import asyncio

import pytest

from app.config import BackpressureConfig
from app.resilience.backpressure import BackpressureController


@pytest.mark.asyncio
async def test_cancelled_waiter_releases_queue_slot():
    gate = BackpressureController(BackpressureConfig(max_concurrent_requests=1, max_queue_size=1))
    assert await gate.acquire()
    waiter = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0)
    assert gate.queue_size == 1
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert gate.queue_size == 0
    next_waiter = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0)
    assert gate.queue_size == 1
    gate.release()
    assert await next_waiter
    gate.release()
    assert gate.current_active == gate.queue_size == 0


@pytest.mark.asyncio
async def test_zero_queue_accepts_idle_capacity_and_sheds_only_overload():
    gate = BackpressureController(BackpressureConfig(max_concurrent_requests=1, max_queue_size=0))
    assert await gate.acquire()
    assert not await gate.acquire()
    assert gate.rejected_total == 1
    gate.release()
    assert await gate.acquire()
    gate.release()


@pytest.mark.asyncio
async def test_queue_timeout_does_not_leak_capacity():
    gate = BackpressureController(BackpressureConfig(max_concurrent_requests=1, max_queue_size=1,
                                                     queue_timeout=.01))
    assert await gate.acquire()
    assert not await gate.acquire()
    assert gate.queue_size == 0
    gate.release()
    assert await gate.acquire()
    gate.release()
