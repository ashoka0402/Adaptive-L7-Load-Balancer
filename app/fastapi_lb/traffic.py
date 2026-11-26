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
from app.fastapi_lb.capacity_control import CapacityController, IDS

LOCAL = re.compile(r"(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?", re.I)
PAGE = Path(__file__).with_name("traffic.html")
SAMPLE_IDS = IDS


class TrafficOptions(BaseModel):
    rate: int = Field(default=40, ge=1, le=300)
    duration: int = Field(default=60, ge=5, le=120)
    concurrency: int = Field(default=32, ge=1, le=64)
    pattern: Literal["constant", "ramp", "burst", "spike", "tsunami", "cascade", "recovery"] = "constant"


class CapacityOptions(BaseModel):
    backend: Literal["backend-1","backend-2","backend-3","backend-4","backend-5","backend-6"]
    enabled: bool = True
    rate: int = Field(default=40,ge=5,le=500)
    concurrency: int = Field(default=8,ge=1,le=64)
    queue: int = Field(default=12,ge=0,le=64)
    crash: bool = False
    cooldown: int = Field(default=10,ge=3,le=60)


class CapacityPreset(BaseModel):
    crash: bool = False


class AutoOptions(BaseModel):
    enabled: bool


class WorkOptions(BaseModel):
    backend: Literal["backend-1","backend-2","backend-3","backend-4","backend-5","backend-6"]
    iterations: int = Field(default=1000, ge=1000, le=300000)


class ChaosOptions(BaseModel):
    backend: Literal["backend-1","backend-2","backend-3","backend-4","backend-5","backend-6"]
    mode: Literal["normal","offline","unavailable","error","flaky","timeout","disconnect"]
    duration: int = Field(default=30, ge=5, le=120)


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
                    elif options.pattern == "spike":
                        rate = rate if options.duration//3 <= second < 2*options.duration//3 else max(1,rate//5)
                    elif options.pattern == "cascade":
                        rate = max(1,round(rate*min(1,.4+.6*second/max(1,options.duration//2))))
                    elif options.pattern == "recovery" and second >= options.duration//2:
                        rate = max(1,rate//8)
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
    chaos_state: dict = {b:{'mode':'unknown','remaining':0} for b in SAMPLE_IDS}
    events: deque[dict] = deque(maxlen=40)

    def origin_url(backend: str) -> str:
        index = IDS.index(backend)+1
        hostname = backend if u.hostname == 'load-balancer' else '127.0.0.1'
        return f'http://{hostname}:{8000+index}'

    capacity = CapacityController(generator,target,token,origin_url)

    async def set_work(backend: str, iterations: int) -> None:
        if not token:
            raise HTTPException(409,"Sample workload control requires the Compose setup")
        try:
            async with httpx.AsyncClient(timeout=4,trust_env=False) as client:
                response = await client.post(origin_url(backend)+"/__benchmark/complexity",
                    json={"iterations":iterations},headers={"X-Benchmark-Token":token})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(502,"Sample backend control unavailable") from exc
        complexity[backend] = iterations

    async def backend_chaos(backend: str, mode: str | None = None,
                            duration: int = 30) -> dict:
        if not token:
            raise HTTPException(409,"Sample chaos controls require the Compose setup")
        try:
            async with httpx.AsyncClient(timeout=3,trust_env=False) as client:
                url = origin_url(backend)+"/__benchmark/chaos"
                headers = {"X-Benchmark-Token":token}
                if mode is None:
                    response = await client.get(url,headers=headers)
                else:
                    response = await client.post(url,headers=headers,
                                                 json={"mode":mode,"duration":duration})
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError,ValueError) as exc:
            chaos_state[backend] = {"mode":"unknown","remaining":0}
            raise HTTPException(502,"Sample backend chaos control unavailable") from exc
        previous = chaos_state[backend]["mode"]
        chaos_state[backend] = result
        if mode is not None or previous not in ("unknown",result["mode"]):
            events.append({"ts":time.time(),"backend":backend,"mode":result["mode"],
                           "source":"operator" if mode is not None else "observed"})
        return result

    async def poll_faults() -> None:
        while True:
            async with lock:
                await asyncio.gather(*(backend_chaos(b) for b in SAMPLE_IDS),
                                     return_exceptions=True)
                await capacity.tick()
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        poller = asyncio.create_task(poll_faults()) if token else None
        try:
            yield
        finally:
            if poller is not None:
                poller.cancel()
                with suppress(asyncio.CancelledError):
                    await poller
            await capacity.stop()
            await generator.stop()
            if token:
                await asyncio.gather(*(backend_chaos(b,"normal") for b in SAMPLE_IDS),
                                     return_exceptions=True)
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
        return dict(generator.snapshot(),complexity=complexity,workControl=bool(token),
                    chaos=chaos_state,chaosEvents=list(events),capacity=capacity.snapshot())

    @app.post("/api/start")
    async def start(request: Request, options: TrafficOptions):
        guard(request)
        async with lock:
            if generator.running or capacity.busy:
                raise HTTPException(409,"Traffic or comparison already running")
            generator.task = asyncio.create_task(generator.run(options))
            await asyncio.sleep(0)
        return {"ok":True}

    @app.post("/api/stop")
    async def stop(request: Request):
        guard(request)
        async with lock:
            await capacity.stop()
            await generator.stop()
        return {"ok":True}

    @app.post("/api/work")
    async def work(request: Request, options: WorkOptions):
        guard(request)
        async with lock:
            if capacity.busy:
                raise HTTPException(409,"Comparison in progress")
            await set_work(options.backend,options.iterations)
        return {"ok":True}

    @app.post("/api/chaos")
    async def chaos(request: Request, options: ChaosOptions):
        guard(request)
        async with lock:
            if capacity.busy:
                raise HTTPException(409,"Comparison in progress")
            result = await backend_chaos(options.backend,options.mode,options.duration)
        return {"ok":True,**result}

    @app.post("/api/chaos/reset")
    async def reset_chaos(request: Request):
        guard(request)
        async with lock:
            if capacity.busy:
                raise HTTPException(409,"Comparison in progress")
            results = await asyncio.gather(*(backend_chaos(b,"normal") for b in SAMPLE_IDS),
                                           return_exceptions=True)
        failed = [b for b,result in zip(SAMPLE_IDS,results) if isinstance(result,BaseException)]
        if failed:
            raise HTTPException(502,"Could not reset: "+", ".join(failed))
        return {"ok":True}

    @app.post("/api/capacity")
    async def capacity_config(request: Request, options: CapacityOptions):
        guard(request)
        async with lock:
            if capacity.busy:
                raise HTTPException(409,"Comparison in progress")
            return await capacity.configure(options.backend,options.model_dump(exclude={"backend"}))

    @app.post("/api/capacity/preset")
    async def capacity_preset(request: Request, options: CapacityPreset):
        guard(request)
        async with lock:
            if capacity.busy or generator.running:
                raise HTTPException(409,"Stop traffic before changing the whole cluster")
            await capacity.preset(options.crash)
        return {"ok":True}

    @app.post("/api/capacity/recover")
    async def recover_capacity(request: Request):
        guard(request)
        async with lock:
            if capacity.busy:
                raise HTTPException(409,"Comparison in progress")
            await capacity.recover()
        return {"ok":True}

    @app.post("/api/capacity/auto")
    async def auto_capacity(request: Request, options: AutoOptions):
        guard(request)
        async with lock:
            if capacity.busy or not capacity.available:
                raise HTTPException(409,"Cluster unavailable or comparison in progress")
            capacity.auto=options.enabled
            capacity.high=capacity.low=0
            capacity.event("Autoscaling enabled" if options.enabled else "Autoscaling disabled")
        return {"ok":True}

    @app.post("/api/capacity/compare")
    async def compare_capacity(request: Request, options: TrafficOptions):
        guard(request)
        async with lock:
            if generator.running or capacity.busy or not capacity.available:
                raise HTTPException(409,"Stop traffic and wait for the sample cluster")
            async def reset_faults(members):
                for b in members:
                    await backend_chaos(b,"normal")
                    await set_work(b,1000)
            capacity.job=asyncio.create_task(capacity.compare(options,reset_faults))
            await asyncio.sleep(0)
        return {"ok":True}

    return app


app = create_app()
