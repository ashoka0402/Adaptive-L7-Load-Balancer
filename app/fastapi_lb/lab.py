"""Real loopback demo backends, load generation and comparative benchmarks."""
from __future__ import annotations

import asyncio
import time
from contextlib import suppress

import httpx
from aiohttp import web

from app.fastapi_lb.routing import Backend, Pool, STRATEGIES
from app.fastapi_lb.telemetry import Telemetry, percentile


class Demo:
    def __init__(self, pool: Pool, telemetry: Telemetry, target: str) -> None:
        self.pool = pool
        self.telemetry = telemetry
        self.target = target
        self.runners: dict[str, web.AppRunner] = {}
        self.next_id = 0
        self.breaker_enabled = True
        self.task: asyncio.Task | None = None
        self.arena_task: asyncio.Task | None = None
        self.arena: list[dict] = []
        self.workers = 24
        self.started = 0.0

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def arena_running(self) -> bool:
        return self.arena_task is not None and not self.arena_task.done()

    async def add_backend(self, version: str = "v1") -> Backend:
        if len(self.pool.backends) >= 6:
            raise ValueError("maximum six demo backends")
        index = self.next_id
        self.next_id += 1
        backend = Backend(chr(65 + index) if index < 26 else f"N{index}",
                          "", weight=index % 3 + 1, version=version,
                          breaker_enabled=self.breaker_enabled)
        delay = (4, 12, 22, 45, 8, 16)[index % 6]

        async def handle(request: web.Request) -> web.Response:
            if request.path == "/health":
                return web.Response(status=503 if backend.disabled else 200)
            if backend.disabled:
                return web.Response(status=503, text="Demo backend offline")
            backend.fault_count += 1
            fault = backend.fault
            await asyncio.sleep((delay + (250 if fault in {"slow", "timeout"} else 0)) / 1000)
            if fault == "timeout":
                await asyncio.sleep(5.5)
            if fault == "error" or (fault == "flaky" and backend.fault_count % 3 == 0):
                return web.Response(status=500, text="Injected failure")
            if fault == "unavailable":
                return web.Response(status=503, text="Injected unavailable")
            return web.Response(text=backend.id, headers={"X-Backend": backend.id})

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handle)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        try:
            await site.start()
            port = runner.addresses[0][1]
        except BaseException:
            await runner.cleanup()
            raise
        backend.url = f"http://127.0.0.1:{port}"
        self.runners[backend.id] = runner
        self.pool.backends.append(backend)
        self.telemetry.event(f"Backend {backend.id} added ({delay} ms base delay)", "good")
        return backend

    async def remove_backend(self, backend: Backend) -> bool:
        backend.draining = True
        deadline = time.monotonic() + 8
        while backend.active and time.monotonic() < deadline:
            await asyncio.sleep(.1)
        if backend.active:
            backend.draining = False
            return False
        if backend in self.pool.backends:
            self.pool.backends.remove(backend)
        runner = self.runners.pop(backend.id, None)
        if runner:
            await runner.cleanup()
        self.telemetry.event(f"Backend {backend.id} drained and removed")
        return True

    async def load(self, workers: int, duration: float = 60.0) -> None:
        deadline = time.monotonic() + duration
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            async def worker() -> None:
                while time.monotonic() < deadline:
                    try:
                        await client.get(self.target)
                    except httpx.HTTPError:
                        pass
                    # Bound the maximum request rate and allow other coroutines to run.
                    await asyncio.sleep(0.01)
            await asyncio.gather(*(worker() for _ in range(workers)))

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def benchmark(self, workers: int) -> None:
        previous = self.pool.strategy
        self.arena = []
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                for strategy in STRATEGIES:
                    self.pool.strategy = strategy
                    self.pool.cursor = self.pool.selections = 0
                    for b in self.pool.backends:
                        b.current_weight = 0
                        b.ewma = 0
                    self.telemetry.event(f"Arena: {strategy}")
                    counter = 0
                    durations: list[float] = []
                    successes = 0
                    started = time.monotonic()

                    async def worker() -> None:
                        nonlocal counter, successes
                        while counter < 240:
                            counter += 1
                            t0 = time.monotonic()
                            try:
                                response = await client.get(self.target)
                                successes += int(response.is_success)
                            except httpx.HTTPError:
                                pass
                            durations.append((time.monotonic() - t0) * 1000)

                    await asyncio.gather(*(worker() for _ in range(workers)))
                    elapsed = max(time.monotonic() - started, 0.001)
                    self.arena.append({
                        "strategy": strategy, "rps": round(len(durations) / elapsed),
                        "p95": percentile(durations, .95),
                        "successPct": round(100 * successes / len(durations), 1),
                    })
                    await asyncio.sleep(.5)
        finally:
            self.pool.strategy = previous
            self.telemetry.event("Arena finished", "good")

    async def close(self) -> None:
        await self.stop()
        if self.arena_task is not None:
            self.arena_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.arena_task
        for runner in self.runners.values():
            await runner.cleanup()
