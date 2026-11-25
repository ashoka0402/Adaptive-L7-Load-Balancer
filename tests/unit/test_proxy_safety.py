import textwrap
import time

from app.config import CircuitBreakerConfig, load_config
from app.models.backend import Backend, CircuitState
from app.proxy import ReverseProxy
from app.resilience.circuit_breaker import CircuitBreaker


def test_connection_nominated_headers_are_not_forwarded():
    proxy = ReverseProxy.__new__(ReverseProxy)

    filtered = proxy._filter_headers(
        {"Connection": "keep-alive, X-Internal", "X-Internal": "secret", "X-Safe": "yes"}
    )

    assert filtered == {"X-Safe": "yes"}


def test_duplicate_connection_header_tokens_are_unioned():
    proxy = ReverseProxy.__new__(ReverseProxy)

    tokens = proxy._connection_tokens(["keep-alive, X-First", "X-Second"])

    assert tokens == {"keep-alive", "x-first", "x-second"}


def test_circuit_eligibility_does_not_reserve_unselected_probe():
    circuit = CircuitBreaker(CircuitBreakerConfig(recovery_timeout=0, half_open_requests=1))
    backend = Backend("b1", "host", 8001)
    backend.circuit_state = CircuitState.OPEN
    backend.circuit_opened_at = time.monotonic() - 1

    assert circuit.can_attempt(backend)
    assert backend.circuit_state == CircuitState.OPEN
    assert backend.half_open_in_flight == 0
    assert circuit.allow_request(backend)
    assert backend.half_open_in_flight == 1


def test_load_config_needs_no_token_and_defaults_to_loopback(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent("""
            backends:
              - id: b1
                host: localhost
                port: 8001
            """),
        encoding="utf-8",
    )
    assert load_config(path).server.host == "127.0.0.1"
