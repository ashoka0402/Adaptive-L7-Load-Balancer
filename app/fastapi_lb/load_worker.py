"""Bounded HTTP traffic generator; invoked in a separate Python process."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from urllib.parse import urlsplit

import httpx

from app.fastapi_lb.telemetry import percentile


async def workload(target: str, count: int, workers: int, rate: int = 0,
                   duration: float = 0) -> dict:
    if urlsplit(target).hostname != "127.0.0.1":
        raise ValueError("Laboratory traffic is restricted to loopback")
    if not 1 <= workers <= 160 or not 1 <= count <= 100000 or not 0 <= rate <= 800:
        raise ValueError("Invalid workload bounds")
    if not 0 <= duration <= 60:
        raise ValueError("Invalid duration")
    started = time.monotonic()
    latency: list[float] = []
    successes = rejected = dropped = next_id = 0
    async with httpx.AsyncClient(timeout=7, trust_env=False,
                                 limits=httpx.Limits(max_connections=workers,
                                                     max_keepalive_connections=workers)) as client:
        async def request() -> None:
            nonlocal successes, rejected
            t = time.monotonic()
            try:
                response = await client.get(target)
                successes += int(response.is_success)
                rejected += int(response.status_code in (429, 503))
            except httpx.HTTPError:
                pass
            latency.append((time.monotonic()-t)*1000)

        if rate:
            pending: set[asyncio.Task] = set()
            try:
                for i in range(count):
                    await asyncio.sleep(max(0, started+i/rate-time.monotonic()))
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
            async def run() -> None:
                nonlocal next_id
                while next_id < count and (not duration or time.monotonic()-started < duration):
                    next_id += 1
                    await request()
                    if duration:
                        await asyncio.sleep(.01)
            await asyncio.gather(*(run() for _ in range(workers)))
    elapsed = max(.001, time.monotonic()-started)
    offered = count if rate else next_id
    return {"offered": offered, "completed": len(latency), "success": successes,
            "rps": round(successes/elapsed, 1), "p95": percentile(latency,.95),
            "p99": percentile(latency,.99),
            "successPct": round(100*successes/max(1,offered),1),
            "rejected": rejected, "generatorDropped": dropped, "elapsed": round(elapsed,2)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True)
    p.add_argument("--count", type=int, default=240)
    p.add_argument("--workers", type=int, default=24)
    p.add_argument("--rate", type=int, default=0)
    p.add_argument("--duration", type=float, default=0)
    a = p.parse_args()
    print(json.dumps(asyncio.run(workload(a.target,a.count,a.workers,a.rate,a.duration))))


if __name__ == "__main__":
    main()
