"""Round-robin load balancing strategy."""

from __future__ import annotations

from typing import Optional

from app.models.backend import Backend
from app.routing.base import LoadBalancingStrategy


class RoundRobinStrategy(LoadBalancingStrategy):
    """
    Classic round-robin: cycle through healthy/available backends.

    Complexity: O(1) amortized selection.
    Only backends passed in (already filtered to available) participate.
    """

    def __init__(self) -> None:
        self._index: int = 0

    async def select_backend(self, backends: list[Backend]) -> Optional[Backend]:
        if not backends:
            return None
        # Protect against concurrent mutations of the list length
        n = len(backends)
        idx = self._index % n
        self._index = (self._index + 1) % n
        return backends[idx]
