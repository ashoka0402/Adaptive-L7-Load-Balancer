"""aiohttp application factory and lifecycle."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Optional

from aiohttp import web

from app.admin.routes import setup_admin_routes
from app.config import AppConfig
from app.health.active import ActiveHealthChecker
from app.health.passive import PassiveHealthMonitor
from app.metrics import prometheus as metrics
from app.models.backend import Backend, BackendRegistry
from app.pool.connection_pool import ConnectionPool
from app.proxy import ReverseProxy
from app.resilience.backpressure import BackpressureController
from app.resilience.circuit_breaker import CircuitBreaker
from app.resilience.retry import RetryPolicy
from app.routing.base import LoadBalancingStrategy
from app.routing.latency_aware import LatencyAwareStrategy
from app.routing.least_connections import LeastConnectionsStrategy
from app.routing.round_robin import RoundRobinStrategy
from app.routing.weighted_round_robin import WeightedRoundRobinStrategy

logger = logging.getLogger(__name__)


def build_strategy(name: str) -> LoadBalancingStrategy:
    mapping = {
        "round_robin": RoundRobinStrategy,
        "weighted_round_robin": WeightedRoundRobinStrategy,
        "least_connections": LeastConnectionsStrategy,
        "latency_aware": LatencyAwareStrategy,
    }
    cls = mapping.get(name)
    if cls is None:
        raise ValueError(f"unknown strategy: {name}")
    return cls()


class LoadBalancerApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.registry = BackendRegistry()
        for bc in config.backends:
            self.registry.add(
                Backend(id=bc.id, host=bc.host, port=bc.port, weight=bc.weight)
            )
        self.strategy = build_strategy(config.load_balancing.strategy)
        self.pool = ConnectionPool(config.connection_pool, config.timeouts)
        self.circuit = CircuitBreaker(config.circuit_breaker)
        self.retry_policy = RetryPolicy(config.retry)
        self.passive = PassiveHealthMonitor(config.health_check)
        self.backpressure = BackpressureController(config.backpressure)
        self.health_checker: Optional[ActiveHealthChecker] = None
        self.proxy: Optional[ReverseProxy] = None
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self._metrics_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()

    async def start(self) -> None:
        await self.pool.start()
        self.health_checker = ActiveHealthChecker(
            self.registry, self.config.health_check, self.pool.session
        )
        await self.health_checker.start()

        self.proxy = ReverseProxy(
            self.config,
            self.registry,
            self.strategy,
            self.pool,
            self.circuit,
            self.retry_policy,
            self.passive,
            self.backpressure,
        )

        self.app = web.Application(
            client_max_size=self.config.request.max_body_size
        )
        self.app["config"] = self.config
        self.app["registry"] = self.registry
        setup_admin_routes(self.app)
        self.app.router.add_route("*", "/{path:.*}", self.proxy.handle)

        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner,
            self.config.server.host,
            self.config.server.port,
        )
        await self.site.start()
        logger.info(
            "load balancer listening on %s:%s strategy=%s",
            self.config.server.host,
            self.config.server.port,
            self.config.load_balancing.strategy,
        )

        self._metrics_task = asyncio.create_task(self._metrics_loop())

    async def _metrics_loop(self) -> None:
        while not self._shutdown.is_set():
            metrics.update_backend_gauges(self.registry)
            metrics.QUEUE_SIZE.set(self.backpressure.queue_size)
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                continue

    async def stop(self) -> None:
        logger.info("graceful shutdown initiated")
        self._shutdown.set()
        if self._metrics_task:
            self._metrics_task.cancel()
            try:
                await self._metrics_task
            except asyncio.CancelledError:
                pass
        if self.health_checker:
            await self.health_checker.stop()
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        await self.pool.stop()
        logger.info("shutdown complete")
