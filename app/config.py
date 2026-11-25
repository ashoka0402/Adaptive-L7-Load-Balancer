"""Configuration loading and validation using Pydantic."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    admin_token: str = ""


class LoadBalancingConfig(BaseModel):
    strategy: Literal[
        "round_robin",
        "weighted_round_robin",
        "least_connections",
        "latency_aware",
        "adaptive",
    ] = "adaptive"


class AdaptiveWeightsConfig(BaseModel):
    latency: float = Field(default=0.35, ge=0)
    connections: float = Field(default=0.25, ge=0)
    queue: float = Field(default=0.15, ge=0)
    errors: float = Field(default=0.20, ge=0)
    resources: float = Field(default=0.05, ge=0)


class RequestClassRule(BaseModel):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    path_prefix: str = Field(default="/", min_length=1, max_length=256)
    methods: list[str] = Field(default_factory=list, max_length=16)
    headers: dict[str, str] = Field(default_factory=dict, max_length=8)
    backend_ids: list[str] = Field(default_factory=list, max_length=100)


class IntelligenceConfig(BaseModel):
    enabled: bool = False
    autopilot: bool = False
    objective: Literal["balanced", "latency", "throughput", "reliability", "custom"] = "balanced"
    learning: bool = False
    learning_seed: int = 7
    learning_exploration: float = Field(default=0.03, ge=0, le=0.1)
    window_size: int = Field(default=500, ge=20, le=5000)
    observation_seconds: float = Field(default=30, ge=1, le=300)
    trend_points: int = Field(default=8, ge=3, le=60)
    horizon_seconds: float = Field(default=3, gt=0, le=60)
    congestion_threshold: float = Field(default=0.7, gt=0, lt=1)
    prediction_weight: float = Field(default=0.35, ge=0, le=1)
    p99_target_seconds: float = Field(default=0.1, gt=0, le=60)
    error_target: float = Field(default=0.01, gt=0, le=1)
    availability_target: float = Field(default=0.99, ge=0, le=1)
    throughput_target: float = Field(default=0, ge=0, le=1000000)
    minimum_samples: int = Field(default=20, ge=5, le=5000)
    policy_cooldown: float = Field(default=10, ge=1, le=300)
    weight_step: float = Field(default=0.15, gt=0, le=0.25)
    rollback_margin: float = Field(default=0.5, gt=0, le=100)
    rules: list[RequestClassRule] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_intelligence(self) -> IntelligenceConfig:
        if self.learning and not self.autopilot:
            raise ValueError("learning requires autopilot")
        if self.autopilot and not self.enabled:
            raise ValueError("autopilot requires intelligence")
        if self.minimum_samples > self.window_size:
            raise ValueError("minimum_samples exceeds window_size")
        names = [rule.name for rule in self.rules]
        if len(set(names)) != len(names) or "default" in names:
            raise ValueError("request class names must be unique and not reserved")
        return self


class AdaptiveConfig(BaseModel):
    interval: float = Field(default=1.0, gt=0)
    window_size: int = Field(default=100, ge=10)
    exploration: float = Field(default=0.03, ge=0, le=0.30)
    min_probability: float = Field(default=0.005, ge=0, le=0.10)
    slow_start_seconds: float = Field(default=30.0, ge=0)
    smoothing: float = Field(default=0.30, gt=0, le=1)
    max_probability_step: float = Field(default=0.15, gt=0, le=1)
    weights: AdaptiveWeightsConfig = Field(default_factory=AdaptiveWeightsConfig)
    intelligence: IntelligenceConfig = Field(default_factory=IntelligenceConfig)

    @model_validator(mode="after")
    def ensure_weight(self) -> AdaptiveConfig:
        if sum(self.weights.model_dump().values()) <= 0:
            raise ValueError("at least one adaptive metric weight must be positive")
        return self


class BackendConfig(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    weight: int = Field(default=1, ge=1, le=10000)
    capacity: int = Field(default=100, ge=1, le=1000000)

    @field_validator("weight")
    @classmethod
    def weight_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("weight must be >= 1")
        return v


class HealthCheckConfig(BaseModel):
    interval: float = 5.0
    timeout: float = 2.0
    failure_threshold: int = 3
    recovery_threshold: int = 2
    path: str = "/health"


class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = 5
    recovery_timeout: float = 10.0
    half_open_requests: int = 1


class ConnectionPoolConfig(BaseModel):
    max_connections_per_backend: int = 100
    max_idle_connections: int = 20
    idle_timeout: float = 30.0


class TimeoutsConfig(BaseModel):
    connect: float = 2.0
    read: float = 5.0
    write: float = 5.0
    idle: float = 30.0


class RetryConfig(BaseModel):
    enabled: bool = True
    max_attempts: int = 2
    retryable_methods: list[str] = Field(default_factory=lambda: ["GET", "HEAD"])
    retryable_status_codes: list[int] = Field(default_factory=lambda: [502, 503, 504])


class BackpressureConfig(BaseModel):
    max_concurrent_requests: int = 1000
    max_queue_size: int = 500
    queue_timeout: float = 1.0


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"


class MetricsConfig(BaseModel):
    enabled: bool = True
    path: str = "/metrics"


class RequestConfig(BaseModel):
    max_body_size: int = 10 * 1024 * 1024
    max_header_size: int = 8192


class PersistenceConfig(BaseModel):
    enabled: bool = True
    path: str = "data/pulse.db"
    snapshot_limit: int = Field(default=500, ge=1, le=10000)


class ShadowConfig(BaseModel):
    enabled: bool = False
    sample_every: int = Field(default=100, ge=10, le=10000)
    queue_limit: int = Field(default=64, ge=1, le=256)
    algorithms: tuple[Literal["round_robin", "weighted_round_robin", "least_connections", "latency_aware"], ...] = Field(
        default=("round_robin", "least_connections"), max_length=4
    )

    @model_validator(mode="after")
    def unique_algorithms(self) -> ShadowConfig:
        if len(set(self.algorithms)) != len(self.algorithms):
            raise ValueError("duplicate shadow algorithm")
        return self


class AppConfig(BaseModel):
    shadow: ShadowConfig = Field(default_factory=ShadowConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    load_balancing: LoadBalancingConfig = Field(default_factory=LoadBalancingConfig)
    adaptive: AdaptiveConfig = Field(default_factory=AdaptiveConfig)
    backends: list[BackendConfig] = Field(default_factory=list)
    health_check: HealthCheckConfig = Field(default_factory=HealthCheckConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    connection_pool: ConnectionPoolConfig = Field(default_factory=ConnectionPoolConfig)
    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    backpressure: BackpressureConfig = Field(default_factory=BackpressureConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    request: RequestConfig = Field(default_factory=RequestConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)

    @model_validator(mode="after")
    def ensure_backends(self) -> AppConfig:
        if self.server.admin_token in {"", "admin-secret-token-change-me"} or len(
            self.server.admin_token
        ) < 16:
            raise ValueError("admin token must be an external secret of at least 16 characters")
        if not self.backends:
            raise ValueError("at least one backend must be configured")
        ids = [b.id for b in self.backends]
        if len(ids) != len(set(ids)):
            raise ValueError("backend ids must be unique")
        return self


def load_config(path: str | Path) -> AppConfig:
    """Load and validate configuration from YAML file. Fail fast on errors."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    token = os.environ.get("PULSE_ADMIN_TOKEN")
    if token:
        raw.setdefault("server", {})["admin_token"] = token
    configured_token = raw.get("server", {}).get("admin_token", "")
    if configured_token in {"", "admin-secret-token-change-me"} or len(configured_token) < 16:
        raise ValueError("set PULSE_ADMIN_TOKEN to a secret of at least 16 characters")
    return AppConfig.model_validate(raw)
