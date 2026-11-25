"""Hot-path adaptive routing from a precomputed policy snapshot."""

from __future__ import annotations

import random

from app.control.controller import AdaptiveController, BackendPolicy
from app.models.backend import Backend
from app.routing.base import LoadBalancingStrategy


class AdaptiveStrategy(LoadBalancingStrategy):
    def __init__(self, controller: AdaptiveController, rng: random.Random | None = None) -> None:
        self.controller = controller
        self._rng = rng or random.Random()

    async def select_backend(self, backends: list[Backend]) -> Backend | None:
        if not backends:
            return None
        snapshot = self.controller.snapshot
        eligible = [(backend, snapshot.policies.get(backend.id)) for backend in backends]
        weighted = [(backend, policy) for backend, policy in eligible if policy is not None]
        if not weighted:
            return self._rng.choice(backends)
        total = sum(policy.probability for _, policy in weighted)
        target = self._rng.random() * total
        cumulative = 0.0
        for backend, policy in weighted:
            cumulative += policy.probability
            if target <= cumulative:
                return backend
        return weighted[-1][0]

    def policy_for(self, backend_id: str) -> BackendPolicy | None:
        return self.controller.snapshot.policies.get(backend_id)
