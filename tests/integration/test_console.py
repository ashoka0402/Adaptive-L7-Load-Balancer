"""The console is same-origin, authenticated, and reports actual proxy outcomes."""
import aiohttp
import pytest

from tests.integration.test_proxy import running_stack


@pytest.mark.asyncio
async def test_console_reports_live_proxy_outcomes_not_demo_values():
    headers = {"Authorization": "Bearer integration-secret-token"}
    async with running_stack() as (_pulse, state, url), aiohttp.ClientSession() as session:
        async with session.get(f"{url}/pulse/") as response:
            assert response.status == 200
            assert "Pulse" in await response.text()
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        async with session.get(f"{url}/admin/console") as response:
            assert response.status == 401
        async with session.get(f"{url}/admin/console", headers=headers) as response:
            assert (await response.json())["metrics"]["total"] == 0
        async with session.get(f"{url}/api/work") as response:
            assert response.status == 200
            await response.read()
        async with session.get(f"{url}/admin/console", headers=headers) as response:
            data = await response.json()
            assert data["metrics"]["total"] == 1
            assert data["metrics"]["sample_count"] == 1
            assert data["metrics"]["p99_ms"] >= 0
            assert data["backends"][0]["successful_requests"] == 1
            assert data["mode"] == "live"
        assert state["requests"] == 1


@pytest.mark.asyncio
async def test_console_error_rate_counts_actual_failure():
    headers = {"Authorization": "Bearer integration-secret-token"}
    async with running_stack(response_status=503) as (_pulse, _state, url), aiohttp.ClientSession() as session:
        async with session.get(f"{url}/error") as response:
            assert response.status == 503
            await response.read()
        async with session.get(f"{url}/admin/console", headers=headers) as response:
            data = await response.json()
            assert data["metrics"]["total"] == 1
            assert data["metrics"]["error_rate"] == 1
