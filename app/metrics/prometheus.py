"""Prometheus metrics exposition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

if TYPE_CHECKING:
    from app.models.backend import BackendRegistry

# Traffic
REQUESTS_TOTAL = Counter(
    "lb_requests_total",
    "Total requests handled by the load balancer",
    ["method", "status"],
)
ACTIVE_REQUESTS = Gauge(
    "lb_active_requests",
    "Currently active requests",
)
REQUEST_LATENCY = Histogram(
    "lb_request_latency_seconds",
    "End-to-end request latency",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
BACKEND_LATENCY = Histogram(
    "lb_backend_latency_seconds",
    "Backend response latency",
    ["backend"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# Backend
BACKEND_ACTIVE = Gauge(
    "lb_backend_active_connections",
    "Active connections per backend",
    ["backend"],
)
BACKEND_REQUESTS = Counter(
    "lb_backend_requests_total",
    "Requests sent to backend",
    ["backend", "result"],
)
BACKEND_HEALTH = Gauge(
    "lb_backend_health_status",
    "Backend health (1=healthy, 0=unhealthy, 0.5=draining)",
    ["backend"],
)
BACKEND_CIRCUIT = Gauge(
    "lb_backend_circuit_state",
    "Circuit state (0=closed, 1=open, 0.5=half_open)",
    ["backend"],
)

# Reliability
TIMEOUTS = Counter("lb_timeouts_total", "Timeouts")
RETRIES = Counter("lb_retries_total", "Retry attempts")
CIRCUIT_OPENS = Counter(
    "lb_circuit_breaker_opens_total",
    "Circuit breaker open events",
    ["backend"],
)
FAILED_REQUESTS = Counter("lb_failed_requests_total", "Failed requests")

# Load shedding
REJECTED = Counter("lb_rejected_requests_total", "Requests rejected by backpressure")
QUEUE_SIZE = Gauge("lb_queue_size", "Current backpressure queue size")


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def update_backend_gauges(registry: "BackendRegistry") -> None:
    for b in registry.list_all():
        BACKEND_ACTIVE.labels(backend=b.id).set(b.active_connections)
        health_val = {
            "HEALTHY": 1.0,
            "UNHEALTHY": 0.0,
            "DRAINING": 0.5,
        }.get(b.status.value, 0.0)
        BACKEND_HEALTH.labels(backend=b.id).set(health_val)
        circuit_val = {
            "CLOSED": 0.0,
            "OPEN": 1.0,
            "HALF_OPEN": 0.5,
        }.get(b.circuit_state.value, 0.0)
        BACKEND_CIRCUIT.labels(backend=b.id).set(circuit_val)
