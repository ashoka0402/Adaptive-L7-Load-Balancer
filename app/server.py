"""aiohttp application factory and lifecycle."""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from aiohttp import web

from app.admin.routes import setup_admin_routes
from app.app_keys import CONFIG_KEY, REGISTRY_KEY
from app.config import AppConfig
from app.experiments.manager import ExperimentManager
from app.experiments.routes import setup_experiment_routes
from app.health.active import ActiveHealthChecker
from app.health.passive import PassiveHealthMonitor
from app.metrics import prometheus as metrics
from app.models.backend import Backend, BackendRegistry
from app.persistence import SQLiteStore
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
from app.ui.routes import setup_console
from app.ui.telemetry import ConsoleTelemetry

logger = logging.getLogger(__name__)


def build_strategy(name: str) -> LoadBalancingStrategy:
    mapping: dict[str, LoadBalancingStrategy] = {
        "round_robin": RoundRobinStrategy(),
        "weighted_round_robin": WeightedRoundRobinStrategy(),
        "least_connections": LeastConnectionsStrategy(),
        "latency_aware": LatencyAwareStrategy(),
    }
    strategy = mapping.get(name)
    if strategy is None:
        raise ValueError(f"unknown strategy: {name}")
    return strategy


class LoadBalancerApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.registry = BackendRegistry()
        for bc in config.backends:
            self.registry.add(
                Backend(
                    id=bc.id,
                    host=bc.host,
                    port=bc.port,
                    weight=bc.weight,
                    capacity=bc.capacity,
                )
            )
        self.store = None
        if config.persistence.enabled:
            self.store = SQLiteStore(config.persistence.path)
            try:
                self.store.initialize()
            except (OSError, sqlite3.Error):
                logger.exception("persistence unavailable; continuing without experiment history")
                self.store = None
        self.experiments = ExperimentManager(self.store)
        self.console = ConsoleTelemetry()
        self.strategy = build_strategy(config.load_balancing.strategy)
        self.pool = ConnectionPool(config.connection_pool, config.timeouts)
        self.circuit = CircuitBreaker(config.circuit_breaker)
        self.retry_policy = RetryPolicy(config.retry)
        self.passive = PassiveHealthMonitor(config.health_check)
        self.backpressure = BackpressureController(config.backpressure)
        self.health_checker: ActiveHealthChecker | None = None
        self.proxy: ReverseProxy | None = None
        self.app: web.Application | None = None
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._metrics_task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()

    def switch_strategy(self, name: str) -> None:
        from app.config import LoadBalancingConfig

        selected = LoadBalancingConfig.model_validate({"strategy": name}).strategy
        strategy = build_strategy(selected)
        self.strategy = strategy
        self.config.load_balancing.strategy = selected
        if self.proxy is not None:
            self.proxy.strategy = strategy

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
            self.console,
        )

        self.app = web.Application(client_max_size=self.config.request.max_body_size)
        self.app[CONFIG_KEY] = self.config
        self.app[REGISTRY_KEY] = self.registry
        setup_admin_routes(self.app, self.switch_strategy)
        setup_experiment_routes(self.app, self.experiments)
        setup_console(self.app, self.console, self.backpressure)
        self.app.router.add_route("*", "/{path:.*}", self.proxy.handle)

        self.runner = web.AppRunner(self.app, access_log=None, handler_cancellation=True,
                                    auto_decompress=False, max_field_size=self.config.request.max_header_size,
                                    max_line_size=self.config.request.max_header_size)
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
            except TimeoutError:
                continue

    async def stop(self) -> None:
        logger.info("graceful shutdown initiated")
        if self.site:
            await self.site.stop()
        await self.experiments.stop()
        self._shutdown.set()
        if self._metrics_task:
            self._metrics_task.cancel()
            try:
                await self._metrics_task
            except asyncio.CancelledError:
                pass
        if self.health_checker:
            await self.health_checker.stop()
        if self.runner:
            await self.runner.cleanup()
        await self.pool.stop()
        logger.info("shutdown complete")
