"""Weighted round-robin load balancing (smooth WRR)."""

from __future__ import annotations

from typing import Optional

from app.models.backend import Backend
from app.routing.base import LoadBalancingStrategy


class WeightedRoundRobinStrategy(LoadBalancingStrategy):
    """
    Smooth weighted round-robin (Nginx-style).

    Maintains current weights and selects the backend with the highest
    current weight, then adjusts. Avoids simple list duplication.

    Expected distribution approximates weight proportions over time.
    Complexity: O(n) per selection where n = number of backends (small).
    """

    def __init__(self) -> None:
        # backend_id -> current_weight
        self._current_weights: dict[str, int] = {}

    async def select_backend(self, backends: list[Backend]) -> Optional[Backend]:
        if not backends:
            return None

        total_weight = 0
        selected: Optional[Backend] = None
        max_current = -1

        for b in backends:
            cw = self._current_weights.get(b.id, 0) + b.weight
            self._current_weights[b.id] = cw
            total_weight += b.weight
            if cw > max_current:
                max_current = cw
                selected = b

        if selected is not None:
            self._current_weights[selected.id] = (
                self._current_weights.get(selected.id, 0) - total_weight
            )

        # Clean up stale entries for removed backends
        active_ids = {b.id for b in backends}
        stale = [k for k in self._current_weights if k not in active_ids]
        for k in stale:
            del self._current_weights[k]

        return selected
