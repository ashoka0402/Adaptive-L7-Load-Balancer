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
    draining: bool = False
    breaker_enabled: bool = True
    version: str = "v1"
    fault: str = "normal"
    fault_count: int = 0
    assigned: int = 0
    last_selected: float = 0.0
    health_path: str | None = "/health"

    @property
    def breaker(self) -> str:
        if not self.breaker_enabled:
            return "closed"
        if self.open_until > time.monotonic():
            return "open"
        return "half_open" if self.open_until else "closed"

    @property
    def available(self) -> bool:
        return (
            self.alive and not self.disabled and not self.draining and self.breaker != "open"
            and not self.probing
        )

    def begin(self) -> None:
        self.active += 1
        self.assigned += 1
        self.last_selected = time.monotonic()
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
            if self.breaker_enabled and (self.failures >= 3 or self.open_until):
                self.open_until = time.monotonic() + 3


@dataclass
class Pool:
    backends: list[Backend]
    strategy: str = "round_robin"
    cursor: int = 0
    selections: int = 0
    canary_percent: int = 0
    canary_counter: int = 0

    def select(self, ip: str = "", excluded: set[str] | None = None,
               trace: dict | None = None) -> Backend | None:
        candidates = [
            b for b in self.backends if b.available and b.id not in (excluded or set())
        ]
        if trace is not None:
            trace["strategy"] = self.strategy
            trace["candidates"] = [
                {"id": b.id, "active": b.active, "ewma_ms": round(b.ewma, 2),
                 "weight": b.weight, "eligible": b in candidates,
                 "reason": "eligible" if b in candidates else (
                     "retry exclusion" if b.id in (excluded or set()) else
                     "draining" if b.draining else "disabled" if b.disabled else
                     "unhealthy" if not b.alive else "circuit or probe"
                 )} for b in self.backends
            ]
        stable = [b for b in candidates if b.version == "v1"]
        canary = [b for b in candidates if b.version == "v2"]
        # Deterministic percentage split, only on initial attempts.
        self.canary_counter += int(not excluded)
        use_canary = (not excluded and canary and self.canary_percent > 0
                      and ((self.canary_counter - 1) * self.canary_percent % 100
                           + self.canary_percent >= 100))
        candidates = canary if use_canary else stable
        if trace is not None:
            trace["eligible_group"] = [b.id for b in candidates]
            trace["reason"] = "canary allocation" if use_canary else "stable pool"
        if not candidates:
            return None
        self.selections += 1
        strategy = self.strategy
        reason = strategy
        if strategy == "weighted_round_robin":
            for b in candidates:
                b.current_weight += b.weight
            if trace is not None:
                trace["scores"] = {b.id: b.current_weight for b in candidates}
            chosen = max(candidates, key=lambda b: b.current_weight)
            chosen.current_weight -= sum(b.weight for b in candidates)
        elif strategy == "least_connections":
            chosen = min(candidates, key=lambda b: (b.active, b.id))
        elif strategy == "least_latency":
            chosen = min(candidates, key=lambda b: b.ewma or 0.0)
        elif strategy == "least_loaded":
            chosen = min(candidates, key=lambda b: b.active + b.ewma / 100)
        elif strategy == "p2c":
            sampled = random.sample(candidates, min(2, len(candidates)))
            if trace is not None:
                trace["sampled"] = [b.id for b in sampled]
            chosen = min(sampled, key=lambda b: b.active + b.ewma / 100)
        elif strategy == "ip_hash":
            digest = int.from_bytes(hashlib.sha256(ip.encode()).digest()[:8], "big")
            chosen = candidates[digest % len(candidates)]
        elif strategy == "latency_aware":
            # Sample every new backend and periodically explore to avoid starvation.
            unknown = [b for b in candidates if not b.ewma and not b.active]
            if unknown:
                reason = "cold-start exploration"
                chosen = unknown[0]
            elif stale := [b for b in candidates if b.last_selected and
                           not b.active and time.monotonic()-b.last_selected >= 2]:
                reason = "stale latency exploration"
                chosen = min(stale, key=lambda b: b.last_selected)
            elif self.selections % 20 == 0:
                reason = "periodic exploration"
                chosen = min(candidates, key=lambda b: b.last_selected)
            else:
                chosen = min(
                    candidates, key=lambda b: (b.ewma or 50) * (1 + b.active)
                )
        else:
            chosen = candidates[self.cursor % len(candidates)]
            self.cursor += 1
        if trace is not None:
            trace["decision"] = reason
            trace["selected"] = chosen.id
            if strategy in {"least_connections", "least_latency", "least_loaded", "p2c",
                            "latency_aware"}:
                trace["scores"] = {
                    b.id: round(
                        b.active if strategy == "least_connections" else
                        b.ewma if strategy == "least_latency" else
                        (b.ewma or 50) * (1 + b.active) if strategy == "latency_aware"
                        else b.active + b.ewma / 100, 2
                    ) for b in candidates
                }
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
