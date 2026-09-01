"""Configuration loading and validation using Pydantic."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    admin_token: str = "admin-secret-token-change-me"


class LoadBalancingConfig(BaseModel):
    strategy: Literal[
        "round_robin",
        "weighted_round_robin",
        "least_connections",
        "latency_aware",
    ] = "latency_aware"


class BackendConfig(BaseModel):
    id: str
    host: str
    port: int
    weight: int = 1

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
    retryable_status_codes: list[int] = Field(
        default_factory=lambda: [502, 503, 504]
    )


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


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    load_balancing: LoadBalancingConfig = Field(default_factory=LoadBalancingConfig)
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

    @model_validator(mode="after")
    def ensure_backends(self) -> "AppConfig":
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
    return AppConfig.model_validate(raw)
