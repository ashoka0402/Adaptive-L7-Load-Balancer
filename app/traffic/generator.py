"""Bounded asynchronous HTTP traffic generator."""

from __future__ import annotations

import asyncio
import statistics
import time
from collections import Counter
from dataclasses import asdict, dataclass

import aiohttp


@dataclass(frozen=True)
class TrafficResult:
    requests: int
    successful: int
    errors: int
    elapsed_seconds: float
    rps: float
    average_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    statuses: dict[int, int]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AsyncTrafficGenerator:
    """Generate a fixed-duration workload without creating unbounded tasks."""

    def __init__(self, *, concurrency: int = 50, timeout: float = 10.0) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        self.concurrency = concurrency
        self.timeout = timeout

    async def run(
        self,
        url: str,
        *,
        duration: float,
        warmup: float = 0.0,
    ) -> TrafficResult:
        if duration <= 0 or warmup < 0:
            raise ValueError("duration must be positive and warmup cannot be negative")

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        connector = aiohttp.TCPConnector(limit=self.concurrency)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            if warmup:
                await self._phase(session, url, warmup, record=False)
            started = time.perf_counter()
            latencies, statuses = await self._phase(session, url, duration, record=True)
            elapsed = time.perf_counter() - started

        ordered = sorted(latencies)
        successful = sum(count for status, count in statuses.items() if 200 <= status < 400)
        errors = sum(statuses.values()) - successful
        return TrafficResult(
            requests=len(ordered),
            successful=successful,
            errors=errors,
            elapsed_seconds=elapsed,
            rps=len(ordered) / elapsed if elapsed else 0.0,
            average_ms=statistics.mean(ordered) * 1000 if ordered else 0.0,
            p50_ms=self._percentile(ordered, 0.50),
            p95_ms=self._percentile(ordered, 0.95),
            p99_ms=self._percentile(ordered, 0.99),
            statuses=dict(statuses),
        )

    async def _phase(
        self,
        session: aiohttp.ClientSession,
        url: str,
        duration: float,
        *,
        record: bool,
    ) -> tuple[list[float], Counter[int]]:
        deadline = time.perf_counter() + duration
        latencies: list[float] = []
        statuses: Counter[int] = Counter()

        async def worker() -> None:
            while time.perf_counter() < deadline:
                started = time.perf_counter()
                status = 0
                try:
                    async with session.get(url, allow_redirects=False) as response:
                        await response.read()
                        status = response.status
                except (aiohttp.ClientError, TimeoutError, OSError):
                    pass
                if record:
                    latencies.append(time.perf_counter() - started)
                    statuses[status] += 1

        await asyncio.gather(*(worker() for _ in range(self.concurrency)))
        return latencies, statuses

    @staticmethod
    def _percentile(ordered: list[float], quantile: float) -> float:
        if not ordered:
            return 0.0
        index = min(int((len(ordered) - 1) * quantile), len(ordered) - 1)
        return ordered[index] * 1000
