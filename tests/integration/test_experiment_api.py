"""Exercise real admin HTTP routes, persistence, and replay."""
import asyncio

import aiohttp
import pytest

from tests.integration.test_proxy import running_stack


@pytest.mark.asyncio
async def test_experiment_api_local_header_and_replay():
    headers = {"X-Pulse-Local": "1"}
    async with running_stack() as (pulse, _state, url), aiohttp.ClientSession() as session:
        async with session.post(f"{url}/admin/experiments",json={}) as response:
            assert response.status==403
        async with session.post(f"{url}/admin/experiments",headers=headers,
                                json={"target":"http://example.com"}) as response:
            assert response.status==400
        spec={"algorithms":["round_robin","latency_aware"],"rps":20,"duration":.4}
        async with session.post(f"{url}/admin/experiments",headers=headers,json=spec) as response:
            assert response.status==202
            first=(await response.json())["id"]
        await asyncio.wait_for(pulse.experiments.jobs[first].task,timeout=10)
        async with session.get(f"{url}/admin/experiments/{first}?full=true",headers=headers) as response:
            assert response.status==200
            data=await response.json()
            assert data["state"]=="completed"
            assert len(data["runs"])==2
            assert all(r["summary"]["offered"]==8 for r in data["runs"])
        async with session.get(f"{url}/admin/experiments/{first}/export?format=csv",headers=headers) as response:
            assert response.status==200
            assert len((await response.text()).splitlines())==17
        async with session.post(f"{url}/admin/experiments/{first}/replay",headers=headers) as response:
            assert response.status==200
            second=(await response.json())["id"]
        await asyncio.wait_for(pulse.experiments.jobs[second].task,timeout=10)
        assert pulse.experiments.jobs[second].runs==data["runs"]
