# Adaptive L7 Load Balancer

> **A fault-tolerant, latency-aware Layer 7 HTTP load balancer built from scratch in Python, with adaptive routing, health-based failover, circuit breaking, backpressure, connection pooling, and production-style observability.**

<p align="center">

**[Architecture] · [Benchmarks] · [Quick Start] · [Design Decisions]**

</p>

> **Portfolio / systems-engineering project.**  
> Built for studying and demonstrating load-balancing, distributed-systems, networking, and reliability concepts. Not intended to be used as a production internet-facing proxy.

---

# What This Project Does

A load balancer sits between clients and a pool of backend servers and decides **where each request should go**.

A basic load balancer might simply do:

```text
Request
   ↓
Round Robin
   ↓
Backend
```

But real systems have to deal with:

- slow backends
- failing backends
- overloaded instances
- connection limits
- transient failures
- retry storms
- traffic spikes
- graceful shutdown
- observability

This project implements those concerns directly in the load balancer rather than hiding them behind an existing proxy or library.

The result is an **adaptive Layer 7 reverse proxy** that continuously uses backend health, latency, and connection state to make routing decisions.

---

# Why Adaptive Routing?

A static load-balancing strategy assumes that all healthy backends are roughly equivalent.

In reality:

```text
Backend 1 → 10 ms
Backend 2 → 15 ms
Backend 3 → 200 ms
```

A naive Round Robin algorithm still sends traffic evenly:

```text
B1 → B2 → B3 → B1 → B2 → B3
```

This project instead allows routing decisions to incorporate live backend behavior.

For latency-aware routing:

```text
EWMA Latency
      +
Active Connections
      +
Backend Capacity
      ↓
Routing Score
      ↓
Best Candidate
```

This allows the load balancer to react to changing backend conditions rather than treating the backend pool as static.

---

# Architecture

```text
                         Clients
                            │
                            ▼
              ┌───────────────────────────┐
              │   Adaptive L7 Load        │
              │        Balancer           │
              │                           │
              │  HTTP Reverse Proxy       │
              │  Routing Engine           │
              │  Health Monitor           │
              │  Circuit Breaker           │
              │  Connection Pool           │
              │  Backpressure              │
              │  Metrics                   │
              └─────────────┬─────────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
          Backend-1     Backend-2     Backend-3
              │             │             │
              └─────────────┼─────────────┘
                            │
                            ▼
                 Prometheus → Grafana
```

The load balancer owns the complete request path:

```text
Client
  ↓
Accept request
  ↓
Validate / identify request
  ↓
Select healthy backend
  ↓
Acquire pooled connection
  ↓
Forward request
  ↓
Receive response
  ↓
Update backend state
  ↓
Return response
```

Backend state continuously feeds back into future routing decisions.

---

# Core Components

| Component | Responsibility |
|---|---|
| **HTTP Proxy** | Forwards Layer 7 HTTP requests and responses |
| **Routing Engine** | Selects the backend for each request |
| **Health Monitor** | Detects unhealthy backends |
| **Circuit Breaker** | Prevents repeated requests to failing backends |
| **Connection Pool** | Reuses backend connections |
| **Backpressure** | Prevents overload and unbounded queue growth |
| **Retry Manager** | Performs controlled retries for safe requests |
| **Backend Manager** | Dynamically adds, removes, and drains backends |
| **Metrics** | Exposes runtime behavior to Prometheus |
| **Structured Logging** | Provides request-level observability |

---

# Routing Strategies

The routing engine supports multiple strategies so their behavior can be compared under different workloads.

## Round Robin

Cycles through available healthy backends.

```text
B1 → B2 → B3 → B1 → B2 → B3
```

**Complexity:** `O(1)`

Useful as the simplest baseline.

---

## Weighted Round Robin

Distributes traffic according to backend weights.

For example:

```text
Backend      Weight

B1             5
B2             3
B3             2
```

Approximately:

```text
B1 → 50%
B2 → 30%
B3 → 20%
```

The implementation uses **Smooth Weighted Round Robin**, avoiding naive list duplication while maintaining proportional distribution.

---

## Least Connections

Routes to the backend with the fewest active connections.

Conceptually:

```text
B1 → 12 connections
B2 →  4 connections  ← selected
B3 →  9 connections
```

Tie-breaking considers latency and backend ID.

This makes the strategy responsive to current concurrency rather than historical traffic distribution.

---

# Latency-Aware Routing

This is the core adaptive routing strategy.

Each backend maintains an **EWMA latency estimate** rather than reacting directly to the latest request.

The routing score is:

```text
Score =
    EWMA_latency × (1 + active_connections / capacity)
```

The backend with the lowest score becomes the preferred candidate.

### Why EWMA?

Raw latency is noisy:

```text
10ms
11ms
250ms
12ms
10ms
```

Reacting immediately to the 250 ms sample could cause unnecessary routing oscillations.

EWMA smooths individual spikes while still allowing the system to adapt to sustained changes.

### Connection-aware penalty

A backend can have excellent latency while already handling a large amount of traffic.

The connection term prevents the router from continuously stampeding the currently fastest backend.

```text
Fast backend
     │
     ├── Low latency
     │
     └── High active connections
              ↓
        Increased score
              ↓
        Other backends
        become attractive
```

A small exploration component also prevents backends from being permanently starved.

---

# Health & Failure Handling

Backend availability is tracked using both **active** and **passive** signals.

## Active Health Checks

The load balancer periodically probes:

```text
GET /health
```

Backends transition between:

```text
HEALTHY
   │
   │ repeated failures
   ▼
UNHEALTHY
   │
   │ successful probes
   ▼
HEALTHY
```

Health thresholds and intervals are configurable.

---

## Passive Health Monitoring

Real traffic also provides health information.

Signals include:

- connection failures
- request timeouts
- backend errors
- HTTP 5xx responses

This means a backend does not need to wait for the next active health probe before its state can react to failures.

```text
Active probes
      +
Real traffic
      ↓
Backend health state
      ↓
Routing decisions
```

---

# Circuit Breaker

Health checks alone are not enough.

A backend may technically respond to health probes while failing real application requests.

The circuit breaker protects the system from repeatedly sending traffic to a failing backend.

```text
             failures
CLOSED ─────────────────► OPEN
  ▲                         │
  │                         │ recovery timeout
  │                         ▼
  └──────── success ─── HALF_OPEN
```

### CLOSED

Requests are allowed normally.

### OPEN

Requests are rejected from the failing backend without repeatedly attempting the connection.

### HALF_OPEN

A limited recovery attempt is allowed.

If successful:

```text
HALF_OPEN → CLOSED
```

If it fails:

```text
HALF_OPEN → OPEN
```

This prevents one bad backend from consuming the load balancer's available capacity.

---

# Retries

Retries are deliberately restricted.

By default, retries are allowed only for:

```text
GET
HEAD
```

and only for configured retryable failure conditions.

This avoids blindly replaying non-idempotent requests.

```text
POST /payment
      ↓
Backend timeout
      ↓
DO NOT blindly replay
```

versus:

```text
GET /api/data
      ↓
Backend timeout
      ↓
Retry another healthy backend
```

Retry attempts and retryable status codes are configurable.

---

# Backpressure & Load Shedding

An unbounded queue can turn overload into catastrophic latency.

Instead, the load balancer uses bounded capacity:

```text
Incoming Requests
       │
       ▼
   Bounded Queue
       │
   ┌───┴────┐
   │        │
Capacity   Full
   │        │
   ▼        ▼
 Process    503
```

When the system reaches its configured capacity, requests are rejected instead of waiting indefinitely.

This provides **controlled degradation**:

```text
More traffic
    ↓
Queue fills
    ↓
Requests rejected
    ↓
Latency remains bounded
```

rather than:

```text
More traffic
    ↓
Unbounded queue
    ↓
Latency explodes
    ↓
Memory pressure
    ↓
System failure
```

---

# Connection Pooling

Backend connections are pooled and reused rather than establishing a new connection for every request.

```text
Request 1 ──┐
Request 2 ──┼──► Connection Pool ──► Backend
Request 3 ──┘
```

This reduces connection establishment overhead and makes keep-alive traffic more efficient.

Pool behavior is configurable through:

- maximum connections
- keep-alive settings
- connection timeouts
- idle behavior

---

# Dynamic Backend Management

Backends can be managed without restarting the load balancer.

The admin API supports:

```text
ADD
  ↓
HEALTHY
  ↓
ACTIVE
  ↓
DRAINING
  ↓
REMOVED
```

### Why draining?

Immediately removing a backend can interrupt requests that are already in flight.

Instead:

```text
Backend
   ↓
DRAINING
   ↓
Stop receiving new requests
   ↓
Finish in-flight requests
   ↓
Close connections
   ↓
Remove
```

This enables safer runtime reconfiguration.

---

# Layer 7 Proxy Behavior

Because this is a Layer 7 load balancer, routing happens at the HTTP level.

The proxy handles:

- HTTP methods
- request headers
- response headers
- request/response streaming
- status codes
- request IDs

Each request receives an `X-Request-ID` when required, allowing the request to be correlated across logs and metrics.

Example:

```text
Client
  │
  │ X-Request-ID: abc123
  ▼
Load Balancer
  │
  ▼
Backend
```

The same request ID can then be used to trace the request through the system.

---

# Observability

The system exposes Prometheus-compatible metrics at:

```text
/metrics
```

Important metrics include:

```text
lb_requests_total
lb_request_latency_seconds
lb_backend_latency_seconds

lb_backend_active_connections
lb_backend_health_status
lb_backend_circuit_state

lb_timeouts_total
lb_retries_total
lb_rejected_requests_total
lb_queue_size
```

These metrics allow the behavior of the routing and reliability mechanisms to be observed rather than inferred.

---

## Structured Logging

Requests are emitted as structured JSON.

Example:

```json
{
  "timestamp": "...",
  "request_id": "...",
  "method": "GET",
  "path": "/api/test",
  "backend": "backend-1",
  "status_code": 200,
  "latency_ms": 12.3,
  "retry_count": 0
}
```

This makes request-level debugging and aggregation easier than unstructured text logs.

---

# Prometheus + Grafana

The project includes a preconfigured monitoring stack:

```text
Load Balancer
      │
      │ /metrics
      ▼
 Prometheus
      │
      ▼
  Grafana
```

The Grafana dashboard is automatically provisioned when using Docker Compose.

This allows you to observe:

- request throughput
- request latency
- backend latency
- active connections
- backend health
- circuit breaker state
- retries
- timeouts
- rejected requests
- queue depth

---

# Benchmarking

The project is designed around **measurement rather than assumptions**.

A simple benchmark can generate concurrent traffic:

```bash
python benchmarks/run_benchmarks.py   --url http://localhost:8080/api/test   --concurrency 100   --duration 15
```

Example output:

```text
requests: 74564
rps:      4970.93
avg_ms:   11.50
p50_ms:   8.25
```

The benchmark suite can be used to investigate questions such as:

- How does throughput change with concurrency?
- How does latency change under load?
- Does latency-aware routing improve tail behavior?
- What happens when a backend becomes slow?
- Does circuit breaking prevent a failing backend from affecting the pool?
- Does backpressure prevent unbounded latency?
- How much does connection pooling matter?

---

# Failure Scenarios

The most interesting behavior appears when the backend pool is not perfectly healthy.

## Slow Backend

```text
Backend-1 → 10 ms
Backend-2 → 100 ms
Backend-3 → 12 ms
```

Latency-aware routing should gradually favor the faster backends while continuing controlled exploration.

---

## Backend Failure

```text
Backend-2
    ↓
Connection failures / timeouts
    ↓
Health degradation
    ↓
Circuit OPEN
    ↓
Traffic shifts away
```

The remaining backends continue serving traffic.

---

## Traffic Spike

```text
Traffic
   ↓
Queue grows
   ↓
Capacity reached
   ↓
Load shedding
   ↓
503 responses
```

Instead of allowing latency and memory consumption to grow without bound, the system fails fast.

---

# Design Decisions

| Decision | Rationale |
|---|---|
| **Python + asyncio** | Native asynchronous concurrency without a thread-per-request architecture |
| **Layer 7 proxy** | Allows HTTP-aware routing, retries, health handling, and request-level observability |
| **EWMA latency** | Smooths noisy measurements while adapting to sustained changes |
| **Latency + connection score** | Avoids continuously concentrating traffic on one fast backend |
| **Circuit breaker** | Fails fast on unhealthy backends and protects remaining capacity |
| **Bounded queue** | Prevents unbounded latency and memory growth |
| **Controlled retries** | Avoids retry storms and unsafe replay of non-idempotent requests |
| **Connection pooling** | Reuses backend connections and reduces connection overhead |
| **Dynamic draining** | Allows backend removal without abruptly terminating in-flight traffic |
| **Prometheus metrics** | Makes runtime behavior measurable and debuggable |
| **Own routing / health / circuit breaker** | Keeps the core systems concepts visible rather than hiding them behind a library |

---

# Quick Start

## Docker

The easiest way to run the complete stack:

```bash
docker compose up --build
```

Services:

| Service | URL |
|---|---|
| Load Balancer | http://localhost:8080 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

Default Grafana credentials:

```text
admin / admin
```

The Grafana dashboard **Adaptive L7 Load Balancer** is automatically provisioned.

---

# Running Locally

Create an environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the backends:

```bash
python -m backends.server --port 8001 --id backend-1

python -m backends.server   --port 8002   --id backend-2   --mode slow   --slow-ms 100

python -m backends.server --port 8003 --id backend-3
```

Update the backend hosts in:

```text
config/config.yaml
```

to:

```text
127.0.0.1
```

Then start the load balancer:

```bash
python -m app.main -c config/config.yaml
```

---

# Admin API

All mutating endpoints require:

```text
Authorization: Bearer <admin_token>
```

### List Backends

```bash
curl http://localhost:8080/admin/backends
```

### Add a Backend

```bash
curl -X POST http://localhost:8080/admin/backends   -H "Authorization: Bearer admin-secret-token-change-me"   -H "Content-Type: application/json"   -d '{"host":"backend-4","port":8004,"weight":2}'
```

### Drain a Backend

```bash
curl -X PATCH http://localhost:8080/admin/backends/backend-2   -H "Authorization: Bearer admin-secret-token-change-me"   -H "Content-Type: application/json"   -d '{"status":"DRAINING"}'
```

### Delete a Backend

```bash
curl -X DELETE http://localhost:8080/admin/backends/backend-2   -H "Authorization: Bearer admin-secret-token-change-me"
```

---

# Configuration

Configuration lives in:

```text
config/config.yaml
```

Configuration is validated with **Pydantic at startup** so invalid configuration fails fast.

Major sections include:

```text
server
load_balancing.strategy
backends
health_check
circuit_breaker
connection_pool
timeouts
retry
backpressure
```

This keeps operational behavior separate from the implementation.

---

# Load Testing

Using Locust:

```bash
locust   -f tests/load/locustfile.py   --host http://localhost:8080
```

Or use the built-in concurrent benchmark:

```bash
python benchmarks/run_benchmarks.py   --url http://localhost:8080/api/test   --concurrency 100   --duration 15
```

The benchmark results should be interpreted together with Prometheus metrics rather than using throughput alone.

---

# Tests

Run the unit test suite:

```bash
pytest tests/unit -q
```

The test suite covers the core components of the load balancer and helps validate routing, health, reliability, and supporting behavior independently.

---

# Project Structure

```text
Adaptive-L7-Load-Balancer/
│
├── app/
│   ├── routing/
│   ├── health/
│   ├── circuit_breaker/
│   ├── proxy/
│   ├── connection_pool/
│   ├── backpressure/
│   └── ...
│
├── backends/
│   └── server.py
│
├── benchmarks/
│   └── run_benchmarks.py
│
├── tests/
│   ├── unit/
│   └── load/
│
├── monitoring/
│   ├── prometheus/
│   └── grafana/
│
├── config/
│   └── config.yaml
│
├── docker/
│   └── ...
│
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

# What I Learned

This project was built to explore the interaction between **performance and reliability** rather than implementing a load balancer as a simple request router.

The main engineering lessons are:

### Adaptive routing is a feedback system

Routing decisions influence backend load, which influences latency, which then influences future routing decisions.

```text
Routing
   ↓
Backend Load
   ↓
Latency
   ↓
EWMA
   ↓
Routing Score
   ↓
Routing
```

### Reliability mechanisms interact

Health checks, circuit breakers, retries, connection pools, and backpressure cannot be designed independently.

For example:

```text
Retry
  ↓
More backend requests
  ↓
More load
  ↓
Higher latency
  ↓
More failures
  ↓
More retries
```

Without controls, this can become a feedback loop.

The system therefore combines:

```text
Health Checks
     +
Circuit Breaking
     +
Restricted Retries
     +
Backpressure
```

to provide controlled degradation.

### Benchmarks are part of the design

A load balancer can look correct while behaving poorly under concurrency.

The project therefore uses load testing and runtime metrics to validate assumptions about:

- throughput
- latency
- routing distribution
- failure handling
- queue behavior
- backend recovery

---

# Limitations

This is a portfolio and systems-engineering project rather than a production-grade internet-facing proxy.

Current limitations include:

- No distributed control plane
- No multi-node load-balancer clustering
- No persistent distributed backend state
- Single-process routing state
- Configuration is primarily local
- Authentication for the admin API is intentionally simple
- Benchmark results depend on the test environment
- The implementation is not intended to replace mature production proxies such as Nginx or Envoy

These limitations are intentional in scope: the goal is to implement and understand the core mechanisms rather than reproduce an entire production proxy ecosystem.

---

# Future Improvements

Potential extensions include:

- Distributed load-balancer state
- Consistent hashing
- More advanced adaptive routing
- Automatic backend discovery
- TLS termination
- HTTP/2 support
- Distributed tracing
- More comprehensive tail-latency benchmarking
- Automated chaos/failure testing
- Multi-process or multi-node deployment
- More sophisticated retry budgets and overload control

---

# License

MIT
