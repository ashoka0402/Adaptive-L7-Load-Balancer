"""Bounded experiment lifecycle, replay, and result retention."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from app.experiments.http_lab import run_http
from app.experiments.lab import ranking, simulate
from app.experiments.spec import ExperimentSpec
from app.persistence.sqlite import SQLiteStore

logger = logging.getLogger(__name__)
TERMINAL = {"completed","cancelled","failed"}


@dataclass
class Job:
    id: str
    spec: ExperimentSpec
    state: str = "queued"
    progress: dict = field(default_factory=dict)
    runs: list[dict] = field(default_factory=list)
    error: str | None = None
    task: asyncio.Task | None = None
    gate: asyncio.Event = field(default_factory=asyncio.Event)

    def info(self, full: bool = False) -> dict:
        payload = {"id":self.id,"spec":self.spec.model_dump(),"state":self.state,
                   "progress":self.progress,"error":self.error,
                   "ranking":ranking(self.runs,self.spec.objective)}
        if full:
            payload["runs"] = self.runs
        else:
            payload["runs"] = [{k:r[k] for k in ("algorithm","seed","mode","summary")} for r in self.runs]
        return payload


class ExperimentManager:
    def __init__(self, store: SQLiteStore | None = None) -> None:
        self.store = store
        self.jobs: dict[str,Job] = {}

    def start(self, spec: ExperimentSpec) -> Job:
        if any(job.state not in TERMINAL for job in self.jobs.values()):
            raise RuntimeError("one experiment at a time")
        while len(self.jobs) >= 4:
            self.jobs.pop(next(iter(self.jobs)))
        job = Job(uuid.uuid4().hex,spec)
        job.gate.set()
        self.jobs[job.id] = job
        job.task = asyncio.create_task(self._run(job),name=f"experiment-{job.id}")
        return job

    async def _run(self, job: Job) -> None:
        try:
            job.state = "running"
            for i in range(job.spec.repetitions):
                for algorithm in job.spec.algorithms:
                    async def progress(frame: dict) -> None:
                        job.progress = {"algorithm":algorithm,"repetition":i+1,**frame}
                        await job.gate.wait()
                        await asyncio.sleep(0)
                    runner = simulate if job.spec.mode == "simulation" else run_http
                    job.runs.append(await runner(job.spec,algorithm,job.spec.seed+i,progress))
            job.state = "completed"
            if self.store:
                try:
                    await asyncio.to_thread(self.store.save_experiment,job.id,job.info(full=True))
                except Exception:
                    logger.exception("experiment persistence failed")
                    job.error = "result available in memory; persistence failed"
        except asyncio.CancelledError:
            job.state = "cancelled"
        except Exception:
            logger.exception("experiment failed")
            job.state,job.error = "failed","experiment execution failed; see server logs"

    def pause(self, job: Job) -> None:
        if job.spec.mode != "simulation" or job.state != "running":
            raise RuntimeError("only running simulations can be paused")
        job.gate.clear()
        job.state = "paused"

    def resume(self, job: Job) -> None:
        if job.state != "paused":
            raise RuntimeError("experiment is not paused")
        job.state = "running"
        job.gate.set()

    async def cancel(self, job: Job) -> None:
        if job.task and job.state not in TERMINAL:
            job.task.cancel()
            await asyncio.gather(job.task,return_exceptions=True)
            job.state = "cancelled"

    async def stop(self) -> None:
        for job in self.jobs.values():
            await self.cancel(job)
