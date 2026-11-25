"""Compare original and current proxy on the same runner and local backend fleet.

This is a bounded closed-loop HTTP measurement, not an open-loop capacity claim.
Run only on a trusted historical checkout. Never sends traffic off loopback.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import socket
import statistics
import sys
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web

from app.experiments.http_lab import listener
from app.traffic import AsyncTrafficGenerator


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def wait_ready(port: int, process: asyncio.subprocess.Process) -> None:
    async with asyncio.timeout(15):
        while process.returncode is None:
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                await asyncio.sleep(.05)
    raise RuntimeError("proxy exited before becoming ready")


async def measure(root: Path, strategy: str, configs: list[dict], duration: float,
                  warmup: float, concurrency: int, work: Path) -> dict:
    port = available_port()
    token = secrets.token_hex(24)
    config = {
        "server": {"host": "127.0.0.1", "port": port, "admin_token": token},
        "backends": configs, "load_balancing": {"strategy": strategy},
        "persistence": {"enabled": False},
        "logging": {"level": "WARNING"},
        "backpressure": {"max_concurrent_requests": 100, "max_queue_size": 50},
        "adaptive": {"slow_start_seconds": 0, "intelligence": {"enabled": True, "autopilot": True}},
    }
    path = work / "benchmark-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    env = {**os.environ, "PULSE_ADMIN_TOKEN": token, "PYTHONPATH": str(root)}
    log = work / ("proxy-" + strategy + ".log")
    with log.open("wb") as output:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.main", "-c", str(path), cwd=root,
            env=env, stdout=output, stderr=output,
        )
        try:
            await wait_ready(port, process)
            result = await AsyncTrafficGenerator(concurrency=concurrency).run(
                f"http://127.0.0.1:{port}/work", duration=duration, warmup=warmup
            )
            data = result.to_dict()
            data["successful_rps"] = result.successful / result.elapsed_seconds
            if not result.successful:
                raise RuntimeError("no successful HTTP responses; inspect " + str(log))
            return data
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await process.wait()


async def compare(args: argparse.Namespace) -> dict:
    async with AsyncExitStack() as stack:
        configs = []
        for i, delay in enumerate((.005, .015, .03)):
            async def serve(request: web.Request, delay: float = delay) -> web.Response:
                if request.path != "/health":
                    await asyncio.sleep(delay)
                return web.Response(text="pulse benchmark")
            app = web.Application()
            app.router.add_get("/{path:.*}", serve)
            port = await listener(app, stack)
            configs.append({"id": f"b{i+1}", "host": "127.0.0.1", "port": port, "weight": 1})
        variants = [
            ("original-ewma", args.baseline.resolve(), "latency_aware"),
            ("current-ewma", Path.cwd(), "latency_aware"),
            ("pulse-adaptive", Path.cwd(), "adaptive"),
        ]
        rows = []
        for repetition in range(args.repetitions):
            # Rotate order to reduce systematic warm-machine bias.
            for label, root, strategy in variants[repetition % 3:] + variants[:repetition % 3]:
                result = await measure(root, strategy, configs, args.duration, args.warmup,
                                       args.concurrency, args.output.parent.resolve())
                rows.append({"variant": label, "repetition": repetition+1, **result})
        metrics = ("successful_rps", "p99_ms", "errors")
        summaries = {label: {metric: statistics.median(float(r[metric]) for r in rows
                     if r["variant"] == label) for metric in metrics}
                     for label, _, _ in variants}
        return {
            "mode": "measured-http", "timestamp": datetime.now(UTC).isoformat(),
            "environment": {"platform": platform.platform(), "python": sys.version,
                            "cpu_count": os.cpu_count(), "commit": os.getenv("GITHUB_SHA")},
            "baseline_commit": "027d578",
            "workload": {"concurrency": args.concurrency, "duration_seconds": args.duration,
                         "warmup_seconds": args.warmup, "repetitions": args.repetitions,
                         "backend_service_delays_ms": [5,15,30]},
            "runs": rows, "median": summaries,
            "limitations": "Single machine, synthetic service delays, closed-loop traffic, separate proxy processes; no universal throughput claim.",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--warmup", type=float, default=1)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("reports/release-comparison.json"))
    args = parser.parse_args()
    if not 0 < args.duration <= 60 or not 0 <= args.warmup <= 20 or not 1 <= args.concurrency <= 100 or not 1 <= args.repetitions <= 5:
        parser.error("workload exceeds bounded release benchmark limits")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(compare(args))
    args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report["median"], indent=2))


if __name__ == "__main__":
    main()
