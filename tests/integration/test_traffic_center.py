"""Traffic controller tests: independent HTTP client, bounded local controls."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.fastapi_lb.traffic import Generator, TrafficOptions, create_app


def test_traffic_center_security_and_validation(monkeypatch):
    monkeypatch.delenv("PULSE_BENCHMARK_TOKEN",raising=False)
    with TestClient(create_app(),base_url="http://localhost:8081") as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/status").json()["state"] == "idle"
        assert client.post("/api/start",json={}).status_code == 403
        headers={"X-Pulse-Local":"1"}
        assert client.post("/api/start",json={"rate":301},headers=headers).status_code == 422
        assert client.post("/api/work",json={"backend":"backend-2","iterations":200000},
                           headers=headers).status_code == 409
        assert client.get("/api/status",headers={"Host":"external.example"}).status_code == 403
        assert client.get("/api/status",headers={"Sec-Fetch-Site":"cross-site"}).status_code == 403


@pytest.mark.asyncio
async def test_generator_real_tcp_requests_and_statistics():
    received = []
    async def serve(reader,writer):
        try:
            while True:
                line=await reader.readline()
                if not line:
                    break
                received.append(line.decode().strip())
                while await reader.readline() not in (b"\r\n",b""):
                    pass
                body=json.dumps({"id":1}).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nX-Backend: test-origin\r\nContent-Length: "
                             +str(len(body)).encode()+b"\r\n\r\n"+body)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server=await asyncio.start_server(serve,"127.0.0.1",0)
    try:
        port=server.sockets[0].getsockname()[1]
        generator=Generator(f"http://127.0.0.1:{port}")
        generator.task=asyncio.create_task(generator.run(TrafficOptions(rate=2,duration=5,concurrency=2)))
        await generator.task
        state=generator.snapshot()
        assert state["state"]=="completed"
        assert state["success"]==10 and state["offered"]==10
        assert state["backends"]=={"test-origin":10}
        assert len(received)==10 and all("GET /api/items/" in x for x in received)
    finally:
        server.close()
        await server.wait_closed()


def test_chaos_controls_require_local_header_and_config(monkeypatch):
    monkeypatch.delenv("PULSE_BENCHMARK_TOKEN",raising=False)
    with TestClient(create_app(),base_url="http://localhost:8081") as client:
        body={"backend":"backend-2","mode":"offline","duration":30}
        assert client.post("/api/chaos",json=body).status_code==403
        assert client.post("/api/chaos",json=body,headers={"X-Pulse-Local":"1"}).status_code==409
        assert client.post("/api/chaos",json={**body,"duration":121},
                           headers={"X-Pulse-Local":"1"}).status_code==422


@pytest.mark.asyncio
async def test_real_sample_origin_chaos_and_recovery():
    from aiohttp.test_utils import TestClient as AioClient, TestServer
    from backends.workload import create_workload
    async with AioClient(TestServer(create_workload("test","secret"))) as client:
        control="/__benchmark/chaos"
        headers={"X-Benchmark-Token":"secret"}
        assert (await client.post(control,json={"mode":"offline"})).status==404
        for mode,health,item in [("offline",503,503),("unavailable",200,503),
                                 ("error",200,500),("normal",200,200)]:
            r=await client.post(control,json={"mode":mode,"duration":5},headers=headers)
            assert r.status==200
            assert (await client.get("/health")).status==health
            assert (await client.get("/api/items/1")).status==item
        await client.post(control,json={"mode":"flaky","duration":5},headers=headers)
        assert [(await client.get("/api/items/1")).status for _ in range(3)]==[200,200,500]


@pytest.mark.asyncio
async def test_fault_auto_expires_without_controller():
    from aiohttp.test_utils import TestClient as AioClient, TestServer
    from backends.workload import create_workload
    async with AioClient(TestServer(create_workload("test","secret"))) as client:
        await client.post("/__benchmark/chaos",json={"mode":"offline","duration":5},
                          headers={"X-Benchmark-Token":"secret"})
        assert (await client.get("/health")).status==503
        await asyncio.sleep(5.1)
        assert (await client.get("/health")).status==200
        assert (await client.get("/api/items/1")).status==200
