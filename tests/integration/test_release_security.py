"""Regression checks for destination policy, protocol forwarding and client isolation."""
import gzip

import aiohttp
import pytest
from aiohttp import web

from app.config import AppConfig, BackendConfig, HealthCheckConfig, PersistenceConfig, ServerConfig
from app.server import LoadBalancerApp
from tests.integration.test_proxy import running_stack

AUTH = {"Authorization": "Bearer integration-secret-token"}


@pytest.mark.asyncio
async def test_unapproved_destinations_and_reserved_paths_never_reach_upstream():
    async with running_stack() as (pulse, state, url), aiohttp.ClientSession() as session:
        for host, port in [("169.254.169.254", 80), ("127.0.0.1", 9000), ("bad@host", 80)]:
            async with session.post(url+"/admin/backends", headers=AUTH,
                                    json={"id":"unsafe","host":host,"port":port}) as response:
                assert response.status in {400, 403}
        assert pulse.registry.get("unsafe") is None
        for path in ("/admin/not-a-route", "/pulse/missing"):
            async with session.get(url+path, headers=AUTH) as response:
                assert response.status == 404
        async with session.post(url+"/admin/console", headers=AUTH) as response:
            assert response.status == 404
        assert state["requests"] == 0


@pytest.mark.asyncio
async def test_forwarding_preserves_gzip_and_does_not_share_cookies_or_admin_token():
    payload = gzip.compress(b"real compressed response")
    seen = []
    async def handler(request):
        if request.path == "/health":
            return web.Response(text="healthy")
        seen.append(dict(request.headers))
        response = web.Response(body=payload, headers={"Content-Encoding":"gzip"})
        response.set_cookie("private", "first-client")
        return response

    app = web.Application()
    app.router.add_get("/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    pulse = LoadBalancerApp(AppConfig(
        server=ServerConfig(host="127.0.0.1", port=0, admin_token="integration-secret-token"),
        backends=[BackendConfig(id="b", host="127.0.0.1", port=port)],
        health_check=HealthCheckConfig(interval=60), persistence=PersistenceConfig(enabled=False),
    ))
    try:
        await pulse.start()
        url = f"http://127.0.0.1:{pulse.runner.addresses[0][1]}"
        async with aiohttp.ClientSession(auto_decompress=False) as session:
            async with session.get(url+"/a", headers=AUTH) as response:
                assert response.headers["Content-Encoding"] == "gzip"
                assert await response.read() == payload
            async with session.get(url+"/b") as response:
                assert await response.read() == payload
        assert len(seen) == 2
        assert not any(key.lower()=="authorization" for key in seen[0])
        assert not any(key.lower()=="cookie" for key in seen[1])
    finally:
        await pulse.stop()
        await runner.cleanup()
