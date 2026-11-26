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
