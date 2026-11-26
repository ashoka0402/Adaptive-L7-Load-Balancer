"""Real loopback demo backends, load generation and comparative benchmarks."""
from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import sys
import time
from contextlib import suppress

from app.fastapi_lb.lab_origin import FAULTS, serve_origin
from app.fastapi_lb.routing import Backend, Pool, STRATEGIES
from app.fastapi_lb.telemetry import Telemetry


class ProcessBackend(Backend):
    """Propagate local lab controls to an independently running origin."""
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        shared = self.__dict__.get("shared")
        if shared is not None and name in {"fault","disabled","fault_count"}:
            with shared.get_lock():
                if name == "fault":
                    shared[0] = FAULTS.index(value)
                    shared[2] += 1
                elif name == "disabled":
                    shared[1] = int(value)
                elif value == 0:
                    shared[2] += 1


async def stop_process(process) -> None:
    if process.is_alive():
        process.terminate()
        await asyncio.to_thread(process.join, 3)
    if process.is_alive():
        process.kill()
        await asyncio.to_thread(process.join, 3)
    else:
        await asyncio.to_thread(process.join)
    process.close()


async def external_workload(target: str, count: int, workers: int,
                            rate: int = 0, duration: float = 0) -> dict:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.fastapi_lb.load_worker", "--target", target,
        "--count", str(count), "--workers", str(workers), "--rate", str(rate),
        "--duration", str(duration), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, error = await process.communicate()
        if process.returncode:
            raise RuntimeError("External load generator failed: "+error.decode(errors="replace")[-300:])
        return json.loads(output)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class Demo:
    def __init__(self, pool: Pool, telemetry: Telemetry, target: str) -> None:
        self.pool = pool
        self.telemetry = telemetry
        self.target = target
        self.runners: dict = {}
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
        backend = ProcessBackend(chr(65 + index) if index < 26 else f"N{index}",
                          "", weight=index % 3 + 1, version=version,
                          breaker_enabled=self.breaker_enabled)
        delay = (4, 12, 22, 45, 8, 16)[index % 6]

        context = mp.get_context("spawn")
        backend.shared = context.Array("i", [0,0,0])
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=serve_origin,
                                  args=(backend.id, delay, backend.shared, send),
                                  daemon=True)
        process.start()
        send.close()
        try:
            if not await asyncio.to_thread(receive.poll, 10):
                raise RuntimeError("Lab origin startup timed out")
            port = receive.recv()
        except BaseException:
            await stop_process(process)
            raise
        finally:
            receive.close()
        backend.url = f"http://127.0.0.1:{port}"
        self.runners[backend.id] = process
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
            await stop_process(runner)
        self.telemetry.event(f"Backend {backend.id} drained and removed")
        return True

    async def load(self, workers: int, duration: float = 60.0) -> None:
        await external_workload(self.target,100000,workers,duration=duration)

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError, RuntimeError):
                await self.task
            self.task = None

    async def benchmark(self, workers: int) -> None:
        previous = self.pool.strategy
        self.arena = []
        try:
            for strategy in STRATEGIES:
                self.pool.strategy = strategy
                self.pool.cursor = self.pool.selections = 0
                for b in self.pool.backends:
                    b.current_weight = 0
                    b.ewma = 0
                self.telemetry.event(f"Arena: {strategy}")
                result = await external_workload(self.target,240,workers)
                self.arena.append(dict(result,strategy=strategy))
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
            await stop_process(runner)
        self.runners.clear()
