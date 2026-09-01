"""Health monitoring unit tests."""

from app.config import HealthCheckConfig
from app.health.passive import PassiveHealthMonitor
from app.models.backend import Backend, BackendStatus


def test_passive_marks_unhealthy():
    cfg = HealthCheckConfig(failure_threshold=3, recovery_threshold=2)
    mon = PassiveHealthMonitor(cfg)
    b = Backend(id="b1", host="h", port=1)
    for _ in range(3):
        mon.record_failure(b, is_timeout=True)
    assert b.status == BackendStatus.UNHEALTHY


def test_passive_recovery():
    cfg = HealthCheckConfig(failure_threshold=3, recovery_threshold=2)
    mon = PassiveHealthMonitor(cfg)
    b = Backend(id="b1", host="h", port=1, status=BackendStatus.UNHEALTHY)
    mon.record_success(b, 0.01)
    mon.record_success(b, 0.01)
    assert b.status == BackendStatus.HEALTHY


def test_ewma_update():
    b = Backend(id="b1", host="h", port=1)
    b.update_latency(0.1, alpha=0.5)
    assert abs(b.average_latency - 0.1) < 1e-9
    b.update_latency(0.2, alpha=0.5)
    assert abs(b.average_latency - 0.15) < 1e-9
