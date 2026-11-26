"""Independent sample application: real SQLite read and CPU work, no sleep."""
from __future__ import annotations

import argparse
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

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status":"ok","backend":backend_id})

    async def item(request: web.Request) -> web.Response:
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

    async def cleanup(app: web.Application) -> None:
        db.close()

    app = web.Application(client_max_size=1024)
    app.router.add_get("/health", health)
    app.router.add_get("/api/items/{item_id}", item)
    app.router.add_get("/api/test",item)
    app.router.add_get("/direct",item)
    if benchmark_token:
        app.router.add_post("/__benchmark/complexity", complexity)
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
