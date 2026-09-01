"""Latency-aware adaptive routing with EWMA and load awareness."""

from __future__ import annotations

import math
from typing import Optional

from app.models.backend import Backend
from app.routing.base import LoadBalancingStrategy


class LatencyAwareStrategy(LoadBalancingStrategy):
    """
    Adaptive latency-aware routing.

    Score for each backend:
        score = latency_ewma * (1 + active_connections / capacity_factor)

    Lower score is better. A small exploration term prevents permanent
    starvation of slightly slower but healthy backends.

    Tradeoffs:
    - Pure lowest-latency can overload the fastest backend.
    - Combining with active connections provides natural backpressure.
    - EWMA smooths noise; alpha controls responsiveness (default 0.3).
    - Stale latency (no recent samples) is treated as higher cost.
    """

    def __init__(
        self,
        capacity_factor: float = 50.0,
        exploration: float = 0.05,
        stale_penalty: float = 1.5,
    ) -> None:
        self.capacity_factor = capacity_factor
        self.exploration = exploration
        self.stale_penalty = stale_penalty

    async def select_backend(self, backends: list[Backend]) -> Optional[Backend]:
        if not backends:
            return None
        if len(backends) == 1:
            return backends[0]

        best: Optional[Backend] = None
        best_score = math.inf

        for b in backends:
            latency = b.average_latency if b.average_latency > 0 else 0.05
            # Penalize backends with no successful traffic yet slightly less aggressively
            if b.successful_requests == 0:
                latency *= self.stale_penalty

            load_factor = 1.0 + (b.active_connections / self.capacity_factor)
            score = latency * load_factor

            # Tiny deterministic exploration based on id hash to avoid pure sticky
            score += self.exploration * (hash(b.id) % 100) / 10000.0

            if score < best_score:
                best_score = score
                best = b

        return best
