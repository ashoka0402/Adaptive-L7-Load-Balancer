"""Regression tests for predictive scoring and safe objective feedback."""
import time

import pytest

from app.config import AdaptiveConfig, IntelligenceConfig, RequestClassRule
from app.control.autopilot import KEYS, Autopilot
from app.control.controller import AdaptiveController
from app.control.intelligence import Intelligence
from app.models.backend import Backend, BackendRegistry
from app.routing.adaptive import AdaptiveStrategy
from app.telemetry.pipeline import TelemetryPipeline


def test_forecast_before_saturation():
    engine = Intelligence(IntelligenceConfig(enabled=True))
    for tick, connections in enumerate([2, 4, 6, 8, 10]):
        prediction = engine.sample("b", now=float(tick), connections=connections,
                                   queue=0, capacity=20, latency=.01,
                                   error_rate=0, available=True)
    assert prediction["pressure"] == .5
    assert prediction["forecast"] > .7
    assert prediction["confidence"] > .9
    assert prediction["risk"] > 0


def test_prediction_shifts_traffic():
    registry = BackendRegistry()
    for name in ("a", "b"):
        registry.add(Backend(name, "host", 8000, capacity=20))
    telemetry = TelemetryPipeline()
    controller = AdaptiveController(registry, telemetry, AdaptiveConfig(
        slow_start_seconds=0, smoothing=1, max_probability_step=1,
        intelligence=IntelligenceConfig(enabled=True)
    ))
    for tick, load in enumerate([2, 4, 6, 8, 10]):
        registry.backends["a"].active_connections = load
        registry.backends["b"].active_connections = 10
        for name in ("a", "b"):
            telemetry.record(name, .01, True)
        controller.publish(now=float(tick))
    assert controller.intelligence.predictions["a"]["risk"] > 0
    assert controller.snapshot.policies["a"].probability < controller.snapshot.policies["b"].probability


def test_classification_and_fingerprints():
    cfg = IntelligenceConfig(enabled=True, rules=[RequestClassRule(
        name="compute", path_prefix="/compute", methods=["POST"],
        headers={"x-mode": "batch"}, backend_ids=["a"]
    )])
    engine = Intelligence(cfg)
    assert engine.classify("POST", "/compute/task", {"x-mode": "batch"}) == ("compute", frozenset({"a"}))
    assert engine.classify("GET", "/compute/task", {}) == ("default", frozenset())
    for _ in range(10):
        engine.observe("a", "compute", .02, True, 3)
    engine.summarize(time.monotonic(), {"a"})
    assert engine.fingerprints["a"]["compute"]["p95_ms"] == pytest.approx(20)
    assert engine.class_quality["compute"]["a"] > 0
    engine.remove("a")
    assert not engine.profiles


def test_bounded_feedback_and_rollback():
    cfg = IntelligenceConfig(enabled=True, autopilot=True, policy_cooldown=1)
    pilot = Autopilot(cfg)
    weights = dict(zip(KEYS, (.35, .25, .15, .20, .05)))
    normal = {"samples": 30, "p99_seconds": .02, "error_rate": .02,
              "availability": .98, "success_rps_lower_bound": 100}
    changed = pilot.update(0, weights, weights, normal, .2)
    assert changed["errors"] > weights["errors"]
    assert sum(changed.values()) == pytest.approx(1)
    assert pilot.update(.5, changed, weights, normal, .2) == changed
    bad = dict(normal, error_rate=.5, availability=.5)
    assert pilot.update(2, changed, weights, bad, .2) == pytest.approx(weights)
    assert "rollback" in pilot.reason


@pytest.mark.asyncio
async def test_class_selection_respects_candidates():
    registry = BackendRegistry()
    a, b = Backend("a", "host", 8001), Backend("b", "host", 8002)
    registry.add(a)
    registry.add(b)
    controller = AdaptiveController(registry, TelemetryPipeline(), AdaptiveConfig())
    controller.publish()
    controller.intelligence.class_quality = {"compute": {"a": 1, "b": .25}}
    strategy = AdaptiveStrategy(controller)
    assert await strategy.select_for_class([b], "compute") is b
    assert await strategy.select_for_class([], "compute") is None


def test_recovery_resets_prediction_window():
    engine = Intelligence(IntelligenceConfig(enabled=True))
    for tick in range(5):
        engine.sample("a", now=float(tick), connections=tick * 2, queue=0,
                      capacity=10, latency=.01, error_rate=0, available=True)
    engine.sample("a", now=5, connections=0, queue=0, capacity=10, latency=.1,
                  error_rate=1, available=False)
    recovered = engine.sample("a", now=6, connections=0, queue=0, capacity=10,
                              latency=.01, error_rate=0, available=True)
    assert recovered["recovery_age"] == 0
    assert recovered["confidence"] == 0
    assert recovered["risk"] == 0


def test_learning_does_not_explore_while_slo_is_violated():
    cfg = IntelligenceConfig(enabled=True, autopilot=True, learning=True, policy_cooldown=1)
    pilot = Autopilot(cfg)
    weights = dict(zip(KEYS, (.35, .25, .15, .20, .05)))
    slo = {"samples": 100, "p99_seconds": .02, "error_rate": .10,
           "availability": .90, "success_rps_lower_bound": 50}
    pilot.update(0, weights, weights, slo, .2)
    assert pilot.last_arm is None
    assert "reliability" in pilot.reason
