"""Active health checking of backends."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import aiohttp

from app.models.backend import BackendStatus, CircuitState

if TYPE_CHECKING:
    from app.config import HealthCheckConfig
    from app.models.backend import BackendRegistry

logger = logging.getLogger(__name__)


class ActiveHealthChecker:
    """Periodically probes GET /health on every backend."""

    def __init__(
        self,
        registry: "BackendRegistry",
        config: "HealthCheckConfig",
        session: aiohttp.ClientSession,
    ) -> None:
        self.registry = registry
        self.config = config
        self.session = session
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="health-checker")

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
                await self._check_all()
            except Exception:
                logger.exception("health check loop error")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.config.interval
                )
                break
            except asyncio.TimeoutError:
                continue

    async def _check_all(self) -> None:
        backends = self.registry.list_all()
        if not backends:
            return
        await asyncio.gather(
            *(self._check_one(b) for b in backends),
            return_exceptions=True,
        )

    async def _check_one(self, backend) -> None:
        url = f"http://{backend.host}:{backend.port}{self.config.path}"
        start = time.monotonic()
        try:
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=self.config.timeout),
            ) as resp:
                latency = time.monotonic() - start
                backend.last_health_check = time.time()
                if 200 <= resp.status < 300:
                    self._on_success(backend, latency)
                else:
                    self._on_failure(backend)
        except Exception as exc:
            backend.last_health_check = time.time()
            logger.debug("health check failed for %s: %s", backend.id, exc)
            self._on_failure(backend)

    def _on_success(self, backend, latency: float) -> None:
        backend.consecutive_successes += 1
        backend.consecutive_failures = 0
        backend.update_latency(latency)

        if backend.status == BackendStatus.UNHEALTHY:
            if backend.consecutive_successes >= self.config.recovery_threshold:
                backend.status = BackendStatus.HEALTHY
                logger.info(
                    "backend %s recovered → HEALTHY after %d successes",
                    backend.id,
                    backend.consecutive_successes,
                )
        # Circuit breaker recovery is handled separately; health is orthogonal

    def _on_failure(self, backend) -> None:
        backend.consecutive_failures += 1
        backend.consecutive_successes = 0
        if backend.consecutive_failures >= self.config.failure_threshold:
            if backend.status != BackendStatus.UNHEALTHY:
                backend.status = BackendStatus.UNHEALTHY
                logger.warning(
                    "backend %s marked UNHEALTHY after %d failures",
                    backend.id,
                    backend.consecutive_failures,
                )
