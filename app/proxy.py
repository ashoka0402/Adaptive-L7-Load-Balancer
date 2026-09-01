"""HTTP reverse proxy core — request forwarding, retries, streaming."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

import aiohttp
from aiohttp import web

from app.metrics import prometheus as metrics
from app.models.request import RequestContext

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.health.passive import PassiveHealthMonitor
    from app.models.backend import Backend, BackendRegistry
    from app.pool.connection_pool import ConnectionPool
    from app.resilience.backpressure import BackpressureController
    from app.resilience.circuit_breaker import CircuitBreaker
    from app.resilience.retry import RetryPolicy
    from app.routing.base import LoadBalancingStrategy

logger = logging.getLogger(__name__)

# Hop-by-hop headers that must not be forwarded
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
}


class ReverseProxy:
    def __init__(
        self,
        config: "AppConfig",
        registry: "BackendRegistry",
        strategy: "LoadBalancingStrategy",
        pool: "ConnectionPool",
        circuit: "CircuitBreaker",
        retry_policy: "RetryPolicy",
        passive: "PassiveHealthMonitor",
        backpressure: "BackpressureController",
    ) -> None:
        self.config = config
        self.registry = registry
        self.strategy = strategy
        self.pool = pool
        self.circuit = circuit
        self.retry_policy = retry_policy
        self.passive = passive
        self.backpressure = backpressure

    async def handle(self, request: web.Request) -> web.StreamResponse:
        # Metrics path short-circuit
                # Metrics path short-circuit
        if request.path == self.config.metrics.path:
            body, ctype = metrics.metrics_response()
            # aiohttp rejects charset inside content_type kwarg
            media_type = ctype.split(";")[0].strip() if ctype else "text/plain"
            return web.Response(
                body=body,
                headers={"Content-Type": ctype or "text/plain; version=0.0.4"},
            )

        # Backpressure
        if not await self.backpressure.acquire():
            metrics.REJECTED.inc()
            metrics.QUEUE_SIZE.set(self.backpressure.queue_size)
            return web.json_response(
                {"error": "service overloaded"}, status=503
            )

        metrics.ACTIVE_REQUESTS.inc()
        metrics.QUEUE_SIZE.set(self.backpressure.queue_size)
        ctx = RequestContext.create(
            method=request.method,
            path=request.path_qs.split("?", 1)[0],
            query_string=request.query_string,
            headers=dict(request.headers),
        )

        try:
            return await self._proxy(request, ctx)
        finally:
            self.backpressure.release()
            metrics.ACTIVE_REQUESTS.dec()
            metrics.QUEUE_SIZE.set(self.backpressure.queue_size)

    async def _proxy(
        self, request: web.Request, ctx: RequestContext
    ) -> web.StreamResponse:
        body = await request.read()
        if len(body) > self.config.request.max_body_size:
            return web.json_response({"error": "body too large"}, status=413)

        last_error: Optional[Exception] = None
        last_status: Optional[int] = None

        while True:
            backend = await self._select(ctx)
            if backend is None:
                metrics.FAILED_REQUESTS.inc()
                metrics.REQUESTS_TOTAL.labels(
                    method=ctx.method, status="503"
                ).inc()
                return web.json_response(
                    {"error": "no available backends"}, status=503
                )

            ctx.backend_id = backend.id
            backend.active_connections += 1
            metrics.BACKEND_ACTIVE.labels(backend=backend.id).set(
                backend.active_connections
            )

            start = time.monotonic()
            try:
                response = await self._forward(request, ctx, backend, body)
                latency = time.monotonic() - start
                status = response.status

                if status >= 500:
                    self.passive.record_failure(backend, status_code=status)
                    self.circuit.on_failure(backend)
                    metrics.BACKEND_REQUESTS.labels(
                        backend=backend.id, result="error"
                    ).inc()
                    if self.retry_policy.should_retry(ctx, status_code=status):
                        ctx.retry_count += 1
                        metrics.RETRIES.inc()
                        backend.active_connections = max(
                            0, backend.active_connections - 1
                        )
                        continue
                    # Fall through to return error response
                else:
                    self.passive.record_success(backend, latency)
                    self.circuit.on_success(backend)
                    metrics.BACKEND_REQUESTS.labels(
                        backend=backend.id, result="ok"
                    ).inc()

                metrics.BACKEND_LATENCY.labels(backend=backend.id).observe(latency)
                metrics.REQUEST_LATENCY.observe(ctx.elapsed)
                metrics.REQUESTS_TOTAL.labels(
                    method=ctx.method, status=str(status)
                ).inc()

                self._log(ctx, status, latency)
                return response

            except asyncio.TimeoutError:
                latency = time.monotonic() - start
                metrics.TIMEOUTS.inc()
                self.passive.record_failure(backend, is_timeout=True)
                self.circuit.on_failure(backend)
                metrics.BACKEND_REQUESTS.labels(
                    backend=backend.id, result="timeout"
                ).inc()
                last_error = asyncio.TimeoutError("backend timeout")
                if self.retry_policy.should_retry(ctx, is_connection_error=True):
                    ctx.retry_count += 1
                    metrics.RETRIES.inc()
                    continue
                break
            except (aiohttp.ClientError, OSError) as exc:
                latency = time.monotonic() - start
                self.passive.record_failure(backend, is_timeout=False)
                self.circuit.on_failure(backend)
                metrics.BACKEND_REQUESTS.labels(
                    backend=backend.id, result="error"
                ).inc()
                last_error = exc
                if self.retry_policy.should_retry(ctx, is_connection_error=True):
                    ctx.retry_count += 1
                    metrics.RETRIES.inc()
                    continue
                break
            finally:
                backend.active_connections = max(0, backend.active_connections - 1)
                metrics.BACKEND_ACTIVE.labels(backend=backend.id).set(
                    backend.active_connections
                )
                await self.pool.release(backend.id)

        metrics.FAILED_REQUESTS.inc()
        metrics.REQUESTS_TOTAL.labels(method=ctx.method, status="502").inc()
        self._log(ctx, 502, ctx.elapsed)
        return web.json_response(
            {"error": "bad gateway", "detail": str(last_error)}, status=502
        )

    async def _select(self, ctx: RequestContext) -> Optional["Backend"]:
        # Prefer backends that pass circuit check
        candidates = []
        for b in self.registry.available():
            if self.circuit.allow_request(b):
                candidates.append(b)
        if not candidates:
            # Also consider HALF_OPEN that circuit allows
            for b in self.registry.list_all():
                if b.enabled and self.circuit.allow_request(b):
                    candidates.append(b)
        return await self.strategy.select_backend(candidates)

    async def _forward(
        self,
        request: web.Request,
        ctx: RequestContext,
        backend: "Backend",
        body: bytes,
    ) -> web.StreamResponse:
        session = await self.pool.acquire(backend.id)
        url = f"http://{backend.host}:{backend.port}{ctx.path}"
        if ctx.query_string:
            url = f"{url}?{ctx.query_string}"

        headers = self._filter_headers(ctx.headers)
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
            for k, v in backend_resp.headers.items():
                kl = k.lower()
                if kl in HOP_BY_HOP:
                    continue
                if kl == "content-length":
                    continue  # let StreamResponse handle or set later
                resp.headers[k] = v
            resp.headers["x-request-id"] = ctx.request_id
            resp.headers["x-backend-id"] = backend.id

            await resp.prepare(request)

            async for chunk in backend_resp.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
            await resp.write_eof()
            return resp

    def _filter_headers(self, headers: dict[str, str]) -> dict[str, str]:
        out = {}
        for k, v in headers.items():
            kl = k.lower()
            if kl in HOP_BY_HOP:
                continue
            if kl == "host":
                continue
            out[k] = v
        return out

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
            },
        )
