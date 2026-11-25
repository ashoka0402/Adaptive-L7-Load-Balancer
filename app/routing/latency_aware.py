"""EWMA latency-aware routing with a simple outstanding-request penalty."""

from __future__ import annotations

from app.models.backend import Backend
from app.routing.base import LoadBalancingStrategy


class LatencyAwareStrategy(LoadBalancingStrategy):
    """Choose the lowest EWMA latency multiplied by a load penalty.

    Unobserved backends use a conservative default. Ties are stable by backend ID.
    The fixed capacity factor is a tuning constant, not a capacity estimator.
    """

    def __init__(self, capacity_factor: float = 50.0, stale_penalty: float = 1.5) -> None:
        self.capacity_factor = capacity_factor
        self.stale_penalty = stale_penalty

    async def select_backend(self, backends: list[Backend]) -> Backend | None:
        if not backends:
            return None

        def score(backend: Backend) -> tuple[float, str]:
            latency = backend.average_latency or 0.05
            if backend.successful_requests == 0:
                latency *= self.stale_penalty
            return latency * (1 + backend.active_connections / self.capacity_factor), backend.id

        return min(backends, key=score)
