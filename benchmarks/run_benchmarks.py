"""Reproducible command-line benchmark for a running Pulse instance."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.traffic import AsyncTrafficGenerator


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8080/api/test")
    p.add_argument("--concurrency", type=int, default=50)
    p.add_argument("--duration", type=float, default=10.0)
    p.add_argument("--warmup", type=float, default=2.0)
    p.add_argument("--label", default="unlabeled")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    generator = AsyncTrafficGenerator(concurrency=args.concurrency)
    measurement = asyncio.run(generator.run(args.url, duration=args.duration, warmup=args.warmup))
    result = {
        "label": args.label,
        "timestamp": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
        },
        "workload": {
            "url": args.url,
            "concurrency": args.concurrency,
            "duration_seconds": args.duration,
            "warmup_seconds": args.warmup,
        },
        "measurement": measurement.to_dict(),
    }
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
