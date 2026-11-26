"""Independent-process real HTTP evidence: Round Robin vs adaptive EWMA.

Run: python -m benchmarks.adaptive_proof --output reports/adaptive-proof.json
No injected sleeps or fabricated measurements in application workload.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import httpx

from app.fastapi_lb.telemetry import percentile


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


async def ready(client: httpx.AsyncClient, url: str, process: subprocess.Popen) -> None:
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError(f"Process exited: {process.returncode}")
        try:
            if (await client.get(url, timeout=.5)).is_success:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(.1)
    raise RuntimeError(f"Readiness timed out: {url}")


def shutdown(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


async def phase(client: httpx.AsyncClient, url: str, duration: int, rate: int) -> dict:
    start = time.monotonic()
    records: list[dict] = []
    pending: set[asyncio.Task] = set()
    dropped = 0

    async def one(index: int) -> None:
        began = time.monotonic()
        record = {"index":index,"t":round(began-start,3),"status":0,"backend":None}
        try:
            response = await client.get(url)
            record["status"] = response.status_code
            if response.is_success:
                record["backend"] = response.headers.get("x-backend") or response.json().get("backend")
            for header,key in (("x-pulse-routing-us","routing_us"),
                               ("x-pulse-upstream-headers-ms","upstream_headers_ms")):
                if header in response.headers:
                    record[key] = float(response.headers[header])
        except (httpx.HTTPError, ValueError):
            pass
        record["ms"] = round((time.monotonic()-began)*1000,3)
        records.append(record)

    for i in range(duration*rate):
        await asyncio.sleep(max(0,start+i/rate-time.monotonic()))
        pending = {p for p in pending if not p.done()}
        if len(pending) >= 64:
            dropped += 1
        else:
            pending.add(asyncio.create_task(one(i)))
    await asyncio.gather(*pending)
    elapsed = max(.001,time.monotonic()-start)
    good = [r for r in records if 200 <= r["status"] < 300]
    counts = Counter(r["backend"] for r in good)
    bins = []
    for second in range(duration):
        group = [r for r in records if second <= r["t"] < second+1]
        ok = [r for r in group if 200 <= r["status"] < 300]
        bins.append({"second":second,"success":len(ok),
                     "p95_ms":percentile([r["ms"] for r in group],.95),
                     "backend_counts":dict(Counter(r["backend"] for r in ok))})
    return {"offered":duration*rate,"completed":len(records),"success":len(good),
            "dropped":dropped,"goodput_rps":round(len(good)/elapsed,2),
            "p50_ms":percentile([r["ms"] for r in records],.5),
            "p95_ms":percentile([r["ms"] for r in records],.95),
            "p99_ms":percentile([r["ms"] for r in records],.99),
            "backend_counts":dict(counts),
            "routing_p50_us":percentile([r["routing_us"] for r in records if "routing_us" in r],.5),
            "upstream_headers_p50_ms":percentile([r["upstream_headers_ms"] for r in records if "upstream_headers_ms" in r],.5),
            "bins":bins,"requests":sorted(records,key=lambda r:r["index"])}


def redistribution(normal: dict, degraded: dict, recovery: dict) -> dict:
    def share(data: dict) -> float:
        return data["backend_counts"].get("B",0)/max(1,sum(data["backend_counts"].values()))
    baseline = share(normal)
    slowed = share(degraded)
    def first_two(data: dict, predicate) -> int | None:
        streak = 0
        for entry in data["bins"]:
            streak = streak+1 if entry["success"] and predicate(share(entry)) else 0
            if streak >= 2:
                return entry["second"]-1
        return None
    adaptation = first_two(degraded,lambda s: s < baseline*.5) if baseline >= .05 else None
    recovery_at = first_two(recovery,lambda s: s >= baseline*.5) if adaptation is not None else None
    return {"B_normal_share":round(baseline,4),"B_degraded_share":round(slowed,4),
            "B_recovery_share":round(share(recovery),4),
            "adaptation_first_two_low_share_bins_s":adaptation,
            "recovery_first_two_restored_share_bins_s":recovery_at,
            "definition":"two nonempty consecutive one-second bins; threshold is half B's normal successful-response share; null means not observed or not applicable"}


async def compare(args) -> dict:
    processes = []
    ports: set[int] = set()
    while len(ports) < 4:
        ports.add(free_port())
    a,b,c,proxy_port = sorted(ports)
    token = secrets.token_hex(24)
    env = dict(os.environ, PULSE_BENCHMARK_TOKEN=token)
    origins = {name:f"http://127.0.0.1:{port}" for name,port in zip("ABC",(a,b,c))}
    proxy_url = f"http://127.0.0.1:{proxy_port}"
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp)/"routes.json"
        config.write_text(json.dumps({"routes":[
            {"prefix":"/","strategy":"round_robin","backends":[
                {"id":k,"url":v} for k,v in origins.items()]},
            {"prefix":"/direct","strategy":"round_robin",
             "backends":[{"id":"A-direct","url":origins["A"]}]}
        ]}),encoding="utf-8")
        try:
            for name,port in zip("ABC",(a,b,c)):
                processes.append(subprocess.Popen(
                    [sys.executable,"-m","backends.workload","--id",name,"--port",str(port)],
                    env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            async with httpx.AsyncClient(timeout=5,trust_env=False) as client:
                for i,name in enumerate("ABC"):
                    await ready(client,origins[name]+"/health",processes[i])
                async def complexity(value: int) -> None:
                    response = await client.post(origins["B"]+"/__benchmark/complexity",
                        json={"iterations":value},headers={"X-Benchmark-Token":token})
                    response.raise_for_status()
                for repetition in range(args.repetitions):
                    # Counterbalance order; fresh proxy state on every run.
                    order = ("round_robin","latency_aware") if repetition%2==0 else ("latency_aware","round_robin")
                    for strategy in order:
                        await complexity(1000)
                        process = subprocess.Popen([sys.executable,"-m","app.main","--config",str(config),
                            "--port",str(proxy_port),"--strategy",strategy],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                        processes.append(process)
                        try:
                            await ready(client,proxy_url+"/pulse/api/console",process)
                            # Warm network connections and establish EWMA before the measured phases.
                            await phase(client,proxy_url+"/api/items/1",2,args.rate)
                            before = await phase(client,proxy_url+"/api/items/1",args.seconds,args.rate)
                            before["backend_state"] = (await client.get(proxy_url+"/pulse/api/console")).json()["backends"]
                            await complexity(args.complexity)
                            degraded = await phase(client,proxy_url+"/api/items/1",args.seconds,args.rate)
                            degraded["backend_state"] = (await client.get(proxy_url+"/pulse/api/console")).json()["backends"]
                            await complexity(1000)
                            recovery = await phase(client,proxy_url+"/api/items/1",args.seconds,args.rate)
                            recovery["backend_state"] = (await client.get(proxy_url+"/pulse/api/console")).json()["backends"]
                            direct = await phase(client,origins["A"]+"/direct",2,args.rate)
                            through = await phase(client,proxy_url+"/direct",2,args.rate)
                            snapshot = (await client.get(proxy_url+"/pulse/api/console")).json()
                            results.append({"repetition":repetition+1,"strategy":strategy,
                                "normal":before,"degraded":degraded,"recovery":recovery,
                                "redistribution":redistribution(before,degraded,recovery),
                                "direct_baseline":direct,"single_backend_proxy":through,
                                "pids":{"proxy":process.pid,"origins":[p.pid for p in processes[:3]],"generator":os.getpid()},
                                "final_timing":snapshot["timing"]})
                        finally:
                            shutdown(process)
                return {"environment":{"python":sys.version,"platform":platform.platform(),
                        "cpu_count":os.cpu_count()},"workload":{"rate":args.rate,"phase_seconds":args.seconds,
                        "repetitions":args.repetitions,"normal_pbkdf2_iterations":1000,
                        "degraded_pbkdf2_iterations":args.complexity,
                        "method":"paced real HTTP, SQLite reads and PBKDF2 computation; B alone changes work"},
                        "runs":results}
        finally:
            for process in reversed(processes):
                shutdown(process)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output",type=Path,default=Path("reports/adaptive-proof.json"))
    p.add_argument("--rate",type=int,default=40)
    p.add_argument("--seconds",type=int,default=12)
    p.add_argument("--repetitions",type=int,default=3)
    p.add_argument("--complexity",type=int,default=200000)
    a=p.parse_args()
    if not 1 <= a.rate <= 100 or not 2 <= a.seconds <= 60 or not 1 <= a.repetitions <= 5 or not 1000 <= a.complexity <= 300000:
        p.error("Workload exceeds bounded local experiment limits")
    report=asyncio.run(compare(a))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    for run in report["runs"]:
        print(run["strategy"],run["repetition"],
              [(p,run[p]["p95_ms"],run[p]["backend_counts"]) for p in ("normal","degraded","recovery")])
    print(f"Raw measured evidence: {a.output}")


if __name__ == "__main__":
    main()
