"""Real origin admission and controller policy regression tests."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.fastapi_lb.capacity_control import CapacityController, IDS
from backends.capacity import Capacity, CapacityGate
from backends.workload import create_workload


@pytest.mark.asyncio
async def test_fifo_queue_is_bounded_and_paced():
    gate=CapacityGate()
    await gate.configure(Capacity(enabled=True,rate=10,concurrency=1,queue=2))
    assert await gate.acquire()
    order=[]
    async def wait(name):
        assert await gate.acquire()
        order.append(name)
        await gate.release()
    first=asyncio.create_task(wait("first"))
    await asyncio.sleep(.01)
    second=asyncio.create_task(wait("second"))
    await asyncio.sleep(.01)
    assert gate.snapshot()["queued"]==2
    assert not await gate.acquire()
    await gate.release()
    await asyncio.gather(first,second)
    assert order==["first","second"]
    assert gate.snapshot()["rejected"]==1
    assert gate.snapshot()["active"]==gate.snapshot()["queued"]==0
    assert gate.starts[-1]-gate.starts[0]>=.19


@pytest.mark.asyncio
async def test_sustained_overload_trips_and_recovers():
    gate=CapacityGate()
    await gate.configure(Capacity(enabled=True,rate=5,concurrency=1,queue=0,
                                  crash=True,cooldown=3))
    assert await gate.acquire()
    for _ in range(9):
        assert not await gate.acquire()
        await asyncio.sleep(.1)
    assert gate.failed()
    assert gate.snapshot()["failures"]==1
    await gate.release()
    await asyncio.sleep(3.1)
    assert not gate.failed()
    assert await gate.acquire()
    await gate.release()


@pytest.mark.asyncio
async def test_real_http_origin_returns_503_when_rate_exceeded():
    async with TestClient(TestServer(create_workload("test","secret"))) as client:
        headers={"X-Benchmark-Token":"secret"}
        path="/__benchmark/capacity"
        assert (await client.get(path)).status==404
        config={"enabled":True,"rate":5,"concurrency":1,"queue":0,"crash":False,"cooldown":3}
        assert (await client.post(path,json=config,headers=headers)).status==200
        assert (await client.get("/api/items/1")).status==200
        assert (await client.get("/api/items/2")).status==503
        assert (await client.get("/health")).status==200
        state=await (await client.get(path,headers=headers)).json()
        assert state["rejected"]>=1 and state["accepted"]>=1
        assert (await client.post(path,json={"rate":501},headers=headers)).status==400


@pytest.mark.asyncio
async def test_scaling_policy_recruits_and_drains_only_standbys(monkeypatch):
    class Generator:
        running=True
    controller=CapacityController(Generator(),"http://load-balancer:8080","secret",lambda b:b)
    controller.auto=True
    controller.standby=list(IDS[3:])
    actions=[]
    overloaded=True
    async def origin(backend,data=None):
        s={"state":"overloaded" if overloaded and backend in controller.members else "healthy",
           "incoming_rps":100 if overloaded else 0,"started_rps":25 if overloaded else 0,
           "rate":25,"enabled":True,"queued":3 if overloaded else 0}
        controller.states[backend]=s
        return s
    async def cluster(data=None):
        controller.available=True
        if data:
            actions.append(data)
            if data["action"]=="add":
                controller.members.append(data["backend"])
            else:
                controller.members.remove(data["backend"])
        return {"members":controller.members}
    monkeypatch.setattr(controller,"origin",origin)
    monkeypatch.setattr(controller,"cluster",cluster)
    await controller.tick()
    await controller.tick()
    assert actions==[{"action":"add","backend":"backend-4"}]
    overloaded=False
    controller.generator.running=False
    controller.last_scale=0
    for _ in range(8):
        await controller.tick()
    assert actions[-1]=={"action":"remove","backend":"backend-4"}
    assert controller.members==list(IDS[:3])
