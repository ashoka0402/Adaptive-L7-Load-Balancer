"""Isolated laboratory origin, with process-shared fault settings."""
from __future__ import annotations

import asyncio

from aiohttp import web

FAULTS = ("normal", "slow", "flaky", "error", "unavailable", "timeout")


def serve_origin(backend_id: str, delay: int, state, ready) -> None:
    async def run() -> None:
        count = 0
        last_generation = -1
        async def handle(request: web.Request) -> web.Response:
            nonlocal count, last_generation
            with state.get_lock():
                fault, disabled, generation = state[:]
            if generation != last_generation:
                count = 0
                last_generation = generation
            if request.path == "/health":
                return web.Response(status=503 if disabled else 200)
            if disabled:
                return web.Response(status=503)
            count += 1
            current = FAULTS[fault]
            await asyncio.sleep((delay + (250 if current in {"slow","timeout"} else 0))/1000)
            if current == "timeout":
                await asyncio.sleep(5.5)
            if current == "error" or (current == "flaky" and count % 3 == 0):
                return web.Response(status=500)
            if current == "unavailable":
                return web.Response(status=503)
            return web.Response(text=backend_id, headers={"X-Backend": backend_id})
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handle)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner,"127.0.0.1",0).start()
            ready.send(runner.addresses[0][1])
            ready.close()
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
    asyncio.run(run())
