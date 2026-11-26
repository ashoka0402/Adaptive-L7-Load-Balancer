"""Regression tests for real local lab controls and bounded experiment mechanics."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.fastapi_lb.routing import Backend, Pool
from app.fastapi_lb.server import create_app


def test_routing_trace_and_canary_split():
    stable = Backend("A", "http://a", ewma=10)
    canary = Backend("B", "http://b", version="v2")
    pool = Pool([stable, canary], "latency_aware", canary_percent=25)
    selections = []
    for _ in range(100):
        trace = {}
        node = pool.select(trace=trace)
        assert node is not None
        assert trace["selected"] == node.id
        assert trace["candidates"]
        selections.append(node.id)
        node.finish(10, True)
    assert selections.count("B") == 25
    trace = {}
    assert pool.select(excluded={"B"}, trace=trace) is stable
    stable.finish(10, True)


def test_demo_policy_fault_trace_replay_and_rollback():
    with TestClient(create_app(), base_url="http://localhost") as client:
        headers = {"X-Pulse-Local": "1"}

        def post(path, data):
            response = client.post("/api/lab/" + path, json=data, headers=headers)
            assert response.status_code == 200, response.text
            return response

        assert client.get("/pulse/lab.js").status_code == 200
        assert client.post("/api/lab/scaling", json={"enabled": True}).status_code == 403
        post("record/start", {})
        post("fault", {"backend": "A", "fault": "slow"})
        assert client.get("/admin/console").json()["backends"][0]["fault"] == "slow"
        post("fault", {"backend": "A", "fault": "normal"})
        assert client.get("/api/test").status_code == 200
        traces = client.get("/api/lab/traces").json()["traces"]
        assert traces[0]["attempts"][0]["selected"]
        assert "path" not in traces[0]
        post("policy", {"breaker": False, "retries": False, "pooling": False,
                        "rate_limit": False, "backpressure": False})
        assert not client.get("/admin/console").json()["lab"]["policy"]["breaker"]
        post("policy", {})
        post("canary", {"percent": 100, "faulty": True})
        for _ in range(3):
            assert client.get("/api/test").status_code == 500
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = client.get("/admin/console").json()["lab"]["canary"]
            if state["status"] == "rolled back":
                break
            time.sleep(.1)
        assert state["percent"] == 0
        assert state["status"] == "rolled back"
        assert client.get("/api/test").status_code == 200
        post("record/stop", {})
        replay = client.get("/api/lab/replay").json()
        assert replay["frames"] and not replay["recording"]


@pytest.mark.asyncio
async def test_scaling_drains_real_listener():
    from app.fastapi_lb.lab import Demo
    from app.fastapi_lb.telemetry import Telemetry

    demo = Demo(Pool([]), Telemetry(), "http://127.0.0.1:8080/api/test")
    node = await demo.add_backend()
    try:
        node.begin()
        task = asyncio.create_task(demo.remove_backend(node))
        await asyncio.sleep(.02)
        assert node.draining and not node.available
        node.finish(10, True)
        assert await task
        assert node not in demo.pool.backends
        assert node.id not in demo.runners
    finally:
        await demo.close()


@pytest.mark.asyncio
async def test_experiment_comparison_restores_original_settings(monkeypatch):
    from app.fastapi_lb.experiments import Experiments
    from app.fastapi_lb.lab import Demo
    from app.fastapi_lb.proxy import Proxy
    from app.fastapi_lb.routing import RouteTrie, STRATEGIES
    from app.fastapi_lb.telemetry import Telemetry

    telemetry = Telemetry()
    pool = Pool([Backend("A", "http://a")])
    demo = Demo(pool, telemetry, "http://127.0.0.1:8080/api/test")
    proxy = Proxy(RouteTrie(), None, telemetry)
    lab = Experiments(demo, proxy, asyncio.Lock())

    async def fake_workload(count, workers, rate=0):
        return {"successPct": 100, "p95": 10, "rps": 100, "offered": count}

    monkeypatch.setattr(lab, "workload", fake_workload)
    await lab.run("arena", "slow", 12)
    assert len(lab.results) == len(STRATEGIES)
    assert lab.winner in STRATEGIES
    assert pool.strategy == "round_robin" and pool.backends[0].fault == "normal"
    await lab.run("resilience", "outage", 12)
    assert len(lab.results) == 6
    assert proxy.policy["breaker"] and not proxy.policy["rate_limit"]
    await lab.run("breaking", "normal", 12)
    assert [r["offeredRps"] for r in lab.results] == [50,100,200,400,800]
