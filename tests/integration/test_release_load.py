"""Bounded real HTTP concurrency and cancellation regression tests."""
import asyncio

import aiohttp
import pytest

from tests.integration.test_proxy import running_stack


@pytest.mark.asyncio
async def test_repeated_concurrent_batches_leave_no_admission_or_pool_leaks():
    async with running_stack(delay=.005) as (pulse, state, url), aiohttp.ClientSession() as session:
        async def send():
            async with session.get(url + "/work") as response:
                await response.read()
                return response.status

        for _ in range(12):
            statuses = await asyncio.gather(*(send() for _ in range(50)))
            assert statuses == [200] * 50
            assert pulse.backpressure.current_active == 0
            assert pulse.backpressure.queue_size == 0
            assert pulse.pool.active_connections("backend") == 0
            assert pulse.registry.get("backend").active_connections == 0

        assert state["requests"] == 600
        assert pulse.console.snapshot()["total"] == 600
