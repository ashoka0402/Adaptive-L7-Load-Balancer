"""Periodic adaptive controller publishing immutable routing policies."""

from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.config import AdaptiveConfig
from app.control.autopilot import Autopilot
from app.control.intelligence import Intelligence
from app.models.backend import BackendRegistry
from app.telemetry.pipeline import TelemetryPipeline

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackendPolicy:
    backend_id: str
    probability: float
    score: float
    capacity: float
    metrics: Mapping[str, float]
    explanation: str

    def to_dict(self) -> dict:
        return {
            "backend_id": self.backend_id,
            "probability": self.probability,
            "score": self.score,
            "capacity": self.capacity,
            "metrics": dict(self.metrics),
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class RoutingPolicySnapshot:
    version: int
    created_at: float
    policies: Mapping[str, BackendPolicy]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "backends": {key: value.to_dict() for key, value in self.policies.items()},
        }


class AdaptiveController:
    """Computes policy off the request path and swaps one immutable reference."""

    def __init__(
        self,
        registry: BackendRegistry,
        telemetry: TelemetryPipeline,
        config: AdaptiveConfig,
        store=None,
    ) -> None:
        self.registry = registry
        self.telemetry = telemetry
        self.config = config
        self.store = store
        self._weights = config.weights.model_dump()
        self._base_weights = self._weights.copy()
        self.intelligence = Intelligence(config.intelligence)
        self.autopilot = Autopilot(config.intelligence)
        self._last_intelligence_sample = float('-inf')
        self._snapshot = RoutingPolicySnapshot(0, time.time(), MappingProxyType({}))
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def snapshot(self) -> RoutingPolicySnapshot:
        return self._snapshot

    def update_weights(self, values: dict[str, float]) -> None:
        unknown = set(values) - set(self._weights)
        if unknown:
            raise ValueError(f"unknown weights: {', '.join(sorted(unknown))}")
        updated = {**self._weights, **values}
        total = sum(updated.values())
        if (
            any(not math.isfinite(value) or value < 0 for value in updated.values())
            or not math.isfinite(total)
            or total <= 0
        ):
            raise ValueError("weights must be non-negative with a positive total")
        self._weights = updated
        self._base_weights = updated.copy()
        self.autopilot.last_loss = None
        self.autopilot.previous = None
        self.publish()

    async def start(self) -> None:
        self._stop.clear()
        self.publish()
        self._task = asyncio.create_task(self._loop(), name="adaptive-controller")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.config.interval)
            except TimeoutError:
                snapshot = self.publish()
                await self._persist(snapshot)

    async def _persist(self, snapshot: RoutingPolicySnapshot) -> None:
        if self.store is None:
            return
        try:
            await asyncio.to_thread(
                self.store.save_policy, snapshot.version, snapshot.to_dict()
            )
        except (OSError, sqlite3.Error):
            logger.exception("failed to persist routing policy version=%s", snapshot.version)

    @staticmethod
    def _normalize(values: dict[str, float]) -> dict[str, float]:
        if not values:
            return {}
        low, high = min(values.values()), max(values.values())
        if math.isclose(low, high):
            return {key: 0.0 for key in values}
        return {key: (value - low) / (high - low) for key, value in values.items()}

    def _project_probabilities(self, target: dict[str, float]) -> dict[str, float]:
        """Project onto a simplex while preserving configured floors and movement bounds."""
        keys = sorted(target)
        count = len(keys)
        floor = min(self.config.min_probability, 1.0 / count)
        same_pool = set(keys) == set(self._snapshot.policies)
        lower = {}
        upper = {}
        for key in keys:
            previous = self._snapshot.policies.get(key)
            if same_pool and previous is not None:
                lower[key] = max(floor, previous.probability - self.config.max_probability_step)
                upper[key] = min(1.0, previous.probability + self.config.max_probability_step)
            else:
                lower[key] = floor
                upper[key] = 1.0

        projected = {
            key: min(upper[key], max(lower[key], target[key])) for key in keys
        }
        delta = 1.0 - sum(projected.values())
        if delta > 0:
            room = {key: upper[key] - projected[key] for key in keys}
        else:
            room = {key: projected[key] - lower[key] for key in keys}
        total_room = sum(room.values())
        if total_room:
            for key in keys:
                projected[key] += delta * room[key] / total_room
        return projected

    def intelligence_status(self) -> dict:
        return {"enabled": self.intelligence.config.enabled,
                "predictions": self.intelligence.predictions,
                "fingerprints": self.intelligence.fingerprints,
                "autopilot": self.autopilot.to_dict(), "weights": self._weights,
                "policy_version": self.snapshot.version,
                "targets": {
                    "p99_seconds": self.intelligence.config.p99_target_seconds,
                    "error_rate": self.intelligence.config.error_target,
                    "availability": self.intelligence.config.availability_target,
                    "success_rps": self.intelligence.config.throughput_target,
                },
                "slo": self.intelligence.slo(time.monotonic())}

    def configure_autopilot(self, objective: str, enabled: bool, learning: bool) -> None:
        if not self.intelligence.config.enabled:
            raise ValueError("intelligence disabled")
        self.autopilot.configure(objective, enabled, learning)
        if not enabled:
            self._weights = self._base_weights.copy()
        self.publish()

    def publish(self, *, now: float | None = None) -> RoutingPolicySnapshot:
        now = time.monotonic() if now is None else now
        backends = self.registry.available()
        intelligence = self.intelligence
        if intelligence.config.enabled and now - self._last_intelligence_sample >= self.config.interval:
            self._last_intelligence_sample = now
            for backend in self.registry.list_all():
                observed = self.telemetry.snapshot(backend.id, backend.average_latency or 0.05, now=now)
                intelligence.sample(
                    backend.id, now=now, connections=backend.active_connections,
                    queue=observed.queue_depth, capacity=backend.capacity,
                    latency=observed.latency, error_rate=observed.error_rate,
                    available=backend.is_available,
                )
            intelligence.summarize(now, {b.id for b in self.registry.list_all()})
            pressure = max((p["pressure"] for p in intelligence.predictions.values()), default=0.0)
            self._weights = self.autopilot.update(
                now, self._weights, self._base_weights, intelligence.slo(now), pressure
            )
        if not backends:
            snapshot = RoutingPolicySnapshot(
                self._snapshot.version + 1, time.time(), MappingProxyType({})
            )
            self._snapshot = snapshot
            return snapshot

        raw: dict[str, dict[str, float]] = {}
        for backend in backends:
            observed = self.telemetry.snapshot(backend.id, backend.average_latency or 0.05, now=now)
            resource = max(
                value for value in (observed.cpu, observed.memory, 0.0) if value is not None
            )
            health_factor = max(0.10, 1.0 - observed.error_rate) * max(0.25, 1.0 - resource * 0.5)
            capacity = max(1.0, backend.capacity * health_factor)
            raw[backend.id] = {
                "latency": observed.latency,
                "connections": backend.active_connections / capacity,
                "queue": observed.queue_depth / max(capacity, 1.0),
                "errors": observed.error_rate,
                "resources": resource,
                "capacity": capacity,
                "samples": float(observed.samples),
                "age": max(0.0, now - backend.registered_at),
            }

        normalized = {
            metric: self._normalize({key: values[metric] for key, values in raw.items()})
            for metric in self._weights
        }
        total_weight = sum(self._weights.values())
        scores: dict[str, float] = {}
        for backend_id, values in raw.items():
            score = (
                sum(
                    self._weights[metric] * normalized[metric][backend_id]
                    for metric in self._weights
                )
                / total_weight
            )
            if intelligence.config.enabled:
                prediction = intelligence.predictions.get(backend_id, {})
                score += intelligence.config.prediction_weight * prediction.get("risk", 0.0)
            if values["samples"] == 0:
                score = max(score, 0.35)  # neutral cold-start prior
            previous = self._snapshot.policies.get(backend_id)
            if previous:
                score = self.config.smoothing * score + (1 - self.config.smoothing) * previous.score
            scores[backend_id] = score

        quality = {}
        backend_map = {backend.id: backend for backend in backends}
        for backend_id, score in scores.items():
            ramp = 1.0
            if self.config.slow_start_seconds > 0:
                age = raw[backend_id]["age"]
                if intelligence.config.enabled:
                    age = min(age, intelligence.predictions.get(backend_id, {}).get("recovery_age", age))
                ramp = max(0.10, min(1.0, age / self.config.slow_start_seconds))
            quality[backend_id] = math.exp(-5.0 * score) * backend_map[backend_id].weight * ramp

        quality_total = sum(quality.values()) or 1.0
        count = len(quality)
        probabilities = {
            key: (1 - self.config.exploration) * value / quality_total
            + self.config.exploration / count
            for key, value in quality.items()
        }
        probabilities = self._project_probabilities(probabilities)

        policies = {}
        for backend_id, probability in probabilities.items():
            values = raw[backend_id]
            metric_values = MappingProxyType(
                {metric: round(normalized[metric][backend_id], 6) for metric in self._weights}
            )
            dominant = max(
                self._weights, key=lambda metric: self._weights[metric] * metric_values[metric]
            )
            policies[backend_id] = BackendPolicy(
                backend_id=backend_id,
                probability=probability,
                score=scores[backend_id],
                capacity=values["capacity"],
                metrics=metric_values,
                explanation=(
                    f"p={probability:.3f} score={scores[backend_id]:.3f}; "
                    f"largest weighted pressure={dominant}; "
                    f"prediction risk={intelligence.predictions.get(backend_id, {}).get('risk', 0.0):.3f}"
                ),
            )

        snapshot = RoutingPolicySnapshot(
            self._snapshot.version + 1,
            time.time(),
            MappingProxyType(policies),
        )
        self._snapshot = snapshot
        return snapshot
