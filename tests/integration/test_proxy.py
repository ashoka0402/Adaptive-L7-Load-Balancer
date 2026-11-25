"""End-to-end proxy tests with ephemeral aiohttp listeners."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import aiohttp
import pytest
from aiohttp import web

from app.config import (
    AllowedDestination,
    AppConfig,
    BackendConfig,
    HealthCheckConfig,
    PersistenceConfig,
    ServerConfig,
)
from app.models.backend import CircuitState
from app.server import LoadBalancerApp


@asynccontextmanager
async def running_stack(*, response_status: int = 200, delay: float = 0):
    state = {"requests": 0}

    async def health(_request):
        return web.Response(text="ok")

    async def handle(_request):
        state["requests"] += 1
        if delay:
            await asyncio.sleep(delay)
        return web.json_response({"served": state["requests"]}, status=response_status)

    backend_app = web.Application()
    backend_app.router.add_get("/health", health)
    backend_app.router.add_route("*", "/{path:.*}", handle)
    backend_runner = web.AppRunner(backend_app)
    await backend_runner.setup()
    backend_site = web.TCPSite(backend_runner, "127.0.0.1", 0)
    await backend_site.start()
    backend_port = backend_site._server.sockets[0].getsockname()[1]

    config = AppConfig(
        server=ServerConfig(host="127.0.0.1", port=0, admin_token="integration-secret-token"),
        backends=[BackendConfig(id="backend", host="127.0.0.1", port=backend_port)],
        allowed_dynamic_destinations=[AllowedDestination(host="127.0.0.1", port=8999)],
        health_check=HealthCheckConfig(interval=60),
        persistence=PersistenceConfig(enabled=False),
    )
    pulse = LoadBalancerApp(config)
    await pulse.start()
    pulse_port = pulse.site._server.sockets[0].getsockname()[1]
    try:
        yield pulse, state, f"http://127.0.0.1:{pulse_port}"
    finally:
        await pulse.stop()
        await backend_runner.cleanup()


@pytest.mark.asyncio
async def test_proxy_forwards_and_exposes_explainable_decision():
    async with (
        running_stack() as (_pulse, _state, url),
        aiohttp.ClientSession() as session,
        session.get(f"{url}/api/test") as response,
    ):
        assert response.status == 200
        assert response.headers["x-backend-id"] == "backend"
        assert "score=" in response.headers["x-pulse-route"]


@pytest.mark.asyncio
async def test_concurrent_requests_release_all_capacity():
    async with running_stack() as (pulse, _state, url):
        async with aiohttp.ClientSession() as session:
            responses = await asyncio.gather(*(session.get(f"{url}/work") for _ in range(100)))
            await asyncio.gather(*(response.read() for response in responses))
            for response in responses:
                response.release()

        assert pulse.registry.get("backend").active_connections == 0
        assert pulse.pool.active_connections("backend") == 0
        assert pulse.backpressure.current_active == 0


@pytest.mark.asyncio
async def test_streamed_503_is_not_retried_or_double_decremented():
    async with (
        running_stack(response_status=503) as (pulse, state, url),
        aiohttp.ClientSession() as session,
        session.get(f"{url}/failure") as response,
    ):
        assert response.status == 503
        await response.read()

    assert state["requests"] == 1
    assert pulse.registry.get("backend").active_connections == 0


@pytest.mark.asyncio
async def test_cancelled_half_open_request_releases_recovery_probe():
    async with running_stack(delay=5) as (pulse, state, url), aiohttp.ClientSession() as session:
        backend = pulse.registry.get("backend")
        backend.circuit_state = CircuitState.HALF_OPEN
        request = asyncio.create_task(session.get(f"{url}/slow"))
        while state["requests"] == 0:
            await asyncio.sleep(0.01)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

    assert backend.half_open_in_flight == 0


@pytest.mark.asyncio
async def test_invalid_admin_update_is_atomic_and_bounded():
    headers = {"Authorization": "Bearer integration-secret-token"}
    async with running_stack() as (pulse, _state, url), aiohttp.ClientSession() as session:
        async with session.patch(
            f"{url}/admin/backends/backend",
            headers=headers,
            json={"weight": 5, "capacity": "bad"},
        ) as response:
            assert response.status == 400
        backend = pulse.registry.get("backend")
        assert backend.weight == 1
        assert backend.capacity == 100

        async with session.put(
            f"{url}/admin/backends/backend/telemetry",
            headers=headers,
            json={"queue_depth": 10**20},
        ) as response:
            assert response.status == 400


@pytest.mark.asyncio
async def test_admin_backend_changes_publish_fresh_routing_policy():
    headers = {"Authorization": "Bearer integration-secret-token"}
    async with running_stack() as (pulse, _state, url), aiohttp.ClientSession() as session:
        version = pulse.controller.snapshot.version
        async with session.post(
            f"{url}/admin/backends",
            headers=headers,
            json={"id": "extra", "host": "127.0.0.1", "port": 8999},
        ) as response:
            assert response.status == 201
        assert pulse.controller.snapshot.version > version
        assert "extra" in pulse.controller.snapshot.policies

        version = pulse.controller.snapshot.version
        async with session.patch(
            f"{url}/admin/backends/extra",
            headers=headers,
            json={"enabled": False},
        ) as response:
            assert response.status == 200
        assert pulse.controller.snapshot.version > version
        assert "extra" not in pulse.controller.snapshot.policies

        version = pulse.controller.snapshot.version
        async with session.delete(
            f"{url}/admin/backends/extra", headers=headers
        ) as response:
            assert response.status == 200
        assert pulse.controller.snapshot.version > version
        assert "extra" not in pulse.controller.snapshot.policies
