"""HTTP reverse proxy core — request forwarding, retries, streaming."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

from app.metrics import prometheus as metrics
from app.models.request import RequestContext
from app.routing.adaptive import AdaptiveStrategy
from app.ui.telemetry import ConsoleTelemetry

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.experiments.live_shadow import LiveShadow
    from app.health.passive import PassiveHealthMonitor
    from app.models.backend import Backend, BackendRegistry
    from app.pool.connection_pool import ConnectionPool
    from app.resilience.backpressure import BackpressureController
    from app.resilience.circuit_breaker import CircuitBreaker
    from app.resilience.retry import RetryPolicy
    from app.routing.base import LoadBalancingStrategy
    from app.telemetry.pipeline import TelemetryPipeline

logger = logging.getLogger(__name__)

# Hop-by-hop headers that must not be forwarded
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
}


class ReverseProxy:
    def __init__(
        self,
        config: AppConfig,
        registry: BackendRegistry,
        strategy: LoadBalancingStrategy,
        pool: ConnectionPool,
        circuit: CircuitBreaker,
        retry_policy: RetryPolicy,
        passive: PassiveHealthMonitor,
        backpressure: BackpressureController,
        telemetry: TelemetryPipeline,
        shadow: LiveShadow | None = None,
        console: ConsoleTelemetry | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.strategy = strategy
        self.pool = pool
        self.circuit = circuit
        self.retry_policy = retry_policy
        self.passive = passive
        self.backpressure = backpressure
        self.telemetry = telemetry
        self.shadow = shadow
        self.console = console if console is not None else ConsoleTelemetry()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        # Metrics path short-circuit
        if self.config.metrics.enabled and request.path == self.config.metrics.path:
            body, ctype = metrics.metrics_response()
            return web.Response(
                body=body,
                headers={"Content-Type": ctype or "text/plain; version=0.0.4"},
            )

        # Backpressure
        started = time.monotonic()
        if not await self.backpressure.acquire():
            metrics.REJECTED.inc()
            metrics.QUEUE_SIZE.set(self.backpressure.queue_size)
            if isinstance(self.strategy, AdaptiveStrategy):
                self.strategy.controller.intelligence.observe_client(time.monotonic() - started, False)
            self.console.record(time.monotonic() - started, False)
            return web.json_response({"error": "service overloaded"}, status=503)

        metrics.ACTIVE_REQUESTS.inc()
        metrics.QUEUE_SIZE.set(self.backpressure.queue_size)
        ctx = RequestContext.create(
            method=request.method,
            path=request.path_qs.split("?", 1)[0],
            query_string=request.query_string,
            headers=dict(request.headers),
        )
        ctx.hop_headers = self._connection_tokens(request.headers.getall("Connection", []))

        success = False
        try:
            if isinstance(self.strategy, AdaptiveStrategy):
                ctx.request_class, ctx.eligible_backend_ids = self.strategy.controller.intelligence.classify(
                    ctx.method, ctx.path, ctx.headers
                )
            response = await self._proxy(request, ctx)
            success = response.status < 500
            if isinstance(self.strategy, AdaptiveStrategy):
                self.strategy.controller.intelligence.observe_client(time.monotonic() - started, response.status < 500)
            return response
        except (asyncio.CancelledError, aiohttp.ClientError, OSError, TimeoutError):
            if isinstance(self.strategy, AdaptiveStrategy):
                self.strategy.controller.intelligence.observe_client(time.monotonic() - started, False)
            raise
        finally:
            self.console.record(time.monotonic() - started, success)
            self.backpressure.release()
            metrics.ACTIVE_REQUESTS.dec()
            metrics.QUEUE_SIZE.set(self.backpressure.queue_size)

    async def _proxy(self, request: web.Request, ctx: RequestContext) -> web.StreamResponse:
        body = await request.read()
        if len(body) > self.config.request.max_body_size:
            return web.json_response({"error": "body too large"}, status=413)

        while True:
            backend = await self._select(ctx)
            if backend is None:
                metrics.FAILED_REQUESTS.inc()
                metrics.REQUESTS_TOTAL.labels(method=ctx.method, status="503").inc()
                if ctx.attempted_backend_ids:
                    return web.json_response({"error": "bad gateway"}, status=502)
                return web.json_response({"error": "no available backends"}, status=503)

            ctx.backend_id = backend.id
            ctx.attempted_backend_ids.add(backend.id)
            backend.active_connections += 1
            metrics.BACKEND_ACTIVE.labels(backend=backend.id).set(backend.active_connections)

            start = time.monotonic()
            try:
                response = await self._forward(request, ctx, backend, body)
                latency = time.monotonic() - start
                status = response.status

                if status >= 500:
                    self.passive.record_failure(backend, status_code=status)
                    self._record(ctx, backend, latency, False)
                    self.circuit.on_failure(backend)
                    metrics.BACKEND_REQUESTS.labels(backend=backend.id, result="error").inc()
                    # The response is already streamed; retrying here would corrupt it.
                else:
                    self.passive.record_success(backend, latency)
                    self._record(ctx, backend, latency, True)
                    self.circuit.on_success(backend)
                    metrics.BACKEND_REQUESTS.labels(backend=backend.id, result="ok").inc()

                metrics.BACKEND_LATENCY.labels(backend=backend.id).observe(latency)
                metrics.REQUEST_LATENCY.observe(ctx.elapsed)
                metrics.REQUESTS_TOTAL.labels(method=ctx.method, status=str(status)).inc()

                self._log(ctx, status, latency)
                return response

            except asyncio.CancelledError:
                self.circuit.abort_request(backend)
                raise
            except TimeoutError:
                latency = time.monotonic() - start
                metrics.TIMEOUTS.inc()
                self.passive.record_failure(backend, is_timeout=True)
                self._record(ctx, backend, latency, False)
                self.circuit.on_failure(backend)
                metrics.BACKEND_REQUESTS.labels(backend=backend.id, result="timeout").inc()
                if ctx.response_started:
                    raise
                if self.retry_policy.should_retry(ctx, is_connection_error=True):
                    ctx.retry_count += 1
                    metrics.RETRIES.inc()
                    continue
                break
            except (aiohttp.ClientError, OSError) as exc:
                latency = time.monotonic() - start
                self.passive.record_failure(backend, is_timeout=False)
                self._record(ctx, backend, latency, False)
                self.circuit.on_failure(backend)
                metrics.BACKEND_REQUESTS.labels(backend=backend.id, result="error").inc()
                logger.warning(
                    "backend request failed backend=%s type=%s", backend.id, type(exc).__name__
                )
                if ctx.response_started:
                    raise
                if self.retry_policy.should_retry(ctx, is_connection_error=True):
                    ctx.retry_count += 1
                    metrics.RETRIES.inc()
                    continue
                break
            finally:
                backend.active_connections = max(0, backend.active_connections - 1)
                metrics.BACKEND_ACTIVE.labels(backend=backend.id).set(backend.active_connections)
                await self.pool.release(backend.id)

        metrics.FAILED_REQUESTS.inc()
        metrics.REQUESTS_TOTAL.labels(method=ctx.method, status="502").inc()
        self._log(ctx, 502, ctx.elapsed)
        return web.json_response({"error": "bad gateway", "request_id": ctx.request_id}, status=502)

    def _record(self, ctx: RequestContext, backend: Backend, latency: float, success: bool) -> None:
        if self.registry.get(backend.id) is not backend:
            return
        self.telemetry.record(backend.id, latency, success)
        if isinstance(self.strategy, AdaptiveStrategy):
            self.strategy.controller.intelligence.observe(
                backend.id, ctx.request_class, latency, success, backend.active_connections
            )

    async def _select(self, ctx: RequestContext) -> Backend | None:
        candidates = [
            backend for backend in self.registry.healthy()
            if self.circuit.can_attempt(backend)
            and (not ctx.eligible_backend_ids or backend.id in ctx.eligible_backend_ids)
        ]
        if ctx.attempted_backend_ids:
            candidates = [b for b in candidates if b.id not in ctx.attempted_backend_ids]
        selected = None
        ctx.routing_reason = ""
        while candidates:
            if isinstance(self.strategy, AdaptiveStrategy):
                candidate = await self.strategy.select_for_class(candidates, ctx.request_class)
            else:
                candidate = await self.strategy.select_backend(candidates)
            if candidate is None:
                break
            if self.circuit.allow_request(candidate):
                selected = candidate
                break
            candidates.remove(candidate)
        if selected is not None and isinstance(self.strategy, AdaptiveStrategy):
            policy = self.strategy.policy_for(selected.id)
            if policy:
                ctx.routing_reason = f"{policy.explanation}; class={ctx.request_class}"
        if selected is not None:
            self.console.decision(selected.id, ctx.routing_reason or self.config.load_balancing.strategy, ctx.request_class)
        if selected is not None and self.shadow is not None:
            self.shadow.observe(selected, candidates)
        return selected

    async def _forward(
        self,
        request: web.Request,
        ctx: RequestContext,
        backend: Backend,
        body: bytes,
    ) -> web.StreamResponse:
        session = await self.pool.acquire(backend.id)
        url = f"http://{backend.host}:{backend.port}{ctx.path}"
        if ctx.query_string:
            url = f"{url}?{ctx.query_string}"

        headers = self._filter_headers(ctx.headers, ctx.hop_headers)
        headers["x-request-id"] = ctx.request_id
        headers["x-forwarded-for"] = request.remote or "unknown"
        headers["x-forwarded-proto"] = request.scheme
        # Host header for backend
        headers["host"] = f"{backend.host}:{backend.port}"

        async with session.request(
            ctx.method,
            url,
            headers=headers,
            data=body if body else None,
            allow_redirects=False,
        ) as backend_resp:
            # Build client response with streaming
            resp = web.StreamResponse(
                status=backend_resp.status,
                reason=backend_resp.reason,
            )
            response_hop_headers = self._connection_tokens(
                backend_resp.headers.getall("Connection", [])
            )
            for k, v in backend_resp.headers.items():
                kl = k.lower()
                if kl in HOP_BY_HOP or kl in response_hop_headers:
                    continue
                if kl == "content-length":
                    continue  # let StreamResponse handle or set later
                resp.headers.add(k, v)
            resp.headers["x-request-id"] = ctx.request_id
            resp.headers["x-backend-id"] = backend.id
            if ctx.routing_reason:
                resp.headers["x-pulse-route"] = ctx.routing_reason

            ctx.response_started = True
            await resp.prepare(request)

            async for chunk in backend_resp.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
            await resp.write_eof()
            return resp

    def _filter_headers(
        self, headers: dict[str, str], connection_tokens: set[str] | None = None
    ) -> dict[str, str]:
        out = {}
        connection_tokens = connection_tokens or self._connection_tokens(
            value for key, value in headers.items() if key.lower() == "connection"
        )
        for k, v in headers.items():
            kl = k.lower()
            if kl in HOP_BY_HOP or kl in connection_tokens:
                continue
            if kl == "host":
                continue
            out[k] = v
        return out

    @staticmethod
    def _connection_tokens(values: Iterable[str]) -> set[str]:
        return {
            token.strip().lower()
            for value in values
            for token in value.split(",")
            if token.strip()
        }

    def _log(self, ctx: RequestContext, status: int, latency: float) -> None:
        logger.info(
            "request completed",
            extra={
                "request_id": ctx.request_id,
                "method": ctx.method,
                "path": ctx.path,
                "backend": ctx.backend_id,
                "status_code": status,
                "latency_ms": round(latency * 1000, 2),
                "retry_count": ctx.retry_count,
                "routing_reason": ctx.routing_reason,
            },
        )
