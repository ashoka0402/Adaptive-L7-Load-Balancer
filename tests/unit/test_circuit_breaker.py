"""Circuit breaker unit tests."""

import time

import pytest

from app.config import CircuitBreakerConfig
from app.models.backend import Backend, CircuitState
from app.resilience.circuit_breaker import CircuitBreaker


@pytest.fixture
def cb():
    return CircuitBreaker(
        CircuitBreakerConfig(failure_threshold=3, recovery_timeout=0.1, half_open_requests=1)
    )


@pytest.fixture
def backend():
    return Backend(id="b1", host="h", port=1)


def test_closed_allows(cb, backend):
    assert cb.allow_request(backend) is True
    assert backend.circuit_state == CircuitState.CLOSED


def test_opens_after_threshold(cb, backend):
    for _ in range(3):
        backend.consecutive_failures += 1
        cb.on_failure(backend)
    assert backend.circuit_state == CircuitState.OPEN
    assert cb.allow_request(backend) is False


def test_half_open_after_timeout(cb, backend):
    backend.circuit_state = CircuitState.OPEN
    backend.circuit_opened_at = time.monotonic() - 1.0
    assert cb.allow_request(backend) is True
    assert backend.circuit_state == CircuitState.HALF_OPEN


def test_half_open_success_closes(cb, backend):
    backend.circuit_state = CircuitState.HALF_OPEN
    backend.half_open_in_flight = 1
    cb.on_success(backend)
    assert backend.circuit_state == CircuitState.CLOSED


def test_half_open_failure_reopens(cb, backend):
    backend.circuit_state = CircuitState.HALF_OPEN
    cb.on_failure(backend)
    assert backend.circuit_state == CircuitState.OPEN
