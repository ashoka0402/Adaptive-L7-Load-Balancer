"""Bounded, live measurements of completed proxy requests."""
from __future__ import annotations

import math
import time
from collections import Counter, deque


def percentile(samples: list[float], q: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return round(ordered[max(0, math.ceil(len(ordered) * q) - 1)], 1)


class Telemetry:
    def __init__(self) -> None:
        self.total = self.ok = 0
        self.samples: deque[tuple[float, float, bool, str]] = deque(maxlen=20000)
        self.events: deque[dict] = deque(maxlen=60)
        self.series: dict[str, deque] = {
            k: deque(maxlen=120) for k in ("rps", "p50", "p95", "p99", "errors")
        }
        self.last_tick = time.monotonic()
        self.last_total = 0

    def event(self, message: str, kind: str = "info") -> None:
        self.events.append({"msg": message, "kind": kind, "ts": int(time.time() * 1000)})

    def record(self, ms: float, ok: bool, backend: str = "") -> None:
        self.total += 1
        self.ok += int(ok)
        self.samples.append((time.monotonic(), ms, ok, backend))

    def tick(self) -> None:
        now = time.monotonic()
        recent = [x for x in self.samples if x[0] > self.last_tick]
        elapsed = max(now - self.last_tick, 0.001)
        self.series["rps"].append(round((self.total - self.last_total) / elapsed))
        for p in (50, 95, 99):
            self.series[f"p{p}"].append(percentile([x[1] for x in recent], p / 100))
        self.series["errors"].append(
            round(100 * sum(not x[2] for x in recent) / len(recent), 1) if recent else 0
        )
        self.last_total = self.total
        self.last_tick = now

    def shares(self) -> Counter:
        now = time.monotonic()
        return Counter(x[3] for x in self.samples if x[0] >= now - 3 and x[2] and x[3])
