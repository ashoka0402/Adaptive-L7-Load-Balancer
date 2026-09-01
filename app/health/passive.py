"""Passive health monitoring based on live traffic outcomes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.models.backend import BackendStatus

if TYPE_CHECKING:
    from app.config import HealthCheckConfig
    from app.models.backend import Backend

logger = logging.getLogger(__name__)


class PassiveHealthMonitor:
    """
    Learns from real request outcomes.

    Connection errors, timeouts, and selected 5xx responses increment
    consecutive_failures and can mark a backend UNHEALTHY.
    """

    def __init__(self, config: "HealthCheckConfig") -> None:
        self.config = config

    def record_success(self, backend: "Backend", latency: float) -> None:
        backend.record_success(latency)
        if backend.status == BackendStatus.UNHEALTHY:
            if backend.consecutive_successes >= self.config.recovery_threshold:
                backend.status = BackendStatus.HEALTHY
                logger.info(
                    "passive recovery: %s → HEALTHY",
                    backend.id,
                )

    def record_failure(
        self,
        backend: "Backend",
        *,
        is_timeout: bool = False,
        status_code: int | None = None,
    ) -> None:
        backend.record_failure()
        # Only treat connection-level and selected 5xx as health signals
        serious = is_timeout or (
            status_code is not None and status_code >= 500
        )
        if serious and backend.consecutive_failures >= self.config.failure_threshold:
            if backend.status != BackendStatus.UNHEALTHY:
                backend.status = BackendStatus.UNHEALTHY
                logger.warning(
                    "passive: %s → UNHEALTHY (timeout=%s status=%s)",
                    backend.id,
                    is_timeout,
                    status_code,
                )
