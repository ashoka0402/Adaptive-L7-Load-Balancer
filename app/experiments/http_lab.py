"""Real HTTP benchmark on freshly created loopback listeners only."""
from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import AsyncExitStack

import aiohttp
from aiohttp import web

from app.config import (
    AppConfig,
    BackendConfig,
    BackpressureConfig,
    HealthCheckConfig,
    LoadBalancingConfig,
    PersistenceConfig,
    ServerConfig,
)
from app.experiments.lab import ADAPTIVE, Node, Progress, adaptive_config, service, summarize
from app.experiments.spec import ExperimentSpec, trace
from app.models.backend import Backend


async def listener(app: web.Application, stack: AsyncExitStack) -> int:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    stack.push_async_callback(runner.cleanup)
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None and site._server.sockets
    return site._server.sockets[0].getsockname()[1]


async def run_http(spec: ExperimentSpec, algorithm: str, seed: int, progress: Progress) -> dict:
    from app.server import LoadBalancerApp

    cpu_started = time.process_time()
    rows: list[dict] = []
    timeline: list[dict] = []
    nodes = {n.id: Node(n, Backend(n.id, "127.0.0.1", 1), n.capacity,
                       error_rate=n.error_rate) for n in spec.nodes}
    in_flight = {n.id: 0 for n in spec.nodes}

    def handler(node: Node):
        async def serve(request: web.Request) -> web.Response:
            if request.path == "/health":
                return web.Response(status=503 if node.down else 200)
            if node.down or in_flight[node.spec.id] >= node.capacity:
                return web.Response(status=503)
            rid = int(request.headers["x-lab-id"])
            cls = request.path.strip("/")
            in_flight[node.spec.id] += 1
            try:
                delay, ok = service(node, seed, rid, cls)
                await asyncio.sleep(delay)
                return web.Response(status=200 if ok and not node.down else 503, text="lab")
            finally:
                in_flight[node.spec.id] -= 1
        return serve

    async with AsyncExitStack() as stack:
        configs = []
        for n in spec.nodes:
            app = web.Application()
            app.router.add_get("/{path:.*}", handler(nodes[n.id]))
            port = await listener(app, stack)
            configs.append(BackendConfig(id=n.id, host="127.0.0.1", port=port,
                                         capacity=n.capacity, weight=n.weight))
        config = AppConfig(
            server=ServerConfig(host="127.0.0.1", port=0, admin_token=secrets.token_hex(24)),
            backends=configs, persistence=PersistenceConfig(enabled=False),
            load_balancing=LoadBalancingConfig(strategy="adaptive" if algorithm in ADAPTIVE else algorithm),
            adaptive=adaptive_config(algorithm,seed,spec),
            health_check=HealthCheckConfig(interval=.2, timeout=.5, failure_threshold=1),
            backpressure=BackpressureConfig(max_concurrent_requests=spec.concurrency, max_queue_size=spec.concurrency),
        )
        pulse = LoadBalancerApp(config)
        await pulse.start()
        stack.push_async_callback(pulse.stop)
        assert pulse.site is not None and pulse.site._server is not None and pulse.site._server.sockets
        port = pulse.site._server.sockets[0].getsockname()[1]
        timeout = aiohttp.ClientTimeout(total=spec.timeout_ms/1000)
        connector = aiohttp.TCPConnector(limit=spec.concurrency)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            start = time.monotonic()
            tasks: set[asyncio.Task] = set()
            arrivals = trace(spec,seed)

            async def send(rid: int, at: float, cls: str) -> None:
                begun = time.monotonic()
                status, bid = "error", None
                try:
                    async with session.get(f"http://127.0.0.1:{port}/{cls}",
                                           headers={"x-lab-id":str(rid)}, allow_redirects=False) as response:
                        await response.read()
                        status = "ok" if response.status < 400 else "error"
                        bid = response.headers.get("x-backend-id")
                except TimeoutError:
                    status = "timeout"
                except (aiohttp.ClientError,OSError):
                    pass
                rows.append({"id":rid,"arrival":at,"completed":time.monotonic()-start,
                             "class":cls,"backend":bid,"status":status,
                             "latency_ms":(time.monotonic()-begun)*1000})

            async def inject():
                for fault in sorted(spec.faults,key=lambda f:f.at):
                    await asyncio.sleep(max(0,start+fault.at-time.monotonic()))
                    nodes[fault.backend].fault(fault)

            fault_task = asyncio.create_task(inject())
            try:
                for rid,(at,cls) in enumerate(arrivals):
                    await asyncio.sleep(max(0,start+at-time.monotonic()))
                    tasks = {task for task in tasks if not task.done()}
                    if len(tasks)>=spec.concurrency:
                        rows.append({"id":rid,"arrival":at,"completed":time.monotonic()-start,
                                     "class":cls,"backend":None,"status":"dropped","latency_ms":0})
                    else:
                        tasks.add(asyncio.create_task(send(rid,at,cls)))
                    if rid % max(1,spec.rps//5)==0:
                        frame={"time":time.monotonic()-start,"completed":len(rows),"offered":len(arrivals),
                               "nodes":{bid:{"active":in_flight[bid],"down":n.down,"capacity":n.capacity}
                                        for bid,n in nodes.items()}}
                        timeline.append(frame)
                        await progress(frame)
                await asyncio.gather(*tasks)
                await fault_task
                await asyncio.sleep(max(0,start+spec.duration+spec.warmup-time.monotonic()))
            finally:
                fault_task.cancel()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(fault_task,*tasks,return_exceptions=True)
            elapsed=max(.000001,time.monotonic()-start-spec.warmup)
    rows.sort(key=lambda r:r["id"])
    summary = summarize(rows,spec,elapsed)
    summary["process_cpu_seconds"] = time.process_time()-cpu_started
    return {"algorithm":algorithm,"seed":seed,"mode":"http","summary":summary,
            "timeline":timeline,"requests":rows,"shadow":{},
            "note":"Measured local HTTP; synthetic application faults; no external targets. CPU includes whole process, setup and teardown."}
