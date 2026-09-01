"""Least-connections load balancing strategy."""

from __future__ import annotations

from typing import Optional

from app.models.backend import Backend
from app.routing.base import LoadBalancingStrategy


class LeastConnectionsStrategy(LoadBalancingStrategy):
    """
    Select the available backend with the fewest active connections.

    Ties broken by lower average latency, then by id for stability.
    Complexity: O(n).
    """

    async def select_backend(self, backends: list[Backend]) -> Optional[Backend]:
        if not backends:
            return None
        return min(
            backends,
            key=lambda b: (b.active_connections, b.average_latency, b.id),
        )
