"""Bounded, instance-local client outcomes for the console; no request payloads."""
from __future__ import annotations

import time
from collections import deque
from math import ceil


class ConsoleTelemetry:
    def __init__(self) -> None:
        self.samples: deque[tuple[float, float, bool]] = deque(maxlen=4096)
        self.total = 0
        self.errors = 0

    def record(self, elapsed: float, success: bool, *, now: float | None = None) -> None:
        self.total += 1
        self.errors += int(not success)
        self.samples.append((time.monotonic() if now is None else now, elapsed, success))

    def snapshot(self, *, now: float | None = None) -> dict:
        current = time.monotonic() if now is None else now
        samples = [x for x in self.samples if current - 30 <= x[0] <= current]
        recent = [x for x in samples if x[0] > current - 1]
        latencies = sorted(x[1] for x in samples)
        count = len(samples)
        return {
            "total": self.total, "errors": self.errors, "sample_count": count,
            "window_seconds": 30, "window_capped": len(self.samples) == self.samples.maxlen,
            "recent_completed_rps_lower_bound": len(recent),
            "recent_success_rps_lower_bound": sum(x[2] for x in recent),
            "error_rate": sum(not x[2] for x in samples) / count if count else None,
            **{f"p{p}_ms": latencies[max(0, ceil(p/100*count)-1)]*1000 if count else None
               for p in (50, 95, 99)},
        }
