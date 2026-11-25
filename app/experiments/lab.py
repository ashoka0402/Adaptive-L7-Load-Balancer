"""Seeded finite-capacity queue simulation using Pulse's real routing strategies."""
from __future__ import annotations

import asyncio
import hashlib
import heapq
import math
import random
import statistics
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.experiments.spec import ExperimentSpec, Fault, NodeSpec, trace
from app.models.backend import Backend, BackendRegistry
from app.routing.base import LoadBalancingStrategy
from app.routing.latency_aware import LatencyAwareStrategy
from app.routing.least_connections import LeastConnectionsStrategy
from app.routing.round_robin import RoundRobinStrategy
from app.routing.weighted_round_robin import WeightedRoundRobinStrategy

Progress = Callable[[dict], Awaitable[None]]


def strategy(name: str) -> LoadBalancingStrategy:
    factories: dict[str, Callable[[], LoadBalancingStrategy]] = {
        "round_robin": RoundRobinStrategy, "weighted_round_robin": WeightedRoundRobinStrategy,
        "least_connections": LeastConnectionsStrategy, "latency_aware": LatencyAwareStrategy,
    }
    return factories[name]()


@dataclass
class Node:
    spec: NodeSpec
    backend: Backend
    capacity: int
    down: bool = False
    extra_ms: float = 0.0
    error_rate: float = 0.0
    queue: deque[tuple[int, float, str]] = field(default_factory=deque)
    active: dict[int, tuple[float, str, bool]] = field(default_factory=dict)

    def fault(self, fault: Fault) -> None:
        if fault.kind == "down":
            self.down = True
        elif fault.kind == "restore":
            self.down = False
            self.extra_ms = 0
            self.error_rate = self.spec.error_rate
            self.capacity = self.spec.capacity
        elif fault.kind in {"latency", "network"}:
            self.extra_ms = fault.value
        elif fault.kind == "error":
            self.error_rate = fault.value
        elif fault.kind == "capacity":
            self.capacity = int(fault.value)
        elif fault.kind == "resource":
            self.capacity = max(1, int(self.spec.capacity * fault.value))
        self.backend.enabled = not self.down
        self.backend.capacity = self.capacity


def service(node: Node, seed: int, request_id: int, request_class: str) -> tuple[float, bool]:
    # Independent random numbers for each request/backend pair across algorithms.
    key = f"{seed}:{request_id}:{node.spec.id}".encode()
    rng = random.Random(int.from_bytes(hashlib.sha256(key).digest()[:8], "big"))
    ms = node.spec.latency_ms * next((value for name, value in node.spec.class_multipliers.items() if name == request_class), 1.0)
    return (ms * rng.uniform(.9, 1.1) + node.extra_ms) / 1000, rng.random() >= node.error_rate


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def summarize(rows: list[dict], spec: ExperimentSpec, elapsed: float) -> dict:
    measured = [r for r in rows if r["arrival"] >= spec.warmup]
    latencies = [r["latency_ms"] for r in measured]
    success = sum(r["status"] == "ok" for r in measured)
    counts = Counter(r["status"] for r in measured)
    choices = Counter(r["backend"] for r in measured if r["backend"] is not None)
    offered = len(measured)
    load = [choices.get(n.id, 0) for n in spec.nodes]
    fairness = sum(load)**2 / (len(load) * sum(x*x for x in load)) if any(load) else 0
    return {"offered": offered, "successful": success, "errors": offered-success,
            "dropped": counts["dropped"], "timeouts": counts["timeout"],
            "offered_rps": offered / max(.000001, elapsed),
            "successful_rps": success / max(.000001, elapsed),
            "error_rate": (offered-success)/max(1, offered),
            "p50_ms": percentile(latencies, .5), "p95_ms": percentile(latencies, .95),
            "p99_ms": percentile(latencies, .99),
            "slo_violation_rate": sum(r["status"] != "ok" or r["latency_ms"] > spec.slo_ms
                                      for r in measured)/max(1, offered),
            "distribution": dict(choices), "jain_fairness": fairness,
            "elapsed_seconds": elapsed}


async def simulate(spec: ExperimentSpec, algorithm: str, seed: int, progress: Progress) -> dict:
    registry = BackendRegistry()
    nodes: dict[str, Node] = {}
    for n in spec.nodes:
        b = Backend(n.id, "simulation", 1, capacity=n.capacity, weight=n.weight, registered_at=0)
        registry.add(b)
        nodes[n.id] = Node(n, b, n.capacity, error_rate=n.error_rate)
    router = strategy(algorithm)
    arrivals = trace(spec, seed)
    events: list[tuple[float, int, int, str, object]] = []
    sequence = 0
    rows: list[dict] = []
    timeline: list[dict] = []
    current = 0.0
    total_active = 0
    completed_ids: set[int] = set()
    busy_area = {n.id: 0.0 for n in spec.nodes}
    queue_area = {n.id: 0.0 for n in spec.nodes}
    capacity_area = {n.id: 0.0 for n in spec.nodes}
    peak_queue = 0
    successes = 0
    routed: Counter[str] = Counter()

    def event(t: float, priority: int, kind: str, payload: object) -> None:
        nonlocal sequence
        sequence += 1
        heapq.heappush(events, (t, priority, sequence, kind, payload))

    for i, (arrival, cls) in enumerate(arrivals):
        event(arrival, 3, "arrival", (i, arrival, cls))
    for fault in spec.faults:
        event(fault.at, 0, "fault", fault)
    for tick in range(math.ceil((spec.warmup+spec.duration+spec.timeout_ms/1000)/.1)+1):
        event(tick*.1, 2, "tick", None)

    def finish(rid: int, arrival: float, cls: str, bid: str | None, status: str) -> None:
        nonlocal total_active, successes
        if rid in completed_ids:
            return
        completed_ids.add(rid)
        successes += int(status == "ok")
        elapsed = max(0.0, current-arrival)
        rows.append({"id": rid, "arrival": arrival, "completed": current, "class": cls,
                     "backend": bid, "status": status, "latency_ms": elapsed*1000})
        if bid is not None:
            node = nodes[bid]
            if status == "ok":
                node.backend.record_success(elapsed)
            else:
                node.backend.record_failure()
            total_active -= 1

    def pump(node: Node) -> None:
        while not node.down and node.queue and len(node.active) < node.capacity:
            rid, arrival, cls = node.queue.popleft()
            if rid in completed_ids:
                continue
            duration, ok = service(node, seed, rid, cls)
            node.active[rid] = (arrival, cls, ok)
            event(current+duration, 1, "complete", (node.spec.id, rid))

    processed = 0
    while events:
        event_time, _, _, kind, payload = heapq.heappop(events)
        interval = max(0.0, event_time-max(current,spec.warmup))
        for bid,node in nodes.items():
            busy_area[bid] += len(node.active)*interval
            queue_area[bid] += len(node.queue)*interval
            capacity_area[bid] += node.capacity*interval
        current = event_time
        processed += 1
        if kind == "fault":
            assert isinstance(payload, Fault)
            node = nodes[payload.backend]
            node.fault(payload)
            if node.down:
                for rid, (arrival, cls, _) in list(node.active.items()):
                    finish(rid, arrival, cls, node.spec.id, "error")
                node.active.clear()
                while node.queue:
                    rid, arrival, cls = node.queue.popleft()
                    finish(rid, arrival, cls, node.spec.id, "error")
            pump(node)
        elif kind == "arrival":
            rid, arrival, cls = payload  # type: ignore[misc]
            candidates = [n.backend for n in nodes.values() if not n.down]
            selected = await router.select_backend(candidates)
            if selected is None or total_active >= spec.concurrency:
                finish(rid, arrival, cls, None, "dropped")
            else:
                node = nodes[selected.id]
                if len(node.active) >= node.capacity and len(node.queue) >= node.spec.queue_limit:
                    finish(rid, arrival, cls, None, "dropped")
                else:
                    total_active += 1
                    routed[node.spec.id] += 1
                    node.queue.append((rid, arrival, cls))
                    event(arrival+spec.timeout_ms/1000, 1, "timeout", (node.spec.id,rid,arrival,cls))
                    pump(node)
        elif kind == "complete":
            bid, rid = payload  # type: ignore[misc]
            node = nodes[bid]
            if rid in node.active:
                arrival, cls, ok = node.active.pop(rid)
                finish(rid, arrival, cls, bid, "ok" if ok else "error")
                pump(node)
        elif kind == "timeout":
            bid, rid, arrival, cls = payload  # type: ignore[misc]
            if rid not in completed_ids:
                node = nodes[bid]
                node.active.pop(rid, None)
                node.queue = deque(item for item in node.queue if item[0] != rid)
                finish(rid, arrival, cls, bid, "timeout")
                pump(node)
        for bid, node in nodes.items():
            node.backend.active_connections = len(node.active) + len(node.queue)
            peak_queue = max(peak_queue, len(node.queue))
        if kind == "tick":
            if current <= spec.duration+spec.warmup or len(completed_ids) < len(arrivals):
                frame = {"time": current, "completed": len(rows), "offered": len(arrivals),
                         "nodes": {bid: {"active": len(n.active), "queue": len(n.queue),
                                         "capacity": n.capacity, "down": n.down, "routed": routed[bid]}
                                   for bid,n in nodes.items()},
                         "successes": successes}
                timeline.append(frame)
                await progress(frame)
        if processed % 256 == 0:
            await asyncio.sleep(0)
        if len(completed_ids) == len(arrivals) and current >= spec.duration+spec.warmup:
            break
    rows.sort(key=lambda row: row["id"])
    elapsed = max(spec.duration, max((r["completed"] for r in rows), default=0)-spec.warmup)
    summary = summarize(rows, spec, elapsed)
    summary.update({"peak_queue": peak_queue,
                    "mean_backend_utilization": {bid: busy_area[bid]/max(.000001,capacity_area[bid]) for bid in nodes},
                    "mean_backend_queue": {bid: queue_area[bid]/max(.000001,current-spec.warmup) for bid in nodes}})
    recovery = []
    for fault in spec.faults:
        if fault.kind == "restore":
            first = next((r for r in sorted(rows,key=lambda row: row["completed"])
                          if r["backend"] == fault.backend and r["status"] == "ok"
                          and r["arrival"] >= fault.at),None)
            recovery.append({"backend":fault.backend,"restore_at":fault.at,
                             "first_success_seconds":first["completed"]-fault.at if first else None})
    summary["recovery"] = recovery
    return {"algorithm": algorithm, "seed": seed, "mode": "simulation", "summary": summary,
            "timeline": timeline, "requests": rows}


def ranking(runs: list[dict], objective: str) -> list[dict]:
    result = []
    for algorithm in dict.fromkeys(r["algorithm"] for r in runs):
        samples = [r["summary"] for r in runs if r["algorithm"] == algorithm]
        metric = {"latency": "p99_ms", "throughput": "successful_rps",
                  "reliability": "error_rate", "balanced": "slo_violation_rate"}[objective]
        values = [s[metric] for s in samples]
        deviation = statistics.stdev(values) if len(values)>1 else None
        # Two-sided Student t interval; n is bounded to 1..5. No interval for n=1.
        t_critical = {2:12.706,3:4.303,4:3.182,5:2.776}.get(len(values))
        mean = statistics.mean(values)
        margin = t_critical*deviation/math.sqrt(len(values)) if t_critical and deviation is not None else None
        result.append({"algorithm": algorithm, "metric": metric,
                       "mean": mean, "repetitions": len(values), "sample_stdev": deviation,
                       "ci95": [mean-margin,mean+margin] if margin is not None else None})
    return sorted(result, key=lambda r: r["mean"], reverse=objective=="throughput")
