"""Same-origin console assets and authenticated instance telemetry."""
from __future__ import annotations

import time
from pathlib import Path

from aiohttp import web

from app.app_keys import CONFIG_KEY, REGISTRY_KEY
from app.experiments.routes import authorize
from app.resilience.backpressure import BackpressureController
from app.ui.telemetry import ConsoleTelemetry

ASSETS = Path(__file__).parent / "static"
TELEMETRY = web.AppKey("console_telemetry", ConsoleTelemetry)
PRESSURE = web.AppKey("console_backpressure", BackpressureController)
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                               "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(ASSETS / "index.html", headers=HEADERS)


async def asset(request: web.Request) -> web.FileResponse:
    name = request.match_info["name"]
    if name not in {"app.js", "model.js", "style.css"}:
        raise web.HTTPNotFound()
    return web.FileResponse(ASSETS / name, headers=HEADERS)


async def overview(request: web.Request) -> web.Response:
    authorize(request)
    config = request.app[CONFIG_KEY]
    registry = request.app[REGISTRY_KEY]
    pressure = request.app[PRESSURE]
    nodes = []
    for backend in registry.list_all():
        data = backend.to_dict()
        data["eligible"] = backend.is_available
        nodes.append(data)
    return web.json_response({
        "timestamp": time.time(), "strategy": config.load_balancing.strategy,
        "mode": "live", "metrics": request.app[TELEMETRY].snapshot(),
        "active": pressure.current_active, "queue": pressure.queue_size,
        "rejected": pressure.rejected_total, "backends": nodes,
    }, headers={"Cache-Control": "no-store"})


def setup_console(app: web.Application, telemetry: ConsoleTelemetry,
                  pressure: BackpressureController) -> None:
    app[TELEMETRY] = telemetry
    app[PRESSURE] = pressure
    app.router.add_get("/pulse", index)
    app.router.add_get("/pulse/", index)
    app.router.add_get("/pulse/assets/{name}", asset)
    app.router.add_get("/admin/console", overview)
