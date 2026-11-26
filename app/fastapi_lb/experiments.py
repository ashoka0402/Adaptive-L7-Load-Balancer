"""Bounded local experiments, scaling, canary rollout and replay."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import suppress

import httpx

from app.fastapi_lb.lab import Demo
from app.fastapi_lb.proxy import Proxy
from app.fastapi_lb.routing import STRATEGIES
from app.fastapi_lb.telemetry import percentile

SCENARIOS = ("normal", "slow", "outage", "overload")
POLICIES = ("breaker", "retries", "pooling", "rate_limit", "backpressure")


class Experiments:
    def __init__(self, demo: Demo, proxy: Proxy, lock: asyncio.Lock) -> None:
        self.demo, self.proxy, self.lock = demo, proxy, lock
        self.pool, self.telemetry = demo.pool, demo.telemetry
        self.job: asyncio.Task | None = None
        self.mode = ""
        self.results: list[dict] = []
        self.winner: str | None = None
        self.state = "idle"
        self.scaling = False
        self.scale_streak = 0
        self.low_streak = 0
        self.last_scale = 0.0
        self.canary_id: str | None = None
        self.canary_started = 0.0
        self.canary_status = "idle"
        self.canary_guard = True
        self.canary_auto = False
        self.recording = False
        self.frames: deque[dict] = deque(maxlen=240)
        self.incident_started = 0.0

    @property
    def busy(self) -> bool:
        return self.job is not None and not self.job.done()

    def set_policy(self, policy: dict) -> None:
        self.proxy.policy.update(policy)
        self.demo.breaker_enabled = self.proxy.policy["breaker"]
        for b in self.pool.backends:
            b.breaker_enabled = self.demo.breaker_enabled
            if not b.breaker_enabled:
                b.open_until = 0
                b.probing = False
                b.failures = 0

    def record_start(self) -> None:
        self.frames.clear()
        self.recording = True
        self.incident_started = time.monotonic()
        self.telemetry.event("Incident recording started")

    def state_view(self) -> dict:
        return {"mode": self.mode, "state": self.state, "busy": self.busy,
                "results": self.results, "winner": self.winner,
                "policy": self.proxy.policy, "scaling": self.scaling,
                "canary": {"id": self.canary_id, "percent": self.pool.canary_percent,
                           "status": self.canary_status, "guard": self.canary_guard,
                           "auto": self.canary_auto},
                "recording": self.recording, "frames": len(self.frames)}

    async def workload(self, count: int, workers: int, rate: int = 0) -> dict:
        """Open-loop paced arrivals with a bounded in-flight cap; no external target."""
        started = time.monotonic()
        latency: list[float] = []
        successes = 0
        rejected = 0
        dropped = 0
        next_id = 0
        async with httpx.AsyncClient(timeout=7, trust_env=False,
                                     limits=httpx.Limits(max_connections=workers)) as client:
            async def request() -> None:
                nonlocal successes, rejected
                t = time.monotonic()
                try:
                    response = await client.get(self.demo.target)
                    successes += int(response.is_success)
                    rejected += int(response.status_code in (429, 503))
                except httpx.HTTPError:
                    pass
                latency.append((time.monotonic()-t)*1000)

            if rate:
                pending: set[asyncio.Task] = set()
                try:
                    for i in range(count):
                        await asyncio.sleep(max(0, started + i/rate-time.monotonic()))
                        pending = {task for task in pending if not task.done()}
                        if len(pending) >= workers:
                            dropped += 1
                        else:
                            pending.add(asyncio.create_task(request()))
                    await asyncio.gather(*pending)
                finally:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
            else:
                async def worker() -> None:
                    nonlocal next_id
                    while next_id < count:
                        next_id += 1
                        await request()
                await asyncio.gather(*(worker() for _ in range(workers)))
        elapsed = max(.001, time.monotonic()-started)
        return {"offered": count, "completed": len(latency), "success": successes,
                "rps": round(successes/elapsed, 1), "p95": percentile(latency,.95),
                "p99": percentile(latency,.99), "successPct": round(100*successes/count,1),
                "rejected": rejected, "generatorDropped": dropped,
                "elapsed": round(elapsed,2)}

    def configure_scenario(self, scenario: str) -> None:
        for b in self.pool.backends:
            b.fault, b.disabled, b.alive = "normal", False, True
            b.ewma, b.current_weight, b.open_until, b.failures = 0, 0, 0, 0
            b.fault_count = 0
        self.pool.cursor = self.pool.selections = 0
        if self.pool.backends:
            if scenario == "slow":
                self.pool.backends[0].fault = "slow"
            elif scenario == "outage":
                self.pool.backends[0].disabled = True
            elif scenario == "overload":
                for b in self.pool.backends:
                    b.fault = "slow"

    async def run(self, mode: str, scenario: str, workers: int) -> None:
        original_strategy = self.pool.strategy
        original_policy = dict(self.proxy.policy)
        originals = {b.id: (b.fault,b.disabled) for b in self.pool.backends}
        self.mode, self.state, self.results, self.winner = mode, "running", [], None
        self.record_start()
        try:
            if mode == "breaking":
                self.configure_scenario(scenario)
                for rate in (50, 100, 200, 400, 800):
                    result = await self.workload(rate*2, min(160, max(48,workers)), rate)
                    result["offeredRps"] = rate
                    result["saturated"] = (result["successPct"] < 99
                                           or result["p95"] > 200
                                           or result["rps"] < rate*.85)
                    self.results.append(result)
                safe = [r for r in self.results if not r["saturated"]]
                self.winner = f'{safe[-1]["offeredRps"]} req/s' if safe else "Below first step"
            elif mode == "arena":
                for strategy in STRATEGIES:
                    self.configure_scenario(scenario)
                    self.pool.strategy = strategy
                    result = await self.workload(240, workers)
                    self.results.append(dict(result, strategy=strategy, scenario=scenario))
                self.winner = min(self.results, key=lambda r: (
                    -r["successPct"], r["p95"], -r["rps"]))["strategy"]
            elif mode == "resilience":
                # Compare one mechanism at a time against a common baseline.
                baseline = {"breaker": True, "retries": True, "pooling": True,
                            "rate_limit": True, "backpressure": True}
                for name in ("all enabled", *POLICIES):
                    self.configure_scenario(scenario)
                    if scenario == "outage":
                        self.pool.backends[0].disabled = False
                        self.pool.backends[0].fault = "unavailable"
                    self.set_policy(dict(baseline))
                    if name != "all enabled":
                        self.set_policy({name: False})
                    self.results.append(dict(await self.workload(480,workers,800),
                                             strategy=name, scenario=scenario))
            self.state = "completed"
        except asyncio.CancelledError:
            self.state = "cancelled"
            raise
        except Exception as exc:
            self.state = "failed"
            self.telemetry.event(f"Experiment failed: {type(exc).__name__}", "bad")
        finally:
            self.pool.strategy = original_strategy
            self.set_policy(original_policy)
            for b in self.pool.backends:
                if b.id in originals:
                    b.fault,b.disabled = originals[b.id]
                    b.alive = not b.disabled
                    b.open_until = 0
                    b.failures = 0
            self.recording = False
            self.telemetry.event(f"{mode}: {self.state}")

    async def canary(self, percent: int, faulty: bool, auto: bool = False) -> None:
        async with self.lock:
            if self.busy or self.demo.arena_running or self.scaling:
                raise ValueError("Stop experiments and autoscaling before rollout")
            if self.canary_id is None:
                b = await self.demo.add_backend("v2")
                self.canary_id = b.id
            else:
                b = next(b for b in self.pool.backends if b.id == self.canary_id)
            b.fault = "error" if faulty else "normal"
            b.fault_count = 0
            b.alive = True
            self.canary_auto = auto
            b.disabled = False
            b.open_until = 0
            b.failures = 0
            self.canary_started = time.monotonic()
            self.pool.canary_percent = percent
            self.canary_status = "rolling out" if percent else "paused"
            self.telemetry.event(f"Canary v2: {percent}% allocation")

    async def tick(self) -> None:
        now = time.monotonic()
        series = self.telemetry.series
        rps = series["rps"][-1] if series["rps"] else 0
        p95 = series["p95"][-1] if series["p95"] else 0
        if self.recording:
            self.frames.append({
                "t": round(now-self.incident_started,1), "rps": rps, "p95": p95,
                "errors": series["errors"][-1] if series["errors"] else 0,
                "strategy": self.pool.strategy,
                "backends": [{"id":b.id,"alive":b.alive and not b.disabled,
                              "breaker":b.breaker,"active":b.active,"fault":b.fault}
                             for b in self.pool.backends],
                "events": list(self.telemetry.events)[-5:],
            })
            if len(self.frames) == self.frames.maxlen:
                self.recording = False
        if self.canary_id and self.pool.canary_percent and self.canary_guard:
            samples = [(status,ms) for t,b,status,ms in self.telemetry.outcomes
                       if t >= self.canary_started and b == self.canary_id]
            node = next((b for b in self.pool.backends if b.id == self.canary_id), None)
            breaker_open = node is not None and node.breaker == "open"
            if breaker_open or len(samples) >= 20:
                errors = sum(status >= 500 for status,ms in samples)/max(1,len(samples))
                if breaker_open or errors > .05 or percentile([ms for status,ms in samples],.95) > 200:
                    self.pool.canary_percent = 0
                    self.canary_status = "rolled back"
                    self.canary_auto = False
                    self.telemetry.event("Canary rollback: error/latency threshold exceeded","bad")
                elif self.canary_auto and now-self.canary_started >= 3:
                    step = next((p for p in (10,25,50,75,100)
                                 if p > self.pool.canary_percent), None)
                    if step is not None:
                        self.pool.canary_percent = step
                        self.canary_started = now
                        self.telemetry.event(f"Canary promoted to {step}%","good")
                    else:
                        self.canary_auto = False
                        self.canary_status = "promoted"
        if not self.scaling or self.busy or self.demo.arena_running or self.pool.canary_percent:
            return
        stable = [b for b in self.pool.backends if b.version == "v1" and not b.disabled]
        self.scale_streak = self.scale_streak+1 if rps >= 150 or p95 > 120 else 0
        self.low_streak = self.low_streak+1 if rps < 40 and p95 < 60 else 0
        if now-self.last_scale < 5:
            return
        async with self.lock:
            if not self.scaling or self.busy or self.demo.arena_running or self.pool.canary_percent:
                return
            if self.scale_streak >= 3 and len(self.pool.backends) < 6:
                await self.demo.add_backend()
                self.last_scale = now
                self.scale_streak = 0
                self.telemetry.event("Autoscaler: scale out","good")
            elif self.low_streak >= 10 and len(stable) > 2:
                if await self.demo.remove_backend(stable[-1]):
                    self.last_scale = now
                    self.low_streak = 0
                    self.telemetry.event("Autoscaler: scale in")

    async def close(self) -> None:
        if self.job is not None and not self.job.done():
            self.job.cancel()
            with suppress(asyncio.CancelledError):
                await self.job
