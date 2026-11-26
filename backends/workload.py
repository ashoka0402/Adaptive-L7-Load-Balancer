"""Independent sample application: real SQLite read and CPU work, no sleep."""
from __future__ import annotations

import argparse
import asyncio
import time
import hashlib
import hmac
import os
import sqlite3

from aiohttp import web


def create_workload(backend_id: str, benchmark_token: str = "") -> web.Application:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT, quantity INTEGER)")
    db.executemany("INSERT INTO items VALUES(?,?,?)",
                   [(i, f"Item {i}", i*3) for i in range(1,101)])
    db.commit()
    iterations = 1000
    fault = 'normal'
    fault_until = 0.0
    fault_count = 0

    def active_fault() -> str:
        return fault if time.monotonic() < fault_until else 'normal'

    async def health(request: web.Request) -> web.Response:
        if active_fault() == "offline":
            return web.json_response({"status":"unavailable","backend":backend_id},status=503)
        return web.json_response({"status":"ok","backend":backend_id})

    async def item(request: web.Request) -> web.Response:
        nonlocal fault_count
        mode = active_fault()
        fault_count += 1
        if mode == "offline" or mode == "unavailable":
            raise web.HTTPServiceUnavailable()
        if mode == "error" or (mode == "flaky" and fault_count % 3 == 0):
            raise web.HTTPInternalServerError()
        if mode == "timeout":
            await asyncio.sleep(6)
            raise web.HTTPGatewayTimeout()
        if mode == "disconnect":
            if request.transport is not None:
                request.transport.abort()
            return web.Response(status=503)
        try:
            item_id = int(request.match_info.get("item_id","1"))
        except ValueError:
            raise web.HTTPBadRequest()
        row = db.execute("SELECT id,name,quantity FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise web.HTTPNotFound()
        digest = hashlib.pbkdf2_hmac("sha256", row[1].encode(), b"pulse-workload", iterations)
        return web.json_response({"id":row[0],"name":row[1],"quantity":row[2],
                                  "checksum":digest.hex()[:16],"backend":backend_id})

    async def complexity(request: web.Request) -> web.Response:
        nonlocal iterations
        if not benchmark_token or not hmac.compare_digest(
            request.headers.get("X-Benchmark-Token",""),benchmark_token
        ):
            raise web.HTTPNotFound()
        data = await request.json()
        value = data.get("iterations")
        if type(value) is not int or not 1000 <= value <= 300000:
            raise web.HTTPBadRequest()
        iterations = value
        return web.json_response({"iterations":iterations})

    async def chaos(request: web.Request) -> web.Response:
        nonlocal fault, fault_until, fault_count
        if not benchmark_token or not hmac.compare_digest(
            request.headers.get("X-Benchmark-Token",""),benchmark_token
        ):
            raise web.HTTPNotFound()
        if request.method == "POST":
            try:
                data = await request.json()
            except (ValueError, TypeError):
                raise web.HTTPBadRequest()
            if not isinstance(data, dict):
                raise web.HTTPBadRequest()
            mode = data.get("mode")
            duration = data.get("duration", 30)
            if (mode not in ("normal","offline","unavailable","error","flaky","timeout","disconnect")
                    or type(duration) is not int or not 5 <= duration <= 120):
                raise web.HTTPBadRequest()
            fault = mode
            fault_count = 0
            fault_until = time.monotonic() + duration if mode != "normal" else 0.0
        return web.json_response({"mode":active_fault(),
            "remaining":round(max(0,fault_until-time.monotonic()),1)})

    async def cleanup(app: web.Application) -> None:
        db.close()

    app = web.Application(client_max_size=1024)
    app.router.add_get("/health", health)
    app.router.add_get("/api/items/{item_id}", item)
    app.router.add_get("/api/test",item)
    app.router.add_get("/direct",item)
    if benchmark_token:
        app.router.add_post("/__benchmark/complexity", complexity)
        app.router.add_get("/__benchmark/chaos", chaos)
        app.router.add_post("/__benchmark/chaos", chaos)
    app.on_cleanup.append(cleanup)
    return app


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--id", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    a = p.parse_args()
    web.run_app(create_workload(a.id,os.getenv("PULSE_BENCHMARK_TOKEN","")),
                host=a.host,port=a.port,access_log=None,print=None)


if __name__ == "__main__":
    main()
