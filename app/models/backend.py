"""Backend registry models and state management."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class BackendStatus(str, Enum):
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"
    DRAINING = "DRAINING"


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class Backend:
    """Thread-safe-ish backend state (updates under asyncio lock in registry)."""

    id: str
    host: str
    port: int
    weight: int = 1
    status: BackendStatus = BackendStatus.HEALTHY
    active_connections: int = 0
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    average_latency: float = 0.0  # EWMA latency in seconds
    recent_latency: float = 0.0
    last_health_check: float = 0.0
    circuit_state: CircuitState = CircuitState.CLOSED
    circuit_opened_at: float = 0.0
    half_open_in_flight: int = 0
    enabled: bool = True

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def is_available(self) -> bool:
        """Whether the backend can accept new normal traffic."""
        if not self.enabled:
            return False
        if self.status == BackendStatus.DRAINING:
            return False
        if self.status == BackendStatus.UNHEALTHY:
            return False
        if self.circuit_state == CircuitState.OPEN:
            return False
        return True

    def update_latency(self, observed: float, alpha: float = 0.3) -> None:
        """Update EWMA latency estimate."""
        if self.average_latency <= 0:
            self.average_latency = observed
        else:
            self.average_latency = alpha * observed + (1.0 - alpha) * self.average_latency
        self.recent_latency = observed

    def record_success(self, latency: float) -> None:
        self.total_requests += 1
        self.successful_requests += 1
        self.consecutive_successes += 1
        self.consecutive_failures = 0
        self.update_latency(latency)

    def record_failure(self) -> None:
        self.total_requests += 1
        self.failed_requests += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "host": self.host,
            "port": self.port,
            "weight": self.weight,
            "status": self.status.value,
            "active_connections": self.active_connections,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "average_latency_ms": round(self.average_latency * 1000, 2),
            "recent_latency_ms": round(self.recent_latency * 1000, 2),
            "last_health_check": self.last_health_check,
            "circuit_state": self.circuit_state.value,
            "enabled": self.enabled,
        }


@dataclass
class BackendRegistry:
    """Central registry of backends with concurrent-safe access via external lock."""

    backends: dict[str, Backend] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)

    def add(self, backend: Backend) -> None:
        if backend.id in self.backends:
            raise ValueError(f"backend already exists: {backend.id}")
        self.backends[backend.id] = backend
        self._order.append(backend.id)

    def remove(self, backend_id: str) -> Optional[Backend]:
        if backend_id not in self.backends:
            return None
        self._order = [i for i in self._order if i != backend_id]
        return self.backends.pop(backend_id)

    def get(self, backend_id: str) -> Optional[Backend]:
        return self.backends.get(backend_id)

    def list_all(self) -> list[Backend]:
        return [self.backends[i] for i in self._order if i in self.backends]

    def available(self) -> list[Backend]:
        return [b for b in self.list_all() if b.is_available]

    def healthy(self) -> list[Backend]:
        return [
            b
            for b in self.list_all()
            if b.status == BackendStatus.HEALTHY and b.enabled
        ]
