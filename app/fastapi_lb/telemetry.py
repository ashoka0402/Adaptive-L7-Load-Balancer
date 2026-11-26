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
        self.traces: deque[dict] = deque(maxlen=200)
        self.trace_sequence = 0
        self.outcomes: deque[tuple[float, str, int, float]] = deque(maxlen=20000)
        self.routing_us: deque[float] = deque(maxlen=1024)
        self.upstream_headers_ms: deque[float] = deque(maxlen=1024)
        self.loop_lag_ms = 0.0
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
        self.loop_lag_ms = round(max(0.0, (now-self.last_tick-.5)*1000), 2)
        recent = []
        for x in reversed(self.samples):
            if x[0] <= self.last_tick:
                break
            recent.append(x)
        elapsed = max(now - self.last_tick, 0.001)
        self.series["rps"].append(round((self.total - self.last_total) / elapsed))
        ordered = sorted(x[1] for x in recent)
        for p in (50, 95, 99):
            self.series[f"p{p}"].append(round(ordered[max(0, math.ceil(len(ordered)*p/100)-1)], 1) if ordered else 0)
        self.series["errors"].append(
            round(100 * sum(not x[2] for x in recent) / len(recent), 1) if recent else 0
        )
        self.last_total = self.total
        self.last_tick = now

    def timing_snapshot(self) -> dict:
        return {"routing_p50_us": percentile(list(self.routing_us), .5),
                "routing_p95_us": percentile(list(self.routing_us), .95),
                "upstream_p50_ms": percentile(list(self.upstream_headers_ms), .5),
                "upstream_p95_ms": percentile(list(self.upstream_headers_ms), .95),
                "loop_lag_ms": self.loop_lag_ms,
                "samples": len(self.routing_us)}

    def shares(self) -> Counter:
        now = time.monotonic()
        result: Counter = Counter()
        for x in reversed(self.samples):
            if x[0] < now-3:
                break
            if x[2] and x[3]:
                result[x[3]] += 1
        return result
