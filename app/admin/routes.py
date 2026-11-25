"""Administrative API for dynamic backend management."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from aiohttp import web

from app.app_keys import CONFIG_KEY, REGISTRY_KEY
from app.config import BackendConfig
from app.models.backend import Backend, BackendStatus

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.models.backend import BackendRegistry

logger = logging.getLogger(__name__)


def _auth(request: web.Request, token: str) -> web.Response | None:
    auth = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


async def list_backends(request: web.Request) -> web.Response:
    cfg: AppConfig = request.app[CONFIG_KEY]
    if err := _auth(request, cfg.server.admin_token):
        return err
    registry: BackendRegistry = request.app[REGISTRY_KEY]
    return web.json_response([b.to_dict() for b in registry.list_all()])


async def add_backend(request: web.Request) -> web.Response:
    cfg: AppConfig = request.app[CONFIG_KEY]
    if err := _auth(request, cfg.server.admin_token):
        return err
    registry: BackendRegistry = request.app[REGISTRY_KEY]
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "invalid json"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"error": "json object required"}, status=400)
    host = body.get("host")
    port = body.get("port")
    bid = body.get("id") or f"{host}-{port}"
    if not isinstance(host, str) or not host or len(host) > 253 or not port:
        return web.json_response({"error": "host and port required"}, status=400)
    if not isinstance(bid, str) or not bid or len(bid) > 128:
        return web.json_response({"error": "invalid backend id"}, status=400)
    if len(registry.list_all()) >= 100:
        return web.json_response({"error": "backend limit reached"}, status=409)
    try:
        port = int(port)
        weight = int(body.get("weight", 1))
        capacity = int(body.get("capacity", 100))
    except (TypeError, ValueError, OverflowError):
        return web.json_response({"error": "invalid numeric value"}, status=400)
    if not 1 <= port <= 65535 or not 1 <= weight <= 10000 or not 1 <= capacity <= 1000000:
        return web.json_response({"error": "invalid port, weight, or capacity"}, status=400)
    if registry.get(bid):
        return web.json_response({"error": "backend exists"}, status=409)

    try:
        validated = BackendConfig(id=bid, host=host, port=port, weight=weight, capacity=capacity)
    except ValueError:
        return web.json_response({"error": "invalid backend configuration"}, status=400)
    if not cfg.destination_allowed(validated.host, validated.port):
        return web.json_response({"error": "destination not in deployment allowlist"}, status=403)
    backend = Backend(**validated.model_dump())
    registry.add(backend)
    logger.info("admin: added backend %s", bid)
    return web.json_response(backend.to_dict(), status=201)


async def update_backend(request: web.Request) -> web.Response:
    cfg: AppConfig = request.app[CONFIG_KEY]
    if err := _auth(request, cfg.server.admin_token):
        return err
    registry: BackendRegistry = request.app[REGISTRY_KEY]
    bid = request.match_info["id"]
    backend = registry.get(bid)
    if not backend:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "invalid json"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"error": "json object required"}, status=400)
    try:
        weight = int(body.get("weight", backend.weight))
        capacity = int(body.get("capacity", backend.capacity))
    except (TypeError, ValueError, OverflowError):
        return web.json_response({"error": "invalid numeric value"}, status=400)
    if not 1 <= weight <= 10000 or not 1 <= capacity <= 1000000:
        return web.json_response({"error": "weight or capacity out of range"}, status=400)
    enabled = body.get("enabled", backend.enabled)
    if not isinstance(enabled, bool):
        return web.json_response({"error": "enabled must be boolean"}, status=400)
    status = body.get("status")
    if status is not None and (not isinstance(status, str) or status not in {"DRAINING", "HEALTHY"}):
        return web.json_response({"error": "invalid status"}, status=400)

    backend.weight = weight
    backend.capacity = capacity
    backend.enabled = enabled
    if status == "DRAINING":
        backend.status = BackendStatus.DRAINING
        logger.info("admin: draining backend %s", bid)
    if status == "HEALTHY" and backend.status == BackendStatus.DRAINING:
        backend.status = BackendStatus.HEALTHY

    return web.json_response(backend.to_dict())


async def delete_backend(request: web.Request) -> web.Response:
    cfg: AppConfig = request.app[CONFIG_KEY]
    if err := _auth(request, cfg.server.admin_token):
        return err
    registry: BackendRegistry = request.app[REGISTRY_KEY]
    bid = request.match_info["id"]
    removed = registry.remove(bid)
    if not removed:
        return web.json_response({"error": "not found"}, status=404)
    logger.info("admin: removed backend %s", bid)
    return web.json_response({"deleted": bid})


def setup_admin_routes(app: web.Application, switch_strategy: Callable[[str], None]) -> None:
    async def update_strategy(request: web.Request) -> web.Response:
        cfg = request.app[CONFIG_KEY]
        if err := _auth(request, cfg.server.admin_token):
            return err
        try:
            body = await request.json()
            if not isinstance(body, dict) or not isinstance(body.get("strategy"), str):
                raise TypeError("strategy required")
            switch_strategy(body["strategy"])
        except (TypeError, ValueError):
            return web.json_response({"error": "unknown routing strategy"}, status=400)
        return web.json_response({"strategy": cfg.load_balancing.strategy})

    app.router.add_patch("/admin/strategy", update_strategy)
    app.router.add_get("/admin/backends", list_backends)
    app.router.add_post("/admin/backends", add_backend)
    app.router.add_patch("/admin/backends/{id}", update_backend)
    app.router.add_delete("/admin/backends/{id}", delete_backend)
