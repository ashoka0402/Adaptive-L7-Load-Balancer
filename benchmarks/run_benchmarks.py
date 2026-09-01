"""Simple benchmark runner using concurrent HTTP requests."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from collections import Counter

import aiohttp


async def worker(
    session: aiohttp.ClientSession,
    url: str,
    results: list[float],
    errors: list[int],
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        start = time.perf_counter()
        try:
            async with session.get(url) as resp:
                await resp.read()
                latency = time.perf_counter() - start
                results.append(latency)
                if resp.status >= 400:
                    errors.append(resp.status)
        except Exception:
            errors.append(0)


async def run(
    url: str,
    concurrency: int,
    duration: float,
) -> dict:
    results: list[float] = []
    errors: list[int] = []
    stop = asyncio.Event()
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            asyncio.create_task(worker(session, url, results, errors, stop))
            for _ in range(concurrency)
        ]
        await asyncio.sleep(duration)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    if not results:
        return {"rps": 0, "error": "no results"}

    results.sort()
    n = len(results)
    total_time = duration
    return {
        "requests": n,
        "rps": n / total_time,
        "avg_ms": statistics.mean(results) * 1000,
        "p50_ms": results[int(n * 0.50)] * 1000,
        "p95_ms": results[int(n * 0.95)] * 1000,
        "p99_ms": results[min(int(n * 0.99), n - 1)] * 1000,
        "errors": len(errors),
        "error_statuses": dict(Counter(errors)),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8080/api/test")
    p.add_argument("--concurrency", type=int, default=50)
    p.add_argument("--duration", type=float, default=10.0)
    args = p.parse_args()
    result = asyncio.run(run(args.url, args.concurrency, args.duration))
    print(result)


if __name__ == "__main__":
    main()
