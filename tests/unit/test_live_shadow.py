"""Shadow decisions observe copies and cannot execute network operations."""
import asyncio

import pytest

from app.config import ShadowConfig
from app.experiments.live_shadow import LiveShadow
from app.models.backend import Backend


@pytest.mark.asyncio
async def test_live_shadow_copies_and_compares_without_mutating_primary():
    shadow=LiveShadow(ShadowConfig(enabled=True,sample_every=10,algorithms=("least_connections",)))
    a=Backend("a","real-a",8001,active_connections=10,average_latency=.05)
    b=Backend("b","real-b",8002,active_connections=1,average_latency=.01)
    for _ in range(10):
        shadow.observe(a,[a,b])
    assert shadow.queue.qsize()==1
    # Change live values after sampling; evaluation must use the sampled values.
    b.active_connections=100
    shadow.start()
    await asyncio.wait_for(shadow.queue.join(),timeout=1)
    report=shadow.report()["candidates"]["least_connections"]
    assert report["samples"]==1
    assert report["disagreements"]==1
    assert report["estimated_latency_delta_ms"]==pytest.approx(-40)
    assert b.active_connections==100
    await shadow.stop()


def test_shadow_queue_is_bounded_and_disabled_by_default():
    a=Backend("a","example",1)
    shadow=LiveShadow(ShadowConfig())
    for _ in range(100):
        shadow.observe(a,[a])
    assert shadow.queue.empty()
    enabled=LiveShadow(ShadowConfig(enabled=True,sample_every=10,queue_limit=1))
    for _ in range(30):
        enabled.observe(a,[a])
    assert enabled.queue.qsize()==1
    assert enabled.dropped==2
