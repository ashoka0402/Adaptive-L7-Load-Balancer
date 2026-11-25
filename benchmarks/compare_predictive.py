"""Compare reactive and predictive routing under the same synthetic trace.

This is a simulation, not real HTTP throughput evidence. The existing simulator
observes assigned service outcomes at dispatch; results are illustrative.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.experiments import RoutingSimulation


async def compare(seeds: list[int]) -> dict:
    simulator = RoutingSimulation()
    runs = []
    for seed in seeds:
        reactive = await simulator.run("adaptive", seed=seed)
        predictive = await simulator.run("predictive", seed=seed)
        runs.append({"seed": seed, "reactive": reactive.to_dict(),
                     "predictive": predictive.to_dict()})
    return {"mode": "simulation", "scenario": simulator.phases, "runs": runs,
            "limitation": "Synthetic dispatch-time outcomes; not an HTTP benchmark."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", nargs="+", type=int, default=[7, 11, 23])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = json.dumps(asyncio.run(compare(args.seeds)), indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
