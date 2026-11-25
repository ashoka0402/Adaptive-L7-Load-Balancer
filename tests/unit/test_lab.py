"""Tests assert actual model outcomes, not a predetermined algorithm winner."""
import asyncio

import pytest
from pydantic import ValidationError

from app.experiments.lab import simulate
from app.experiments.manager import ExperimentManager
from app.experiments.spec import ExperimentSpec, Fault, NodeSpec, trace


async def ignore(frame):
    pass


@pytest.mark.asyncio
async def test_seeded_replay_same_outcomes_and_counts():
    spec=ExperimentSpec(rps=40,duration=1,pattern="mixed",algorithms=["predictive"])
    a=await simulate(spec,"predictive",7,ignore)
    b=await simulate(spec,"predictive",7,ignore)
    assert a==b
    assert len(a["requests"])==len(trace(spec,7))
    assert a["summary"]["successful"]+a["summary"]["errors"]==a["summary"]["offered"]
    assert all(r["completed"]>=r["arrival"] for r in a["requests"])


@pytest.mark.asyncio
async def test_down_backend_cannot_serve_during_fault_and_recovers():
    spec=ExperimentSpec(rps=20,duration=1,nodes=[NodeSpec(id="only",latency_ms=1)],
                        faults=[Fault(at=.2,backend="only",kind="down"),
                                Fault(at=.7,backend="only",kind="restore")])
    run=await simulate(spec,"round_robin",7,ignore)
    rows=run["requests"]
    assert all(r["status"]=="dropped" for r in rows if .2<=r["arrival"]<.7)
    assert all(r["status"]=="ok" for r in rows if r["arrival"]>=.7)
    assert run["summary"]["errors"]>0


@pytest.mark.asyncio
async def test_queue_capacity_and_client_timeout_are_real():
    spec=ExperimentSpec(rps=1000,duration=.1,timeout_ms=30,
                        nodes=[NodeSpec(id="one",capacity=1,queue_limit=1,latency_ms=100)])
    run=await simulate(spec,"round_robin",7,ignore)
    assert run["summary"]["successful"]==0
    assert run["summary"]["timeouts"]>0
    assert run["summary"]["dropped"]>0
    assert run["summary"]["peak_queue"]<=1
    assert all(r["latency_ms"]==pytest.approx(30) for r in run["requests"] if r["status"]=="timeout")


@pytest.mark.asyncio
async def test_arena_uses_same_arrivals_and_does_not_duplicate_shadow_requests():
    spec=ExperimentSpec(rps=60,duration=1,shadow=["least_connections"],shadow_every=1)
    a=await simulate(spec,"round_robin",7,ignore)
    b=await simulate(spec,"least_connections",7,ignore)
    assert [(r["id"],r["arrival"]) for r in a["requests"]]==[(r["id"],r["arrival"]) for r in b["requests"]]
    assert len(a["requests"])==a["summary"]["offered"]
    assert a["shadow"]["least_connections"]["samples"]==len(a["requests"])


def test_external_targets_and_excessive_load_rejected():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({"target":"https://example.com"})
    with pytest.raises(ValidationError):
        ExperimentSpec(mode="http",rps=1000)
    with pytest.raises(ValidationError):
        ExperimentSpec(faults=[Fault(at=1,backend="production",kind="down")])


@pytest.mark.asyncio
async def test_cancel_and_replay_lifecycle():
    manager=ExperimentManager()
    spec=ExperimentSpec(rps=100,duration=2)
    first=manager.start(spec)
    with pytest.raises(RuntimeError):
        manager.start(spec)
    await manager.cancel(first)
    assert first.state=="cancelled"
    second=manager.start(spec)
    await asyncio.wait_for(second.task,timeout=10)
    assert second.state=="completed"
    assert len(second.runs)==len(spec.algorithms)
    await manager.stop()


@pytest.mark.asyncio
async def test_persisted_result_can_be_loaded_and_replayed(tmp_path):
    from app.persistence.sqlite import SQLiteStore

    store=SQLiteStore(tmp_path/"experiments.db")
    store.initialize()
    manager=ExperimentManager(store)
    job=manager.start(ExperimentSpec(rps=10,duration=.4,algorithms=["round_robin"]))
    await asyncio.wait_for(job.task,timeout=10)
    saved=store.get_experiment(job.id)
    assert saved is not None and saved["state"]=="completed"
    assert store.recent_experiments()[0]["id"]==job.id
    replay=manager.start(ExperimentSpec.model_validate(saved["spec"]))
    await asyncio.wait_for(replay.task,timeout=10)
    assert replay.runs==saved["runs"]
