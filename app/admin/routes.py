"""Administrative API for dynamic backend management."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Optional

from aiohttp import web

from app.models.backend import Backend, BackendStatus

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.models.backend import BackendRegistry

logger = logging.getLogger(__name__)


def _auth(request: web.Request, token: str) -> Optional[web.Response]:
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {token}":
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


async def list_backends(request: web.Request) -> web.Response:
    registry: BackendRegistry = request.app["registry"]
    return web.json_response([b.to_dict() for b in registry.list_all()])


async def add_backend(request: web.Request) -> web.Response:
    cfg: AppConfig = request.app["config"]
    if err := _auth(request, cfg.server.admin_token):
        return err
    registry: BackendRegistry = request.app["registry"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    host = body.get("host")
    port = body.get("port")
    weight = int(body.get("weight", 1))
    bid = body.get("id") or f"{host}-{port}"
    if not host or not port:
        return web.json_response({"error": "host and port required"}, status=400)
    if registry.get(bid):
        return web.json_response({"error": "backend exists"}, status=409)

    backend = Backend(id=bid, host=host, port=int(port), weight=weight)
    registry.add(backend)
    logger.info("admin: added backend %s", bid)
    return web.json_response(backend.to_dict(), status=201)


async def update_backend(request: web.Request) -> web.Response:
    cfg: AppConfig = request.app["config"]
    if err := _auth(request, cfg.server.admin_token):
        return err
    registry: BackendRegistry = request.app["registry"]
    bid = request.match_info["id"]
    backend = registry.get(bid)
    if not backend:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    if "weight" in body:
        backend.weight = max(1, int(body["weight"]))
    if "enabled" in body:
        backend.enabled = bool(body["enabled"])
    if body.get("status") == "DRAINING":
        backend.status = BackendStatus.DRAINING
        logger.info("admin: draining backend %s", bid)
    if body.get("status") == "HEALTHY" and backend.status == BackendStatus.DRAINING:
        backend.status = BackendStatus.HEALTHY

    return web.json_response(backend.to_dict())


async def delete_backend(request: web.Request) -> web.Response:
    cfg: AppConfig = request.app["config"]
    if err := _auth(request, cfg.server.admin_token):
        return err
    registry: BackendRegistry = request.app["registry"]
    bid = request.match_info["id"]
    removed = registry.remove(bid)
    if not removed:
        return web.json_response({"error": "not found"}, status=404)
    logger.info("admin: removed backend %s", bid)
    return web.json_response({"deleted": bid})


def setup_admin_routes(app: web.Application) -> None:
    app.router.add_get("/admin/backends", list_backends)
    app.router.add_post("/admin/backends", add_backend)
    app.router.add_patch("/admin/backends/{id}", update_backend)
    app.router.add_delete("/admin/backends/{id}", delete_backend)
