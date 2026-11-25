"""FastAPI entrypoint: local control plane, proxy data plane, SSE and health."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.fastapi_lb.lab import Demo
from app.fastapi_lb.proxy import METHODS, Proxy
from app.fastapi_lb.routing import Backend, Pool, RouteTrie, STRATEGIES
from app.fastapi_lb.telemetry import Telemetry
from app.metrics import prometheus as metrics

LOCAL_HOST = re.compile(r"(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?", re.I)
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
DASHBOARD = Path(__file__).with_name("dashboard.html")


class LoadOptions(BaseModel):
    workers: int = Field(default=24, ge=1, le=48)


class StrategyOptions(BaseModel):
    strategy: Literal[
        "round_robin", "weighted_round_robin", "least_connections",
        "least_latency", "least_loaded", "p2c", "ip_hash", "latency_aware",
    ]


def configured_routes(path: str) -> dict[str, Pool]:
    """Read trusted, local operator configuration; no remote registration API."""
    import yaml

    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if "routes" not in cfg:
        cfg = {"routes": [{
            "prefix": "/", "strategy": cfg.get("load_balancing", {}).get(
                "strategy", "round_robin"
            ),
            "backends": [{
                "id": b["id"], "url": f"http://{b['host']}:{b['port']}",
                "weight": b.get("weight", 1),
            } for b in cfg["backends"]],
        }]}
    routes: dict[str, Pool] = {}
    used: set[str] = set()
    for route in cfg["routes"]:
        prefix = route["prefix"]
        strategy = route.get("strategy", "round_robin")
        if not prefix.startswith("/") or "?" in prefix or "#" in prefix:
            raise ValueError("invalid route prefix")
        if strategy not in STRATEGIES:
            raise ValueError("invalid strategy")
        if prefix in routes:
            raise ValueError("duplicate route prefix")
        backends = []
        for spec in route["backends"]:
            if isinstance(spec, str):
                spec = {"url": spec}
            backend_id = spec.get("id", f"backend-{len(used) + 1}")
            url = spec["url"]
            u = urlsplit(url)
            weight = int(spec.get("weight", 1))
            if (
                not SAFE_ID.fullmatch(backend_id) or backend_id in used
                or u.scheme not in {"http", "https"} or not u.hostname
                or u.username or u.password or u.path not in {"", "/"}
                or u.query or u.fragment or not 1 <= weight <= 10000
                or len(used) >= 100
            ):
                raise ValueError("invalid backend specification")
            used.add(backend_id)
            backends.append(Backend(backend_id, url.rstrip("/"), weight))
        if not backends:
            raise ValueError("empty backend pool")
        routes[prefix] = Pool(backends, strategy)
    if not routes:
        raise ValueError("no configured routes")
    return routes


def create_app(
    config: str | None = None, port: int = 8080, strategy: str | None = None,
) -> FastAPI:
    telemetry = Telemetry()
    routes = configured_routes(config) if config else {"/": Pool([])}
    primary = next(iter(routes.values()))
    if strategy:
        if strategy not in STRATEGIES:
            raise ValueError("unknown strategy")
        primary.strategy = strategy
    trie = RouteTrie()
    for prefix, pool in routes.items():
        trie.insert(prefix, pool)
    demo = Demo(primary, telemetry, f"http://127.0.0.1:{port}/api/test")
    is_demo = config is None
    add_lock = asyncio.Lock()
    proxy: Proxy

    async def health_loop(client: httpx.AsyncClient) -> None:
        async def probe(backend: Backend) -> None:
            previous = backend.alive
            try:
                r = await asyncio.wait_for(
                    client.send(httpx.Request("GET", backend.url + "/health")), timeout=1
                )
                client.cookies.clear()
                backend.alive = r.is_success
            except (httpx.HTTPError, TimeoutError):
                backend.alive = False
            if backend.alive != previous:
                telemetry.event(
                    f"{backend.id}: {'healthy' if backend.alive else 'unreachable'}",
                    "good" if backend.alive else "bad",
                )

        while True:
            await asyncio.gather(*(
                probe(b) for pool in routes.values() for b in pool.backends
            ))
            await asyncio.sleep(1)

    async def tick() -> None:
        breaker_states: dict[str, str] = {}
        while True:
            await asyncio.sleep(.5)
            telemetry.tick()
            for pool in routes.values():
                for b in pool.backends:
                    old = breaker_states.get(b.id)
                    if old is not None and old != b.breaker:
                        telemetry.event(f"{b.id}: breaker {b.breaker}")
                    breaker_states[b.id] = b.breaker
                    metrics.BACKEND_ACTIVE.labels(backend=b.id).set(b.active)
                    metrics.BACKEND_HEALTH.labels(backend=b.id).set(
                        int(b.alive and not b.disabled)
                    )
                    metrics.BACKEND_CIRCUIT.labels(backend=b.id).set(
                        {"closed": 0, "open": 1, "half_open": .5}[b.breaker]
                    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal proxy
        async with httpx.AsyncClient(
            limits=httpx.Limits(max_connections=120, max_keepalive_connections=60),
            timeout=httpx.Timeout(5, connect=2),
            follow_redirects=False, trust_env=False,
        ) as client:
            if is_demo:
                for _ in range(4):
                    await demo.add_backend()
            proxy = Proxy(trie, client, telemetry)
            app.state.proxy = proxy
            tasks = [asyncio.create_task(health_loop(client)), asyncio.create_task(tick())]
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await demo.close()

    app = FastAPI(title="Pulse L7 Load Balancer", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    def local(request: Request) -> None:
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if (
            not LOCAL_HOST.fullmatch(host)
            or (origin is not None and origin != f"{request.url.scheme}://{host}")
            or request.headers.get("sec-fetch-site") == "cross-site"
            or (request.method not in {"GET", "HEAD"}
                and request.headers.get("x-pulse-local") != "1")
        ):
            raise HTTPException(403, "Local management only")

    def snapshot() -> dict:
        shares = telemetry.shares()
        count = sum(shares.values()) or 1
        all_backends = [b for pool in routes.values() for b in pool.backends]
        return {
            "running": demo.running, "arenaRunning": demo.arena_running,
            "arena": demo.arena, "demo": is_demo,
            "strategy": primary.strategy, "workers": demo.workers,
            "elapsed": round(time.monotonic() - demo.started) if demo.running else 0,
            "totals": {"total": telemetry.total, "ok": telemetry.ok,
                       "failed": telemetry.total - telemetry.ok},
            "successPct": round(100 * telemetry.ok / telemetry.total, 1)
            if telemetry.total else 100,
            **{k: values[-1] if values else 0 for k, values in telemetry.series.items()},
            "series": {k: list(v) for k, v in telemetry.series.items()},
            "backends": [{
                "id": b.id, "label": f"Backend {b.id}", "dead": b.disabled,
                "alive": b.alive, "breaker": b.breaker, "active": b.active,
                "latency": round(b.ewma, 1), "served": b.served,
                "share": round(100 * shares[b.id] / count),
            } for b in all_backends],
            "events": list(telemetry.events)[-25:],
        }

    @app.get("/")
    @app.get("/pulse")
    @app.get("/pulse/")
    async def dashboard(request: Request):
        local(request)
        return FileResponse(DASHBOARD, headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
        })

    @app.get("/api/stream")
    async def stream(request: Request):
        local(request)

        async def events():
            while not await request.is_disconnected():
                yield "data: " + json.dumps(snapshot()) + "\n\n"
                await asyncio.sleep(.5)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/admin/console")
    async def console(request: Request):
        local(request)
        return JSONResponse(snapshot(), headers={"Cache-Control": "no-store"})

    @app.post("/api/test/start")
    async def start(request: Request, options: LoadOptions):
        local(request)
        if not is_demo or demo.arena_running:
            raise HTTPException(409, "Load generation requires idle demo mode")
        if not demo.running:
            demo.workers = options.workers
            demo.started = time.monotonic()
            demo.task = asyncio.create_task(demo.load(options.workers))
            telemetry.event(f"Load test started: {options.workers} workers, max 60s", "good")
        return {"ok": True}

    @app.post("/api/test/stop")
    async def stop(request: Request):
        local(request)
        await demo.stop()
        telemetry.event("Load test stopped")
        return {"ok": True}

    @app.post("/api/strategy")
    async def change_strategy(request: Request, options: StrategyOptions):
        local(request)
        if demo.arena_running:
            raise HTTPException(409, "Arena is running")
        primary.strategy = options.strategy
        telemetry.event(f"Strategy changed to {options.strategy}")
        return {"ok": True, "strategy": primary.strategy}

    @app.post("/api/backend/add")
    async def add(request: Request):
        local(request)
        async with add_lock:
            if not is_demo or demo.arena_running:
                raise HTTPException(409, "Adding servers requires demo mode")
            try:
                b = await demo.add_backend()
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "id": b.id}

    @app.post("/api/backend/{backend_id}/toggle")
    async def toggle(request: Request, backend_id: str):
        local(request)
        if not is_demo or demo.arena_running:
            raise HTTPException(409, "Backend toggles require demo mode")
        b = next((b for b in primary.backends if b.id == backend_id), None)
        if b is None:
            raise HTTPException(404, "Unknown backend")
        b.disabled = not b.disabled
        if not b.disabled:
            b.alive, b.open_until, b.failures = True, 0.0, 0
        telemetry.event(f"{b.id}: {'offline' if b.disabled else 'restored'}",
                        "bad" if b.disabled else "good")
        return {"ok": True}

    @app.post("/api/arena")
    async def arena(request: Request, options: LoadOptions):
        local(request)
        if not is_demo or demo.running or demo.arena_running:
            raise HTTPException(409, "Arena requires idle demo mode")
        demo.arena_task = asyncio.create_task(demo.benchmark(options.workers))
        return {"ok": True}

    @app.get("/metrics")
    async def prometheus():
        data, content_type = metrics.metrics_response()
        return Response(data, headers={"Content-Type": content_type})

    @app.api_route("/{path:path}", methods=sorted(METHODS))
    async def forward(request: Request, path: str):
        return await proxy.handle(request)

    return app


app = create_app(os.getenv("PULSE_ROUTES") or None, int(os.getenv("PULSE_PORT", "8080")))
