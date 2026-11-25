"""Configuration loading and validation using Pydantic."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)



class ServerConfig(ConfigModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=0, le=65535)


class LoadBalancingConfig(ConfigModel):
    strategy: Literal[
        "round_robin",
        "weighted_round_robin",
        "least_connections",
        "latency_aware",
    ] = "round_robin"


class BackendConfig(ConfigModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    weight: int = Field(default=1, ge=1, le=10000)
    capacity: int = Field(default=100, ge=1, le=1000000)

    @field_validator("host")
    @classmethod
    def valid_host(cls, value: str) -> str:
        # Exact DNS names or IPv4 only; never URL syntax, userinfo or paths.
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value):
            raise ValueError("backend host must be a DNS name or IPv4 address")
        return value.lower()

    @field_validator("weight")
    @classmethod
    def weight_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("weight must be >= 1")
        return v


class AllowedDestination(ConfigModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def valid_host(cls, value: str) -> str:
        return BackendConfig.valid_host(value)


class HealthCheckConfig(ConfigModel):
    interval: float = Field(default=5.0, gt=0, le=3600)
    timeout: float = Field(default=2.0, gt=0, le=60)
    failure_threshold: int = Field(default=3, ge=1, le=1000)
    recovery_threshold: int = Field(default=2, ge=1, le=1000)
    path: str = "/health"

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        if not re.fullmatch(r"/(?!/)[^\s?#]*", value):
            raise ValueError("health path must be an absolute path without query or fragment")
        return value


class CircuitBreakerConfig(ConfigModel):
    failure_threshold: int = Field(default=5, ge=1, le=1000)
    recovery_timeout: float = Field(default=10.0, ge=0, le=3600)
    half_open_requests: int = Field(default=1, ge=1, le=100)


class ConnectionPoolConfig(ConfigModel):
    max_connections_per_backend: int = Field(default=100, ge=1, le=10000)
    max_idle_connections: int = Field(default=20, ge=0, le=10000)
    idle_timeout: float = Field(default=30.0, gt=0, le=3600)
    max_total_connections: int = Field(default=1000, ge=1, le=20000)


class TimeoutsConfig(ConfigModel):
    connect: float = Field(default=2.0, gt=0, le=300)
    read: float = Field(default=5.0, gt=0, le=300)
    write: float = Field(default=5.0, gt=0, le=300)
    idle: float = Field(default=30.0, gt=0, le=3600)


class RetryConfig(ConfigModel):
    enabled: bool = True
    max_attempts: int = Field(default=2, ge=1, le=5)
    retryable_methods: list[str] = Field(default_factory=lambda: ["GET", "HEAD"])
    retryable_status_codes: list[int] = Field(default_factory=lambda: [502, 503, 504])


class BackpressureConfig(ConfigModel):
    max_concurrent_requests: int = Field(default=1000, ge=1, le=10000)
    max_queue_size: int = Field(default=500, ge=0, le=10000)
    queue_timeout: float = Field(default=1.0, gt=0, le=60)


class LoggingConfig(ConfigModel):
    level: str = "INFO"
    format: str = "json"


class MetricsConfig(ConfigModel):
    enabled: bool = True
    path: str = "/metrics"


class RequestConfig(ConfigModel):
    max_body_size: int = Field(default=10 * 1024 * 1024, ge=1, le=64*1024*1024)
    max_header_size: int = Field(default=8192, ge=1024, le=65536)


class PersistenceConfig(ConfigModel):
    enabled: bool = True
    path: str = "data/pulse.db"


class AppConfig(ConfigModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    load_balancing: LoadBalancingConfig = Field(default_factory=LoadBalancingConfig)
    backends: list[BackendConfig] = Field(default_factory=list, max_length=100)
    allowed_dynamic_destinations: list[AllowedDestination] = Field(default_factory=list, max_length=100)

    def destination_allowed(self, host: str, port: int) -> bool:
        return (any((host, port) == (b.host, b.port) for b in self.backends)
                or any((host, port) == (b.host, b.port) for b in self.allowed_dynamic_destinations))
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
