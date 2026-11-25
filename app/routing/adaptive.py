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
        return await self.select_for_class(backends, "default")

    async def select_for_class(
        self, backends: list[Backend], request_class: str
    ) -> Backend | None:
        if not backends:
            return None
        snapshot = self.controller.snapshot
        eligible = [(backend, snapshot.policies.get(backend.id)) for backend in backends]
        weighted = [(backend, policy) for backend, policy in eligible if policy is not None]
        # Newly added eligible backends may be missing from an older snapshot.
        # Avoid starving them until the next policy publication.
        if len(weighted) != len(backends):
            return self._rng.choice(backends)
        quality = self.controller.intelligence.class_quality.get(request_class, {})
        total = sum(policy.probability * quality.get(backend.id, 1.0) for backend, policy in weighted)
        target = self._rng.random() * total
        cumulative = 0.0
        for backend, policy in weighted:
            cumulative += policy.probability * quality.get(backend.id, 1.0)
            if target <= cumulative:
                return backend
        return weighted[-1][0]

    def policy_for(self, backend_id: str) -> BackendPolicy | None:
        return self.controller.snapshot.policies.get(backend_id)
