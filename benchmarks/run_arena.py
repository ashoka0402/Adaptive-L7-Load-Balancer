"""Run reproducible Arena experiments locally; output actual results only."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.experiments.manager import ExperimentManager
from app.experiments.spec import ExperimentSpec


async def run(spec: ExperimentSpec) -> dict:
    manager=ExperimentManager()
    job=manager.start(spec)
    if job.task:
        await job.task
    if job.state!="completed":
        raise RuntimeError(job.error or job.state)
    return job.info(full=True)


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--spec",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    spec=ExperimentSpec.model_validate_json(args.spec.read_text()) if args.spec else ExperimentSpec(
        rps=100,duration=2,repetitions=3,algorithms=["round_robin","weighted_round_robin","least_connections","latency_aware"]
    )
    result=json.dumps(asyncio.run(run(spec)),indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(result+"\n",encoding="utf-8")
    else:
        print(result)


if __name__=="__main__":
    main()
