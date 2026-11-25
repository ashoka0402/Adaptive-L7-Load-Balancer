"""Measure a real isolated HTTP experiment against the Pulse proxy."""
import pytest

from app.experiments.http_lab import run_http
from app.experiments.spec import ExperimentSpec, NodeSpec


@pytest.mark.asyncio
async def test_http_lab_uses_actual_local_proxy():
    frames=[]
    async def progress(frame):
        frames.append(frame)
    spec=ExperimentSpec(mode="http",algorithms=["round_robin"],rps=10,duration=.4,
                        nodes=[NodeSpec(id="one",latency_ms=5,capacity=8)])
    result=await run_http(spec,"round_robin",7,progress)
    assert result["mode"]=="http"
    assert result["summary"]["offered"]==4
    assert result["summary"]["successful"]==4
    assert result["summary"]["distribution"]=={"one":4}
    assert result["summary"]["p95_ms"]>=4
    assert frames
