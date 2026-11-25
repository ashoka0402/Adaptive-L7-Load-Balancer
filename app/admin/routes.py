"""Administrative API for dynamic backend management."""

from __future__ import annotations

import hmac
import logging
import math
from typing import TYPE_CHECKING

from aiohttp import web

from app.app_keys import CONFIG_KEY, CONTROLLER_KEY, REGISTRY_KEY, TELEMETRY_KEY
from app.models.backend import Backend, BackendStatus

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.control.controller import AdaptiveController
    from app.models.backend import BackendRegistry
    from app.telemetry.pipeline import TelemetryPipeline

logger = logging.getLogger(__name__)


def _auth(request: web.Request, token: str) -> web.Response | None:
    auth = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth, f"Bearer {token}"):
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

    backend = Backend(id=bid, host=host, port=port, weight=weight, capacity=capacity)
    registry.add(backend)
    controller: AdaptiveController = request.app[CONTROLLER_KEY]
    controller.publish()
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
    if status not in {None, "DRAINING", "HEALTHY"}:
        return web.json_response({"error": "invalid status"}, status=400)

    backend.weight = weight
    backend.capacity = capacity
    backend.enabled = enabled
    if status == "DRAINING":
        backend.status = BackendStatus.DRAINING
        logger.info("admin: draining backend %s", bid)
    if status == "HEALTHY" and backend.status == BackendStatus.DRAINING:
        backend.status = BackendStatus.HEALTHY

    controller: AdaptiveController = request.app[CONTROLLER_KEY]
    controller.publish()
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
    pipeline: TelemetryPipeline = request.app[TELEMETRY_KEY]
    pipeline.remove(bid)
    controller: AdaptiveController = request.app[CONTROLLER_KEY]
    controller.publish()
    logger.info("admin: removed backend %s", bid)
    return web.json_response({"deleted": bid})


async def routing_policy(request: web.Request) -> web.Response:
    cfg: AppConfig = request.app[CONFIG_KEY]
    if err := _auth(request, cfg.server.admin_token):
        return err
    controller: AdaptiveController = request.app[CONTROLLER_KEY]
    return web.json_response(controller.snapshot.to_dict())


async def update_routing_weights(request: web.Request) -> web.Response:
    cfg: AppConfig = request.app[CONFIG_KEY]
    if err := _auth(request, cfg.server.admin_token):
        return err
    controller: AdaptiveController = request.app[CONTROLLER_KEY]
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise TypeError("json object required")
        weights = {key: float(value) for key, value in body.items()}
        controller.update_weights(weights)
    except (TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response(controller.snapshot.to_dict())


async def update_backend_telemetry(request: web.Request) -> web.Response:
    cfg: AppConfig = request.app[CONFIG_KEY]
    if err := _auth(request, cfg.server.admin_token):
        return err
    registry: BackendRegistry = request.app[REGISTRY_KEY]
    backend_id = request.match_info["id"]
    if not registry.get(backend_id):
        return web.json_response({"error": "not found"}, status=404)
    pipeline: TelemetryPipeline = request.app[TELEMETRY_KEY]
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise TypeError("json object required")
        for key in ("cpu", "memory"):
            if key in body and not math.isfinite(float(body[key])):
                raise ValueError("telemetry values must be finite")
        queue_depth = int(body["queue_depth"]) if "queue_depth" in body else None
        if queue_depth is not None and not 0 <= queue_depth <= 1000000:
            raise ValueError("queue_depth out of range")
        pipeline.update_external(
            backend_id,
            queue_depth=queue_depth,
            cpu=float(body["cpu"]) if "cpu" in body else None,
            memory=float(body["memory"]) if "memory" in body else None,
        )
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid telemetry"}, status=400)
    return web.json_response({"updated": backend_id})


def setup_admin_routes(app: web.Application) -> None:
    app.router.add_get("/admin/backends", list_backends)
    app.router.add_post("/admin/backends", add_backend)
    app.router.add_patch("/admin/backends/{id}", update_backend)
    app.router.add_delete("/admin/backends/{id}", delete_backend)
    app.router.add_put("/admin/backends/{id}/telemetry", update_backend_telemetry)
    app.router.add_get("/admin/routing/policy", routing_policy)
    app.router.add_patch("/admin/routing/weights", update_routing_weights)
