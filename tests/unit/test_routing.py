"""Unit tests for load-balancing strategies."""

import pytest

from app.models.backend import Backend, BackendStatus
from app.routing.latency_aware import LatencyAwareStrategy
from app.routing.least_connections import LeastConnectionsStrategy
from app.routing.round_robin import RoundRobinStrategy
from app.routing.weighted_round_robin import WeightedRoundRobinStrategy


def _backends(n: int = 3) -> list[Backend]:
    return [
        Backend(id=f"b{i}", host="h", port=8000 + i, weight=1)
        for i in range(1, n + 1)
    ]


@pytest.mark.asyncio
async def test_round_robin_cycles():
    s = RoundRobinStrategy()
    backends = _backends(3)
    order = [ (await s.select_backend(backends)).id for _ in range(6) ]
    assert order == ["b1", "b2", "b3", "b1", "b2", "b3"]


@pytest.mark.asyncio
async def test_round_robin_empty():
    s = RoundRobinStrategy()
    assert await s.select_backend([]) is None


@pytest.mark.asyncio
async def test_weighted_round_robin_distribution():
    s = WeightedRoundRobinStrategy()
    backends = [
        Backend(id="a", host="h", port=1, weight=5),
        Backend(id="b", host="h", port=2, weight=3),
        Backend(id="c", host="h", port=3, weight=2),
    ]
    counts = {"a": 0, "b": 0, "c": 0}
    for _ in range(100):
        sel = await s.select_backend(backends)
        counts[sel.id] += 1
    # Approximate proportions 50/30/20
    assert counts["a"] > counts["b"] > counts["c"]
    assert counts["a"] >= 40
    assert counts["c"] >= 10


@pytest.mark.asyncio
async def test_least_connections():
    s = LeastConnectionsStrategy()
    backends = _backends(3)
    backends[0].active_connections = 10
    backends[1].active_connections = 2
    backends[2].active_connections = 5
    sel = await s.select_backend(backends)
    assert sel.id == "b2"


@pytest.mark.asyncio
async def test_latency_aware_prefers_faster():
    s = LatencyAwareStrategy()
    backends = _backends(3)
    backends[0].average_latency = 0.02
    backends[0].successful_requests = 10
    backends[1].average_latency = 0.50
    backends[1].successful_requests = 10
    backends[2].average_latency = 0.025
    backends[2].successful_requests = 10
    # Run several times; should strongly prefer b1 or b3
    picks = [ (await s.select_backend(backends)).id for _ in range(20) ]
    assert picks.count("b2") < 5
