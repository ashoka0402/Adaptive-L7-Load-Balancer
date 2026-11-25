"""Seeded finite-capacity queue simulation using Pulse's real routing strategies."""
from __future__ import annotations

import asyncio
import hashlib
import heapq
import math
import random
import statistics
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.config import AdaptiveConfig, IntelligenceConfig, RequestClassRule
from app.control.controller import AdaptiveController
from app.control.intelligence import percentile
from app.experiments.spec import ExperimentSpec, Fault, NodeSpec, trace
from app.models.backend import Backend, BackendRegistry
from app.routing.adaptive import AdaptiveStrategy
from app.routing.base import LoadBalancingStrategy
from app.routing.latency_aware import LatencyAwareStrategy
from app.routing.least_connections import LeastConnectionsStrategy
from app.routing.round_robin import RoundRobinStrategy
from app.routing.weighted_round_robin import WeightedRoundRobinStrategy
from app.telemetry.pipeline import TelemetryPipeline

Progress = Callable[[dict], Awaitable[None]]
ADAPTIVE = {"adaptive", "predictive", "autopilot", "learning"}


def adaptive_config(name: str, seed: int, spec: ExperimentSpec) -> AdaptiveConfig:
    return AdaptiveConfig(interval=.1, slow_start_seconds=1, intelligence=IntelligenceConfig(
        enabled=name in {"predictive", "autopilot", "learning"},
        autopilot=name in {"autopilot", "learning"}, learning=name == "learning",
        learning_seed=seed, horizon_seconds=.5, policy_cooldown=1,
        p99_target_seconds=spec.slo_ms / 1000, objective=spec.objective,
        rules=[RequestClassRule(name=c, path_prefix="/" + c)
               for c in ("light", "compute", "database", "streaming")],
    ))


def strategy(name: str, controller: AdaptiveController, seed: int) -> LoadBalancingStrategy:
    if name in ADAPTIVE:
        return AdaptiveStrategy(controller, random.Random(seed))
    return {"round_robin": RoundRobinStrategy, "weighted_round_robin": WeightedRoundRobinStrategy,
            "least_connections": LeastConnectionsStrategy,
            "latency_aware": LatencyAwareStrategy}[name]()


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
    ms = node.spec.latency_ms * node.spec.class_multipliers.get(request_class, 1)
    return (ms * rng.uniform(.9, 1.1) + node.extra_ms) / 1000, rng.random() >= node.error_rate


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
    telemetry = TelemetryPipeline()
    controller = AdaptiveController(registry, telemetry, adaptive_config(algorithm, seed, spec))
    router = strategy(algorithm, controller, seed)
    shadows = {name: strategy(name, controller, seed+1) for name in spec.shadow}
    shadow_stats: dict[str, dict] = {name: {"samples": 0, "disagreements": 0,
                                          "estimated_latency_delta_ms_sum": 0.0,
                                          "estimated_error_delta_sum": 0.0}
                                    for name in shadows}
    arrivals = trace(spec, seed)
    events: list[tuple[float, int, int, str, object]] = []
    sequence = 0
    rows: list[dict] = []
    timeline: list[dict] = []
    current = 0.0
    total_active = 0
    completed_ids: set[int] = set()
    policy_changes = 0
    last_weights: dict = {}
    peak_queue = 0

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
        nonlocal total_active
        if rid in completed_ids:
            return
        completed_ids.add(rid)
        elapsed = max(0.0, current-arrival)
        rows.append({"id": rid, "arrival": arrival, "completed": current, "class": cls,
                     "backend": bid, "status": status, "latency_ms": elapsed*1000})
        controller.intelligence.observe_client(elapsed, status == "ok", now=current)
        if bid is not None:
            node = nodes[bid]
            telemetry.record(bid, elapsed, status == "ok")
            controller.intelligence.observe(bid, cls, elapsed, status == "ok",
                                            node.backend.active_connections, now=current)
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

    while events:
        current, _, _, kind, payload = heapq.heappop(events)
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
            selected = await (router.select_for_class(candidates, cls) if isinstance(router, AdaptiveStrategy)
                              else router.select_backend(candidates))
            if selected is None or total_active >= spec.concurrency:
                finish(rid, arrival, cls, None, "dropped")
            else:
                node = nodes[selected.id]
                if rid % spec.shadow_every == 0:
                    for name, shadow in shadows.items():
                        alt = await shadow.select_backend(candidates)
                        if alt:
                            other = nodes[alt.id]
                            stats = shadow_stats[name]
                            stats["samples"] += 1
                            stats["disagreements"] += int(alt.id != selected.id)
                            def estimate(n: Node) -> float:
                                return (n.spec.latency_ms+n.extra_ms)*(1+len(n.queue)/max(1,n.capacity))
                            stats["estimated_latency_delta_ms_sum"] += estimate(other)-estimate(node)
                            stats["estimated_error_delta_sum"] += other.error_rate-node.error_rate
                if len(node.active) >= node.capacity and len(node.queue) >= node.spec.queue_limit:
                    finish(rid, arrival, cls, None, "dropped")
                else:
                    total_active += 1
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
            telemetry.update_external(bid, queue_depth=len(node.queue),
                                      cpu=min(1,len(node.active)/node.capacity), now=current)
        if kind == "tick":
            snapshot = controller.publish(now=current)
            weights = controller.intelligence_status()["weights"]
            if weights != last_weights:
                policy_changes += 1
                last_weights = weights.copy()
            if current <= spec.duration+spec.warmup or len(completed_ids) < len(arrivals):
                frame = {"time": current, "completed": len(rows), "offered": len(arrivals),
                         "nodes": {bid: {"active": len(n.active), "queue": len(n.queue),
                                         "capacity": n.capacity, "down": n.down,
                                         "probability": snapshot.policies[bid].probability if bid in snapshot.policies else 0}
                                   for bid,n in nodes.items()},
                         "successes": sum(r["status"] == "ok" for r in rows),
                         "policy_version": snapshot.version}
                timeline.append(frame)
                await progress(frame)
        if sequence % 256 == 0:
            await asyncio.sleep(0)
        if len(completed_ids) == len(arrivals) and current >= spec.duration+spec.warmup:
            break
    rows.sort(key=lambda row: row["id"])
    elapsed = max(spec.duration, max((r["completed"] for r in rows), default=0)-spec.warmup)
    summary = summarize(rows, spec, elapsed)
    summary.update({"peak_queue": peak_queue, "policy_changes": policy_changes})
    for stats in shadow_stats.values():
        count = max(1, stats["samples"])
        stats["disagreement_rate"] = stats["disagreements"]/count
        stats["estimated_latency_delta_ms"] = stats.pop("estimated_latency_delta_ms_sum")/count
        stats["estimated_error_delta"] = stats.pop("estimated_error_delta_sum")/count
    return {"algorithm": algorithm, "seed": seed, "mode": "simulation", "summary": summary,
            "timeline": timeline, "requests": rows, "shadow": shadow_stats,
            "shadow_note": "Model estimates on the primary policy's state, not measured counterfactuals."}


def ranking(runs: list[dict], objective: str) -> list[dict]:
    result = []
    for algorithm in dict.fromkeys(r["algorithm"] for r in runs):
        samples = [r["summary"] for r in runs if r["algorithm"] == algorithm]
        metric = {"latency": "p99_ms", "throughput": "successful_rps",
                  "reliability": "error_rate", "balanced": "slo_violation_rate"}[objective]
        values = [s[metric] for s in samples]
        result.append({"algorithm": algorithm, "metric": metric,
                       "mean": statistics.mean(values), "repetitions": len(values),
                       "sample_stdev": statistics.stdev(values) if len(values)>1 else None})
    return sorted(result, key=lambda r: r["mean"], reverse=objective=="throughput")
