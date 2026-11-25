"""Strict experiment specifications. No external addresses or arbitrary URLs."""
from __future__ import annotations

import random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Algorithm = Literal["round_robin", "weighted_round_robin", "least_connections",
                    "latency_aware"]
ALGORITHMS = ("round_robin", "weighted_round_robin", "least_connections",
              "latency_aware")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class NodeSpec(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    capacity: int = Field(default=8, ge=1, le=64)
    queue_limit: int = Field(default=32, ge=0, le=128)
    latency_ms: float = Field(default=15, ge=1, le=2000)
    error_rate: float = Field(default=0, ge=0, le=1)
    weight: int = Field(default=1, ge=1, le=100)
    class_multipliers: dict[Literal["light", "compute", "database", "streaming"], float] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def valid_multipliers(self) -> NodeSpec:
        if any(not .1 <= value <= 10 for value in self.class_multipliers.values()):
            raise ValueError("class multipliers must be between 0.1 and 10")
        return self


class Fault(StrictModel):
    at: float = Field(ge=0, le=120)
    backend: str
    kind: Literal["down", "restore", "latency", "error", "capacity", "network", "resource"]
    value: float = Field(default=0, ge=0, le=2000)

    @model_validator(mode="after")
    def valid_value(self) -> Fault:
        if self.kind == "error" and self.value > 1:
            raise ValueError("error probability must be <= 1")
        if self.kind == "capacity" and (not self.value.is_integer() or not 1 <= self.value <= 64):
            raise ValueError("capacity must be an integer between 1 and 64")
        if self.kind == "resource" and not 0 < self.value <= 1:
            raise ValueError("resource capacity factor must be in (0,1]")
        return self


def default_nodes() -> list[NodeSpec]:
    return [NodeSpec(id="b1", capacity=6, latency_ms=10),
            NodeSpec(id="b2", capacity=10, latency_ms=20, weight=2),
            NodeSpec(id="b3", capacity=8, latency_ms=30)]


def default_algorithms() -> list[Algorithm]:
    return ["round_robin", "latency_aware"]


class ExperimentSpec(StrictModel):
    mode: Literal["simulation", "http"] = "simulation"
    algorithms: list[Algorithm] = Field(default_factory=default_algorithms, min_length=1, max_length=8)
    nodes: list[NodeSpec] = Field(default_factory=default_nodes, min_length=1, max_length=8)
    seed: int = Field(default=7, ge=0, le=2**31-1)
    repetitions: int = Field(default=1, ge=1, le=5)
    rps: int = Field(default=200, ge=1, le=5000)
    duration: float = Field(default=5, gt=0, le=120)
    warmup: float = Field(default=0, ge=0, le=20)
    total_requests: int | None = Field(default=None, ge=1, le=20000)
    concurrency: int = Field(default=100, ge=1, le=500)
    pattern: Literal["steady", "burst", "ramp", "periodic", "mixed", "custom"] = "steady"
    rate_schedule: list[tuple[float, float]] = Field(default_factory=list, max_length=32)
    faults: list[Fault] = Field(default_factory=list, max_length=32)
    slo_ms: float = Field(default=100, gt=0, le=60000)
    timeout_ms: float = Field(default=2000, ge=10, le=10000)
    objective: Literal["latency", "throughput", "reliability", "balanced"] = "balanced"

    @model_validator(mode="after")
    def validate_limits(self) -> ExperimentSpec:
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes) or len(set(self.algorithms)) != len(self.algorithms):
            raise ValueError("duplicate backend or algorithm")
        if len(set(self.shadow)) != len(self.shadow):
            raise ValueError("duplicate shadow algorithm")
        if any(f.backend not in ids or f.at > self.duration + self.warmup for f in self.faults):
            raise ValueError("fault target or time outside experiment")
        if self.pattern == "custom":
            if not self.rate_schedule or self.rate_schedule[0][0] != 0:
                raise ValueError("custom schedule must start at zero")
            if any(not 0 <= t <= self.duration + self.warmup or not .1 <= m <= 4
                   for t, m in self.rate_schedule):
                raise ValueError("invalid custom schedule")
            if sorted({t for t, _ in self.rate_schedule}) != [t for t, _ in self.rate_schedule]:
                raise ValueError("schedule times must be unique and ascending")
        bound = min(self.total_requests or 20000, int((self.duration + self.warmup) * self.rps * 4) + 1)
        if bound * self.repetitions * len(self.algorithms) > 80000:
            raise ValueError("experiment work budget exceeded")
        if self.mode == "http" and (self.rps > 200 or self.duration + self.warmup > 20
                                   or self.concurrency > 64 or bound > 4000
                                   or self.repetitions * len(self.algorithms) > 8):
            raise ValueError("HTTP mode exceeds isolated benchmark limits")
        return self


def trace(spec: ExperimentSpec, seed: int) -> list[tuple[float, str]]:
    rng = random.Random(seed)
    result: list[tuple[float, str]] = []
    end = spec.duration + spec.warmup
    now = 0.0
    while round(now, 9) < round(end, 9) and len(result) < (spec.total_requests or 20000):
        request_class = rng.choice(["light", "compute", "database", "streaming"]) if spec.pattern == "mixed" else "light"
        result.append((round(now, 9), request_class))
        fraction = now / end
        factor = 1.0
        if spec.pattern == "burst":
            factor = 4 if .3 <= fraction < .5 else 1
        elif spec.pattern == "ramp":
            factor = .25 + 1.75 * fraction
        elif spec.pattern == "periodic":
            factor = 2 if int(fraction * 8) % 2 else .5
        elif spec.pattern == "custom":
            factor = next(m for t, m in reversed(spec.rate_schedule) if t <= now)
        now += 1 / (spec.rps * factor)
    return result
