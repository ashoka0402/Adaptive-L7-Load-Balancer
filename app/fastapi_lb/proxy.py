"""FastAPI reverse proxy: fixed admission, pooled HTTP, safe retries."""
from __future__ import annotations

import asyncio
import time

import httpx
from anyio import CancelScope
from fastapi import Request, Response
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.fastapi_lb.routing import RouteTrie
from app.fastapi_lb.telemetry import Telemetry
from app.metrics import prometheus as metrics

HOP = {
    b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
    b"te", b"trailer", b"transfer-encoding", b"upgrade", b"proxy-connection",
}
METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}


def clean_headers(
    raw: list[tuple[bytes, bytes]], request: bool = False
) -> list[tuple[bytes, bytes]]:
    nominated = {
        part.strip().lower()
        for k, v in raw if k.lower() == b"connection"
        for part in v.split(b",")
    }
    blocked = HOP | nominated
    if request:
        blocked |= {
            b"host", b"content-length", b"forwarded", b"x-forwarded-for",
            b"x-forwarded-host", b"x-forwarded-proto", b"x-benchmark-token",
        }
    else:
        blocked |= {b"x-backend", b"x-pulse-routing-us", b"x-pulse-upstream-headers-ms"}
    return [(k.lower(), v) for k, v in raw if k.lower() not in blocked]


class ManagedStream(StreamingResponse):
    """Release the upstream even when a downstream disconnect precedes iteration."""
    def __init__(self, content, status_code, cleanup):
        super().__init__(content, status_code=status_code)
        self.cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                await self.cleanup()


class Proxy:
    def __init__(
        self, routes: RouteTrie, client: httpx.AsyncClient, telemetry: Telemetry,
        max_active: int = 100, max_body: int = 10 * 1024 * 1024,
    ) -> None:
        self.routes = routes
        self.client = client
        self.telemetry = telemetry
        self.max_active = max_active
        self.max_body = max_body
        self.active = 0
        self.retry_tokens = 10.0
        self.policy = {"breaker": True, "retries": True, "pooling": True,
                       "rate_limit": False, "backpressure": True}
        self.rate_tokens = 200.0
        self.rate_last = time.monotonic()
        self.trace_enabled = False
        self.trace_every = 1
        self.request_sequence = 0

    def record(self, request: Request, start: float, status: int, backend: str = "") -> None:
        ms = (time.monotonic() - start) * 1000
        ok = 200 <= status < 400
        self.telemetry.record(ms, ok, backend)
        self.telemetry.outcomes.append((time.monotonic(), backend, status, ms))
        trace = getattr(request.state, "pulse_trace", None)
        if trace is not None:
            trace.update(status=status, elapsed_ms=round(ms, 2), backend=backend)
            self.telemetry.traces.append(trace)
        metrics.REQUESTS_TOTAL.labels(method=request.method, status=str(status)).inc()
        metrics.REQUEST_LATENCY.observe(ms / 1000)

    async def handle(self, request: Request) -> Response:
        start = time.monotonic()
        trace = None
        self.request_sequence += 1
        if self.trace_enabled and (self.request_sequence == 1 or self.request_sequence % self.trace_every == 0):
            self.telemetry.trace_sequence += 1
            trace = {"id": self.telemetry.trace_sequence, "method": request.method,
                     "ts": int(time.time()*1000), "attempts": []}
            request.state.pulse_trace = trace
        pool = self.routes.match(request.url.path)
        if pool is None:
            self.record(request, start, 404)
            return Response("No matching route", 404)
        if self.policy["rate_limit"]:
            now = time.monotonic()
            self.rate_tokens = min(200, self.rate_tokens + (now-self.rate_last)*200)
            self.rate_last = now
            if self.rate_tokens < 1:
                self.record(request, start, 429)
                return Response("Demo rate limit", 429)
            self.rate_tokens -= 1
        # Hard cap remains even when experimental admission is switched off.
        if self.active >= (self.max_active if self.policy["backpressure"] else 160):
            self.record(request, start, 503)
            metrics.REJECTED.inc()
            return Response("Overloaded", 503, headers={"Retry-After": "1"})
        self.active += 1
        metrics.ACTIVE_REQUESTS.inc()
        transferred = False
        try:
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > self.max_body:
                            self.record(request, start, 413)
                            return Response("Request too large", 413)
                        body.extend(chunk)
            except TimeoutError:
                self.record(request, start, 408)
                return Response("Request body timeout", 408)
            self.retry_tokens = min(10.0, self.retry_tokens + 0.1)
            excluded: set[str] = set()
            for attempt in range(2 if self.policy["retries"] and request.method in {"GET", "HEAD"} else 1):
                if attempt:
                    if self.retry_tokens < 1:
                        break
                    self.retry_tokens -= 1
                    metrics.RETRIES.inc()
                decision: dict = {}
                routing_started = time.perf_counter_ns()
                backend = pool.select(request.client.host if request.client else "", excluded,
                                      decision if trace is not None else None)
                routing_us = (time.perf_counter_ns() - routing_started) / 1000
                self.telemetry.routing_us.append(routing_us)
                if trace is not None:
                    decision["routing_us"] = round(routing_us, 2)
                    trace["attempts"].append(decision)
                if backend is None:
                    break
                excluded.add(backend.id)
                sent = time.monotonic()
                outcome: bool | None = None
                response: httpx.Response | None = None
                keep = False
                try:
                    # Construct from a trusted origin plus the raw path/query; never
                    # use an incoming absolute URL or a user supplied destination.
                    raw_path = request.scope.get("raw_path", b"/")
                    query = request.scope.get("query_string", b"")
                    target = httpx.URL(backend.url).copy_with(
                        raw_path=raw_path + (b"?" + query if query else b"")
                    )
                    headers = clean_headers(list(request.headers.raw), request=True)
                    if not self.policy["pooling"]:
                        headers.append((b"connection", b"close"))
                    upstream = httpx.Request(
                        request.method, target, headers=headers, content=bytes(body)
                    )
                    response = await self.client.send(upstream, stream=True)
                    headers_ms = (time.monotonic() - sent) * 1000
                    self.telemetry.upstream_headers_ms.append(headers_ms)
                    self.client.cookies.clear()
                    status = response.status_code
                    outcome = status < 500
                    decision["upstream_status"] = status
                    if backend.version == "v2" and status in {502, 503, 504}:
                        self.telemetry.outcomes.append((time.monotonic(), backend.id, status, (time.monotonic()-sent)*1000))
                    if self.policy["retries"] and status in {502, 503, 504} and request.method in {"GET", "HEAD"}:
                        if attempt == 0 and self.retry_tokens >= 1 and any(
                            b.available and b.id not in excluded for b in pool.backends
                        ):
                            continue
                    upstream_response = response
                    selected = backend

                    completed = False

                    async def stream():
                        nonlocal completed
                        async for data in upstream_response.aiter_raw():
                            yield data
                        completed = True

                    async def cleanup() -> None:
                        successful = completed and status < 500
                        try:
                            await upstream_response.aclose()
                        finally:
                            duration = (time.monotonic() - sent) * 1000
                            selected.finish(headers_ms, successful if completed else None)
                            self.active -= 1
                            metrics.ACTIVE_REQUESTS.dec()
                            metrics.BACKEND_REQUESTS.labels(
                                backend=selected.id, result="success" if successful else "error"
                            ).inc()
                            metrics.BACKEND_LATENCY.labels(backend=selected.id).observe(
                                duration / 1000
                            )
                            self.record(request, start, status if completed else 502, selected.id)

                    result = ManagedStream(stream(), status, cleanup)
                    result.raw_headers = clean_headers(list(response.headers.raw)) + [
                        (b"x-backend", backend.id.encode("ascii")),
                        (b"x-pulse-routing-us", f"{routing_us:.2f}".encode("ascii")),
                        (b"x-pulse-upstream-headers-ms", f"{headers_ms:.2f}".encode("ascii")),
                    ]
                    transferred = keep = True
                    return result
                except (httpx.HTTPError, ValueError):
                    decision["upstream_status"] = "transport error"
                    if backend.version == "v2":
                        self.telemetry.outcomes.append((time.monotonic(), backend.id, 502, (time.monotonic()-sent)*1000))
                    outcome = False
                finally:
                    if not keep:
                        try:
                            if response is not None:
                                with CancelScope(shield=True):
                                    await response.aclose()
                        finally:
                            backend.finish((time.monotonic() - sent) * 1000, outcome)
            self.record(request, start, 503)
            return Response("No available upstream", 503)
        finally:
            if not transferred:
                self.active -= 1
                metrics.ACTIVE_REQUESTS.dec()
