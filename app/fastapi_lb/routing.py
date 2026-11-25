"""Route trie, backend state and strategy selection (ported from codedpool)."""
from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field

STRATEGIES = (
    "round_robin", "weighted_round_robin", "least_connections",
    "least_latency", "least_loaded", "p2c", "ip_hash", "latency_aware",
)


@dataclass
class Backend:
    id: str
    url: str
    weight: int = 1
    active: int = 0
    served: int = 0
    ewma: float = 0.0
    alive: bool = True
    disabled: bool = False
    failures: int = 0
    open_until: float = 0.0
    probing: bool = False
    current_weight: int = 0

    @property
    def breaker(self) -> str:
        if self.open_until > time.monotonic():
            return "open"
        return "half_open" if self.open_until else "closed"

    @property
    def available(self) -> bool:
        return (
            self.alive and not self.disabled and self.breaker != "open"
            and not self.probing
        )

    def begin(self) -> None:
        self.active += 1
        if self.breaker == "half_open":
            self.probing = True

    def finish(self, elapsed_ms: float, success: bool | None) -> None:
        self.active -= 1
        self.probing = False
        if success is None:
            return
        if success:
            self.served += 1
            self.ewma = elapsed_ms if not self.ewma else 0.2 * elapsed_ms + 0.8 * self.ewma
            self.failures = 0
            self.open_until = 0.0
        else:
            self.failures += 1
            if self.failures >= 3 or self.open_until:
                self.open_until = time.monotonic() + 3


@dataclass
class Pool:
    backends: list[Backend]
    strategy: str = "round_robin"
    cursor: int = 0
    selections: int = 0

    def select(self, ip: str = "", excluded: set[str] | None = None) -> Backend | None:
        candidates = [
            b for b in self.backends if b.available and b.id not in (excluded or set())
        ]
        if not candidates:
            return None
        self.selections += 1
        strategy = self.strategy
        if strategy == "weighted_round_robin":
            for b in candidates:
                b.current_weight += b.weight
            chosen = max(candidates, key=lambda b: b.current_weight)
            chosen.current_weight -= sum(b.weight for b in candidates)
        elif strategy == "least_connections":
            chosen = min(candidates, key=lambda b: (b.active, b.id))
        elif strategy == "least_latency":
            chosen = min(candidates, key=lambda b: b.ewma or 0.0)
        elif strategy == "least_loaded":
            chosen = min(candidates, key=lambda b: b.active + b.ewma / 100)
        elif strategy == "p2c":
            chosen = min(
                random.sample(candidates, min(2, len(candidates))),
                key=lambda b: b.active + b.ewma / 100,
            )
        elif strategy == "ip_hash":
            digest = int.from_bytes(hashlib.sha256(ip.encode()).digest()[:8], "big")
            chosen = candidates[digest % len(candidates)]
        elif strategy == "latency_aware":
            # Sample every new backend and periodically explore to avoid starvation.
            unknown = [b for b in candidates if not b.ewma and not b.active]
            if unknown:
                chosen = unknown[0]
            elif self.selections % 20 == 0:
                chosen = candidates[self.cursor % len(candidates)]
                self.cursor += 1
            else:
                chosen = min(
                    candidates, key=lambda b: (b.ewma or 50) * (1 + b.active)
                )
        else:
            chosen = candidates[self.cursor % len(candidates)]
            self.cursor += 1
        chosen.begin()
        return chosen


@dataclass
class Node:
    children: dict[str, Node] = field(default_factory=dict)
    pool: Pool | None = None


class RouteTrie:
    def __init__(self) -> None:
        self.root = Node()

    def insert(self, prefix: str, pool: Pool) -> None:
        node = self.root
        for part in prefix.strip("/").split("/") if prefix.strip("/") else []:
            node = node.children.setdefault(part, Node())
        node.pool = pool

    def match(self, path: str) -> Pool | None:
        node = self.root
        result = node.pool
        for part in path.strip("/").split("/") if path.strip("/") else []:
            child = node.children.get(part)
            if child is None:
                break
            node = child
            if node.pool is not None:
                result = node.pool
        return result
