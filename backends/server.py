"""Configurable test backend server."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time

from aiohttp import web

from backends.failure_modes import FailureInjector, FailureMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backend")


class BackendServer:
    def __init__(
        self,
        host: str,
        port: int,
        backend_id: str,
        mode: FailureMode = FailureMode.NORMAL,
        slow_ms: int = 100,
    ) -> None:
        self.host = host
        self.port = port
        self.backend_id = backend_id
        self.injector = FailureInjector(mode=mode, slow_ms=slow_ms)
        self.app = web.Application()
        self.app.router.add_get("/health", self.health)
        self.app.router.add_route("*", "/{path:.*}", self.handle)
        self.request_count = 0

    async def health(self, request: web.Request) -> web.Response:
        if self.injector.mode == FailureMode.OFFLINE:
            return web.Response(status=503, text="offline")
        return web.json_response(
            {"status": "ok", "backend": self.backend_id, "mode": self.injector.mode.value}
        )

    async def handle(self, request: web.Request) -> web.Response:
        self.request_count += 1
        should_error, status = await self.injector.maybe_inject()
        if should_error:
            if status is None:
                # Hang until client times out
                await asyncio.sleep(60)
            return web.Response(status=status or 500, text="injected error")

        body = await request.read()
        return web.json_response(
            {
                "backend": self.backend_id,
                "method": request.method,
                "path": request.path,
                "request_id": request.headers.get("x-request-id"),
                "body_len": len(body),
                "request_count": self.request_count,
                "ts": time.time(),
            }
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8001)
    p.add_argument("--id", default="backend-1")
    p.add_argument(
        "--mode",
        default=os.environ.get("FAILURE_MODE", "normal"),
        choices=[m.value for m in FailureMode],
    )
    p.add_argument("--slow-ms", type=int, default=int(os.environ.get("SLOW_MS", "100")))
    args = p.parse_args()

    mode = FailureMode(args.mode)
    server = BackendServer(args.host, args.port, args.id, mode=mode, slow_ms=args.slow_ms)
    logger.info(
        "starting backend %s on %s:%s mode=%s",
        args.id,
        args.host,
        args.port,
        mode.value,
    )
    web.run_app(server.app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
