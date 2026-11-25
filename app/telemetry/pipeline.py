"""Bounded, event-loop-local rolling backend telemetry."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class TelemetrySnapshot:
    latency: float
    error_rate: float
    queue_depth: int
    cpu: float | None
    memory: float | None
    samples: int


@dataclass
class _Window:
    latencies: deque[float]
    outcomes: deque[bool]
    queue_depth: int = 0
    cpu: float | None = None
    memory: float | None = None
    latency_ewma: float = 0.0
    external_updated_at: float = 0.0


class TelemetryPipeline:
    """Records request outcomes in O(1); aggregation is done by the controller."""

    def __init__(self, window_size: int = 100, external_ttl: float = 10.0) -> None:
        self.window_size = window_size
        self.external_ttl = external_ttl
        self._windows: dict[str, _Window] = {}

    def _window(self, backend_id: str) -> _Window:
        return self._windows.setdefault(
            backend_id,
            _Window(deque(maxlen=self.window_size), deque(maxlen=self.window_size)),
        )

    def record(self, backend_id: str, latency: float, success: bool) -> None:
        window = self._window(backend_id)
        observed = max(0.0, latency)
        window.latencies.append(observed)
        window.outcomes.append(success)
        alpha = 0.25
        window.latency_ewma = (
            observed
            if window.latency_ewma <= 0
            else alpha * observed + (1 - alpha) * window.latency_ewma
        )

    def update_external(
        self,
        backend_id: str,
        *,
        queue_depth: int | None = None,
        cpu: float | None = None,
        memory: float | None = None,
        now: float | None = None,
    ) -> None:
        window = self._window(backend_id)
        if queue_depth is not None:
            window.queue_depth = max(0, queue_depth)
        if cpu is not None:
            window.cpu = min(1.0, max(0.0, cpu))
        if memory is not None:
            window.memory = min(1.0, max(0.0, memory))
        window.external_updated_at = time.monotonic() if now is None else now

    def snapshot(
        self, backend_id: str, fallback_latency: float = 0.05, *, now: float | None = None
    ) -> TelemetrySnapshot:
        window = self._windows.get(backend_id)
        if not window:
            return TelemetrySnapshot(fallback_latency, 0.0, 0, None, None, 0)
        samples = len(window.outcomes)
        failures = sum(1 for success in window.outcomes if not success)
        current = time.monotonic() if now is None else now
        external_fresh = current - window.external_updated_at <= self.external_ttl
        return TelemetrySnapshot(
            latency=window.latency_ewma or fallback_latency,
            error_rate=failures / samples if samples else 0.0,
            queue_depth=window.queue_depth if external_fresh else 0,
            cpu=window.cpu if external_fresh else None,
            memory=window.memory if external_fresh else None,
            samples=samples,
        )

    def remove(self, backend_id: str) -> None:
        self._windows.pop(backend_id, None)
