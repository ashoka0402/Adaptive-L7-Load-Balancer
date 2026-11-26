"""Independent local HTTP traffic controller. Not part of Pulse's data plane."""
from __future__ import annotations

import asyncio
import os
import re
import time
from collections import Counter, deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal

from app.fastapi_lb.telemetry import percentile

LOCAL = re.compile(r"(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?", re.I)
PAGE = Path(__file__).with_name("traffic.html")
SAMPLE_IDS = ("backend-1", "backend-2", "backend-3")


class TrafficOptions(BaseModel):
    rate: int = Field(default=40, ge=1, le=300)
    duration: int = Field(default=60, ge=5, le=120)
    concurrency: int = Field(default=32, ge=1, le=64)
    pattern: Literal["constant", "ramp", "burst"] = "constant"


class WorkOptions(BaseModel):
    backend: Literal["backend-1", "backend-2", "backend-3"]
    iterations: int = Field(default=1000, ge=1000, le=300000)


def guard(request: Request, *, page: bool = False) -> None:
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    navigation = (page and request.method == "GET"
                  and request.headers.get("sec-fetch-mode") == "navigate"
                  and request.headers.get("sec-fetch-dest") == "document")
    if (not LOCAL.fullmatch(host)
            or (origin is not None and origin != f"{request.url.scheme}://{host}")
            or (request.headers.get("sec-fetch-site") == "cross-site" and not navigation)
            or (request.method not in {"GET","HEAD"} and request.headers.get("x-pulse-local") != "1")):
        raise HTTPException(403, "Local traffic controls only")


class Generator:
    def __init__(self, target: str) -> None:
        self.target = target
        self.task: asyncio.Task | None = None
        self.reset()

    def reset(self) -> None:
        self.started = self.ended = 0.0
        self.state = "idle"
        self.options: dict = {}
        self.offered = self.sent = self.completed = self.success = self.dropped = 0
        self.errors = self.cancelled = 0
        self.statuses: Counter = Counter()
        self.backends: Counter = Counter()
        self.latencies: deque[float] = deque(maxlen=36000)
        self.recent: deque[dict] = deque(maxlen=30)
        self.history: deque[dict] = deque(maxlen=120)
        self.last_success = 0
        self.error = ""

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def snapshot(self) -> dict:
        elapsed = max(0.0, (self.ended or time.monotonic())-self.started) if self.started else 0
        ordered = list(self.latencies)
        return {"state": self.state, "running": self.running, "elapsed": round(elapsed,1),
                "options": self.options, "target": self.target,
                "offered": self.offered, "sent": self.sent, "completed": self.completed,
                "success": self.success, "errors": self.errors, "dropped": self.dropped,
                "cancelled": self.cancelled,
                "inflight": self.sent-self.completed, "goodput": round(self.success/max(.001,elapsed),1),
                "p50": percentile(ordered,.5), "p95": percentile(ordered,.95),
                "p99": percentile(ordered,.99), "statuses": dict(self.statuses),
                "backends": dict(self.backends), "recent": list(self.recent),
                "history": list(self.history), "error": self.error}

    async def run(self, options: TrafficOptions) -> None:
        self.reset()
        self.options = options.model_dump()
        self.state = "running"
        self.started = time.monotonic()
        pending: set[asyncio.Task] = set()
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False,
                    limits=httpx.Limits(max_connections=options.concurrency,
                                       max_keepalive_connections=options.concurrency)) as client:
                async def request(index: int) -> None:
                    self.sent += 1
                    t0 = time.monotonic()
                    status = 0
                    backend = None
                    cancelled = False
                    try:
                        response = await client.get(f"{self.target}/api/items/{index%100+1}")
                        status = response.status_code
                        backend = response.headers.get("x-backend")
                    except httpx.HTTPError:
                        pass
                    except asyncio.CancelledError:
                        cancelled = True
                        raise
                    finally:
                        ms = round((time.monotonic()-t0)*1000,2)
                        self.completed += 1
                        self.success += int(200 <= status < 300)
                        self.cancelled += int(cancelled)
                        self.errors += int(not cancelled and not 200 <= status < 300)
                        self.statuses["cancelled" if cancelled else str(status)] += 1
                        if backend and 200 <= status < 300:
                            self.backends[backend] += 1
                        if not cancelled:
                            self.latencies.append(ms)
                        self.recent.append({"id":index,"status":"cancelled" if cancelled else status,
                                            "backend":backend,"ms":ms})

                for second in range(options.duration):
                    rate = options.rate
                    if options.pattern == "ramp":
                        rate = max(1, round(rate*(second+1)/options.duration))
                    elif options.pattern == "burst" and second%10 >= 3:
                        rate = max(1, rate//5)
                    for i in range(rate):
                        due = self.started+second+i/rate
                        await asyncio.sleep(max(0,due-time.monotonic()))
                        self.offered += 1
                        pending = {task for task in pending if not task.done()}
                        if len(pending) >= options.concurrency or time.monotonic()-due > .25:
                            self.dropped += 1
                        else:
                            pending.add(asyncio.create_task(request(self.offered)))
                    await asyncio.sleep(max(0,self.started+second+1-time.monotonic()))
                    self.history.append({"second":second+1,"offered":rate,
                                         "success":self.success-self.last_success})
                    self.last_success = self.success
                await asyncio.gather(*pending)
            self.state = "completed"
        except asyncio.CancelledError:
            self.state = "stopped"
            raise
        except Exception as exc:
            self.state = "failed"
            self.error = type(exc).__name__
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending,return_exceptions=True)
            self.ended = time.monotonic()

    async def stop(self) -> None:
        if self.running:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task


def create_app() -> FastAPI:
    target = os.getenv("PULSE_TRAFFIC_TARGET","http://127.0.0.1:8080").rstrip("/")
    u = urlsplit(target)
    if (u.scheme != "http" or u.hostname not in {"127.0.0.1","load-balancer"}
            or u.username or u.password or u.path or u.query or u.fragment):
        raise ValueError("Traffic target must be the local Pulse service")
    generator = Generator(target)
    token = os.getenv("PULSE_BENCHMARK_TOKEN","")
    complexity = dict.fromkeys(SAMPLE_IDS,1000)
    lock = asyncio.Lock()

    async def set_work(backend: str, iterations: int) -> None:
        if not token:
            raise HTTPException(409,"Sample workload control requires the Compose setup")
        index = SAMPLE_IDS.index(backend)+1
        hostname = backend if u.hostname == "load-balancer" else "127.0.0.1"
        try:
            async with httpx.AsyncClient(timeout=4,trust_env=False) as client:
                response = await client.post(f"http://{hostname}:{8000+index}/__benchmark/complexity",
                    json={"iterations":iterations},headers={"X-Benchmark-Token":token})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(502,"Sample backend control unavailable") from exc
        complexity[backend] = iterations

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await generator.stop()
        for backend,value in complexity.items():
            if value != 1000:
                with suppress(HTTPException):
                    await set_work(backend,1000)

    app = FastAPI(title="Pulse Traffic Control Center",lifespan=lifespan,
                  docs_url=None,redoc_url=None,openapi_url=None)

    @app.get("/")
    async def page(request: Request):
        guard(request,page=True)
        return FileResponse(PAGE,headers={"Cache-Control":"no-store",
            "Content-Security-Policy":"default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options":"nosniff"})

    @app.get("/api/status")
    async def status(request: Request):
        guard(request)
        return dict(generator.snapshot(),complexity=complexity,workControl=bool(token))

    @app.post("/api/start")
    async def start(request: Request, options: TrafficOptions):
        guard(request)
        async with lock:
            if generator.running:
                raise HTTPException(409,"Traffic already running")
            generator.task = asyncio.create_task(generator.run(options))
            await asyncio.sleep(0)
        return {"ok":True}

    @app.post("/api/stop")
    async def stop(request: Request):
        guard(request)
        async with lock:
            await generator.stop()
        return {"ok":True}

    @app.post("/api/work")
    async def work(request: Request, options: WorkOptions):
        guard(request)
        async with lock:
            await set_work(options.backend,options.iterations)
        return {"ok":True}

    return app


app = create_app()
