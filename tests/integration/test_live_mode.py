"""Live mode must forward actual application traffic without lab side effects."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.fastapi_lb.server import create_app


def test_live_default_no_generated_traffic_and_management_namespace():
    with TestClient(create_app(),base_url="http://localhost") as client:
        state=client.get("/pulse/api/console").json()
        assert state["mode"]=="live"
        assert state["strategy"]=="latency_aware"
        assert state["totals"]["total"]==0
        assert state["lab"] is None
        assert len(state["backends"])==3
        assert client.post("/pulse/api/lab/fault",
                           json={"backend":"backend-1","fault":"slow"},
                           headers={"X-Pulse-Local":"1"}).status_code==403


def test_live_forwarding_to_independent_application(tmp_path: Path):
    import os
    import socket
    import subprocess
    import sys
    import time

    import httpx

    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        port=sock.getsockname()[1]
    origin=f"http://127.0.0.1:{port}"
    env=dict(os.environ)
    env.pop("PULSE_BENCHMARK_TOKEN",None)
    child=subprocess.Popen([sys.executable,"-m","backends.workload","--id","real",
                            "--port",str(port)],env=env)
    try:
        for _ in range(100):
            try:
                if httpx.get(origin+"/health",timeout=.5).is_success:
                    break
            except httpx.HTTPError:
                time.sleep(.05)
        else:
            raise AssertionError("Independent application not ready")
        cfg=tmp_path/"routes.json"
        cfg.write_text(json.dumps({"routes":[{"prefix":"/","strategy":"latency_aware",
                     "backends":[{"id":"real","url":origin}]}]}))
        with TestClient(create_app(str(cfg)),base_url="http://localhost") as client:
            r=client.get("/api/items/7")
            assert r.status_code==200
            assert r.json()["id"]==7 and r.json()["quantity"]==21
            assert r.headers["x-backend"]=="real"
            assert float(r.headers["x-pulse-routing-us"])>=0
            assert float(r.headers["x-pulse-upstream-headers-ms"])>=0
            assert client.get("/api/items/999").status_code==404
            state=client.get("/pulse/api/console").json()
            assert state["totals"]["total"]==2
            assert state["backends"][0]["assigned"]==2
            assert client.get("/pulse/api/lab/traces").json()["traces"]
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
