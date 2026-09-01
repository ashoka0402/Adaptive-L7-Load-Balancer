"""Per-backend circuit breaker."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.models.backend import CircuitState

if TYPE_CHECKING:
    from app.config import CircuitBreakerConfig
    from app.models.backend import Backend

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    Classic three-state circuit breaker.

    CLOSED  --failures--> OPEN --cooldown--> HALF_OPEN
    HALF_OPEN --success--> CLOSED
    HALF_OPEN --failure--> OPEN
    """

    def __init__(self, config: "CircuitBreakerConfig") -> None:
        self.config = config

    def allow_request(self, backend: "Backend") -> bool:
        """Return True if a request may be sent to this backend."""
        state = backend.circuit_state

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            if time.monotonic() - backend.circuit_opened_at >= self.config.recovery_timeout:
                backend.circuit_state = CircuitState.HALF_OPEN
                backend.half_open_in_flight = 0
                logger.info("circuit %s OPEN → HALF_OPEN", backend.id)
                return self._try_half_open(backend)
            return False

        # HALF_OPEN
        return self._try_half_open(backend)

    def _try_half_open(self, backend: "Backend") -> bool:
        if backend.half_open_in_flight < self.config.half_open_requests:
            backend.half_open_in_flight += 1
            return True
        return False

    def on_success(self, backend: "Backend") -> None:
        if backend.circuit_state == CircuitState.HALF_OPEN:
            backend.circuit_state = CircuitState.CLOSED
            backend.half_open_in_flight = 0
            backend.consecutive_failures = 0
            logger.info("circuit %s HALF_OPEN → CLOSED", backend.id)
        # In CLOSED we just keep counting successes via health monitors

    def on_failure(self, backend: "Backend") -> None:
        if backend.circuit_state == CircuitState.HALF_OPEN:
            backend.circuit_state = CircuitState.OPEN
            backend.circuit_opened_at = time.monotonic()
            backend.half_open_in_flight = 0
            logger.warning("circuit %s HALF_OPEN → OPEN", backend.id)
            return

        if backend.circuit_state == CircuitState.CLOSED:
            if backend.consecutive_failures >= self.config.failure_threshold:
                backend.circuit_state = CircuitState.OPEN
                backend.circuit_opened_at = time.monotonic()
                logger.warning(
                    "circuit %s CLOSED → OPEN after %d failures",
                    backend.id,
                    backend.consecutive_failures,
                )
