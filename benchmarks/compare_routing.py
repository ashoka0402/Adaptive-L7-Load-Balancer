"""Compare the original latency-aware strategy with Pulse under one scenario."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.experiments import RoutingSimulation


async def compare(seed: int) -> dict[str, object]:
    simulator = RoutingSimulation()
    original = await simulator.run("latency_aware", seed=seed)
    pulse = await simulator.run("adaptive", seed=seed)
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "seed": seed,
        "scenario": [list(phase) for phase in simulator.phases],
        "original": original.to_dict(),
        "pulse": pulse.to_dict(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(asyncio.run(compare(args.seed)), indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
