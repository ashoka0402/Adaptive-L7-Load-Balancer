"""A small deterministic discrete-event routing simulator."""

from __future__ import annotations

import heapq
import random
import statistics
from collections import Counter
from dataclasses import asdict, dataclass

from app.config import AdaptiveConfig, IntelligenceConfig
from app.control.controller import AdaptiveController
from app.models.backend import Backend, BackendRegistry
from app.routing.adaptive import AdaptiveStrategy
from app.routing.base import LoadBalancingStrategy
from app.routing.latency_aware import LatencyAwareStrategy
from app.telemetry.pipeline import TelemetryPipeline


@dataclass(frozen=True)
class SimulationResult:
    strategy: str
    requests: int
    errors: int
    average_ms: float
    p95_ms: float
    p99_ms: float
    selections: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RoutingSimulation:
    """Exercise normal, burst, degradation, and recovery phases identically."""

    phases = (
        ("normal", 300, 0.0020),
        ("burst", 500, 0.0005),
        ("degraded", 400, 0.0010),
        ("recovery", 300, 0.0020),
    )

    async def run(self, strategy_name: str, *, seed: int = 7) -> SimulationResult:
        registry = BackendRegistry()
        for index in range(3):
            backend = Backend(f"b{index + 1}", "simulated", 8001 + index, capacity=20)
            backend.average_latency = (0.008, 0.014, 0.018)[index]
            backend.successful_requests = 10
            registry.add(backend)
        telemetry = TelemetryPipeline(100)
        controller = AdaptiveController(
            registry,
            telemetry,
            AdaptiveConfig(interval=0.025, slow_start_seconds=0, smoothing=0.5,
                           intelligence=IntelligenceConfig(enabled=strategy_name == "predictive",
                                                           horizon_seconds=0.1)),
        )
        controller.publish(now=0.0)
        if strategy_name in {"adaptive", "predictive"}:
            strategy: LoadBalancingStrategy = AdaptiveStrategy(controller, random.Random(seed))
        elif strategy_name == "latency_aware":
            strategy = LatencyAwareStrategy(capacity_factor=20)
        else:
            raise ValueError("strategy must be adaptive, predictive or latency_aware")

        completions: dict[str, list[float]] = {backend.id: [] for backend in registry.list_all()}
        latencies: list[float] = []
        selections: Counter[str] = Counter()
        errors = 0
        now = 0.0
        request_index = 0

        for phase, requests, spacing in self.phases:
            for _ in range(requests):
                now += spacing
                request_index += 1
                for backend in registry.list_all():
                    queue = completions[backend.id]
                    while queue and queue[0] <= now:
                        heapq.heappop(queue)
                    backend.active_connections = len(queue)

                selected = await strategy.select_backend(registry.available())
                if selected is None:
                    errors += 1
                    continue
                backend = selected
                selections[backend.id] += 1
                base_latency, error_rate = self._profile(phase, backend.id)
                rng = random.Random(seed * 1_000_003 + request_index * 31 + int(backend.id[1:]))
                saturation = backend.active_connections / backend.capacity
                overload = max(0.0, saturation - 0.75)
                latency = base_latency * (1 + 5 * overload * overload) * rng.uniform(0.9, 1.1)
                success = rng.random() >= min(0.95, error_rate + max(0.0, saturation - 1) * 0.2)
                heapq.heappush(completions[backend.id], now + latency)
                backend.active_connections += 1
                telemetry.update_external(
                    backend.id,
                    queue_depth=max(0, backend.active_connections - backend.capacity),
                    cpu=min(1.0, saturation),
                    memory=min(1.0, saturation * 0.8),
                )
                telemetry.record(backend.id, latency, success)
                if success:
                    backend.record_success(latency)
                else:
                    backend.record_failure()
                    errors += 1
                latencies.append(latency)
                if request_index % 25 == 0:
                    controller.publish(now=now)

        ordered = sorted(latencies)
        return SimulationResult(
            strategy=strategy_name,
            requests=request_index,
            errors=errors,
            average_ms=statistics.mean(ordered) * 1000,
            p95_ms=self._percentile(ordered, 0.95),
            p99_ms=self._percentile(ordered, 0.99),
            selections=dict(selections),
        )

    @staticmethod
    def _profile(phase: str, backend_id: str) -> tuple[float, float]:
        if phase == "degraded" and backend_id == "b1":
            # Fast failures expose the original latency-only strategy's blind spot.
            return 0.010, 0.35
        if phase == "recovery" and backend_id == "b1":
            return 0.025, 0.01
        base = {"b1": 0.010, "b2": 0.014, "b3": 0.018}[backend_id]
        return base, 0.01

    @staticmethod
    def _percentile(ordered: list[float], quantile: float) -> float:
        index = min(int((len(ordered) - 1) * quantile), len(ordered) - 1)
        return ordered[index] * 1000
