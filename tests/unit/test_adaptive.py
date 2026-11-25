"""Focused tests for Pulse telemetry and adaptive policy publication."""

import random

import pytest

from app.config import AdaptiveConfig
from app.control.controller import AdaptiveController
from app.models.backend import Backend, BackendRegistry
from app.routing.adaptive import AdaptiveStrategy
from app.telemetry.pipeline import TelemetryPipeline


def _controller() -> tuple[BackendRegistry, TelemetryPipeline, AdaptiveController]:
    registry = BackendRegistry()
    registry.add(Backend("healthy", "host", 8001, capacity=20))
    registry.add(Backend("degraded", "host", 8002, capacity=20))
    telemetry = TelemetryPipeline(20)
    config = AdaptiveConfig(slow_start_seconds=0, smoothing=1.0)
    return registry, telemetry, AdaptiveController(registry, telemetry, config)


def test_controller_shifts_traffic_from_degraded_backend() -> None:
    registry, telemetry, controller = _controller()
    for _ in range(10):
        telemetry.record("healthy", 0.010, True)
        telemetry.record("degraded", 0.200, False)
    telemetry.update_external("degraded", queue_depth=15, cpu=0.9, memory=0.8)

    snapshot = controller.publish()

    assert sum(policy.probability for policy in snapshot.policies.values()) == pytest.approx(1)
    assert snapshot.policies["healthy"].probability > snapshot.policies["degraded"].probability
    assert snapshot.policies["degraded"].capacity < registry.get("degraded").capacity
    assert "largest weighted pressure" in snapshot.policies["degraded"].explanation
    assert snapshot.to_dict()["backends"]["healthy"]["metrics"]["latency"] == 0.0
    with pytest.raises(TypeError):
        snapshot.policies["new"] = snapshot.policies["healthy"]


@pytest.mark.asyncio
async def test_strategy_uses_published_probabilities_without_starvation() -> None:
    registry, telemetry, controller = _controller()
    for _ in range(10):
        telemetry.record("healthy", 0.005, True)
        telemetry.record("degraded", 0.500, False)
    controller.publish()
    strategy = AdaptiveStrategy(controller, random.Random(7))

    picks = [(await strategy.select_backend(registry.available())).id for _ in range(500)]

    assert picks.count("healthy") > picks.count("degraded")
    assert picks.count("degraded") > 0


def test_runtime_weights_validate_and_publish_new_version() -> None:
    _, _, controller = _controller()
    first = controller.publish()
    controller.update_weights({"errors": 2.0, "latency": 0.0})
    assert controller.snapshot.version == first.version + 1
    with pytest.raises(ValueError, match="unknown weights"):
        controller.update_weights({"unknown": 1.0})


def test_telemetry_window_and_external_values_are_bounded() -> None:
    telemetry = TelemetryPipeline(10)
    for index in range(20):
        telemetry.record("b1", index / 1000, index % 2 == 0)
    telemetry.update_external("b1", queue_depth=-1, cpu=2.0, memory=-0.5)

    snapshot = telemetry.snapshot("b1")

    assert snapshot.samples == 10
    assert snapshot.error_rate == 0.5
    assert snapshot.queue_depth == 0
    assert snapshot.cpu == 1.0
    assert snapshot.memory == 0.0


def test_final_probabilities_respect_floor_and_movement_bound() -> None:
    _registry, telemetry, controller = _controller()
    first = controller.publish()
    for _ in range(20):
        telemetry.record("healthy", 0.001, True)
        telemetry.record("degraded", 1.0, False)

    second = controller.publish()

    assert sum(policy.probability for policy in second.policies.values()) == pytest.approx(1)
    for backend_id, policy in second.policies.items():
        assert policy.probability >= controller.config.min_probability
        assert abs(policy.probability - first.policies[backend_id].probability) <= (
            controller.config.max_probability_step + 1e-12
        )


def test_external_telemetry_expires() -> None:
    telemetry = TelemetryPipeline(10, external_ttl=0)
    telemetry.update_external("b1", queue_depth=10, cpu=0.8, memory=0.7)

    snapshot = telemetry.snapshot("b1")

    assert snapshot.queue_depth == 0
    assert snapshot.cpu is None
    assert snapshot.memory is None


@pytest.mark.asyncio
async def test_new_backend_remains_eligible_before_policy_refresh() -> None:
    registry, _, controller = _controller()
    controller.publish()
    registry.add(Backend("new", "host", 8003, capacity=20))
    strategy = AdaptiveStrategy(controller, random.Random(7))

    picks = [(await strategy.select_backend(registry.available())).id for _ in range(120)]
    assert "new" in picks
    assert set(picks) == {"healthy", "degraded", "new"}


@pytest.mark.asyncio
async def test_strategy_uses_only_eligible_candidates_with_stale_snapshot() -> None:
    registry, telemetry, controller = _controller()
    for _ in range(20):
        telemetry.record("healthy", 0.01, True)
        telemetry.record("degraded", 0.5, False)
    controller.publish()
    strategy = AdaptiveStrategy(controller, random.Random(11))
    degraded = registry.get("degraded")
    assert degraded is not None

    for _ in range(30):
        assert await strategy.select_backend([degraded]) is degraded
