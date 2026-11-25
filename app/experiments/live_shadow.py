"""Opt-in sampled decision-only shadowing; never performs HTTP or backend calls."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.config import ShadowConfig
from app.models.backend import Backend
from app.routing.base import LoadBalancingStrategy
from app.routing.latency_aware import LatencyAwareStrategy
from app.routing.least_connections import LeastConnectionsStrategy
from app.routing.round_robin import RoundRobinStrategy
from app.routing.weighted_round_robin import WeightedRoundRobinStrategy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sample:
    chosen: str
    candidates: tuple[Backend, ...]


class LiveShadow:
    """Copy only sampled routing state, with a bounded nonblocking queue."""

    def __init__(self, config: ShadowConfig) -> None:
        self.config = config
        self.queue: asyncio.Queue[Sample] = asyncio.Queue(maxsize=config.queue_limit)
        self.task: asyncio.Task | None = None
        self.seen = 0
        self.dropped = 0
        self.routers: dict[str, LoadBalancingStrategy] = {}
        factories = {
            "round_robin": RoundRobinStrategy(), "weighted_round_robin": WeightedRoundRobinStrategy(),
            "least_connections": LeastConnectionsStrategy(), "latency_aware": LatencyAwareStrategy(),
        }
        for name in config.algorithms:
            self.routers[name] = factories[name]
        self.stats: dict[str, dict[str, float]] = {
            name: {"samples": 0, "disagreements": 0, "estimated_latency_delta_ms_sum": 0,
                   "estimated_error_delta_sum": 0} for name in self.routers
        }

    def start(self) -> None:
        if self.config.enabled and self.routers:
            self.task = asyncio.create_task(self._worker(), name="pulse-shadow")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    def observe(self, selected: Backend, candidates: list[Backend]) -> None:
        if not self.config.enabled or not self.routers:
            return
        self.seen += 1
        if self.seen % self.config.sample_every:
            return
        if self.queue.full():
            self.dropped += 1
            return
        # Construct routing-only copies. Do not retain addresses, headers, paths,
        # bodies, request IDs, or live mutable Backend references.
        copies = tuple(Backend(
            id=b.id, host="shadow", port=1, weight=b.weight, capacity=b.capacity,
            active_connections=b.active_connections, average_latency=b.average_latency,
            total_requests=b.total_requests, successful_requests=b.successful_requests,
            failed_requests=b.failed_requests,
        ) for b in candidates)
        self.queue.put_nowait(Sample(selected.id, copies))

    async def evaluate(self, sample: Sample) -> None:
        candidates = list(sample.candidates)
        primary = next((b for b in candidates if b.id == sample.chosen), None)
        if primary is None:
            return
        for name, router in self.routers.items():
            alt = await router.select_backend(candidates)
            if alt is None:
                continue
            stats = self.stats[name]
            stats["samples"] += 1
            stats["disagreements"] += int(alt.id != primary.id)
            # Observational proxies, not causal counterfactual outcomes.
            stats["estimated_latency_delta_ms_sum"] += 1000 * (
                (alt.average_latency or .05) - (primary.average_latency or .05)
            )
            stats["estimated_error_delta_sum"] += (
                alt.failed_requests / max(1, alt.total_requests)
                - primary.failed_requests / max(1, primary.total_requests)
            )

    async def _worker(self) -> None:
        while True:
            sample = await self.queue.get()
            try:
                await self.evaluate(sample)
            except Exception:
                logger.exception("shadow evaluation failed")
            finally:
                self.queue.task_done()

    def report(self) -> dict:
        candidates = {}
        for name, stats in self.stats.items():
            count = stats["samples"]
            candidates[name] = {
                "samples": count, "disagreements": stats["disagreements"],
                "disagreement_rate": stats["disagreements"] / count if count else None,
                "estimated_latency_delta_ms": stats["estimated_latency_delta_ms_sum"] / count if count else None,
                "estimated_error_delta": stats["estimated_error_delta_sum"] / count if count else None,
            }
        return {"enabled": self.config.enabled, "observed_decisions": self.seen,
                "dropped_samples": self.dropped, "candidates": candidates,
                "note": "Sampled decision estimates using observed backend averages; no duplicate HTTP requests."}
