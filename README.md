# Pulse — Adaptive Traffic Intelligence Engine

> Don't just distribute traffic. Understand it.

Pulse is an asynchronous Layer 7 HTTP load balancer with a feedback-controlled routing engine. It combines live latency, concurrency, queue, error, and optional resource signals into versioned routing policies, then distributes traffic probabilistically to avoid starvation, oscillation, and overload concentration.

Pulse remains a modular monolith: one `aiohttp` data plane, one adaptive controller task, bounded in-memory telemetry, and JSON/Prometheus integration points.

## What it provides

- Adaptive routing with normalized multidimensional pressure scores.
- Weighted probabilistic distribution, bounded exploration, cold-start handling, and slow start.
- Smoothed policy changes with per-interval probability limits.
- Explainable policy snapshots and runtime-adjustable metric weights.
- Baseline round-robin, weighted round-robin, least-connections, and EWMA latency-aware strategies.
- Active/passive health, circuit breaking, bounded retries, backpressure, connection reuse, metrics, and structured logs.
- Async traffic generation and SQLite persistence adapters for experiments and policy history.

## Quick start

Python 3.12 or newer is required.

```powershell
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
$env:PULSE_ADMIN_TOKEN = "replace-with-a-long-random-secret" # PowerShell
python -m app.main -c config/config.yaml
```

The supplied configuration uses Docker service names. For a local non-Docker run, change backend hosts in `config/config.yaml` to `127.0.0.1` and start the demo backends separately.

Run the complete development topology:

```bash
export PULSE_ADMIN_TOKEN='replace-with-a-long-random-secret'
export GRAFANA_ADMIN_PASSWORD='replace-with-a-long-random-secret'
docker compose up --build
```

| Service | Development URL |
|---|---|
| Pulse | `http://localhost:8080` |
| Prometheus | `http://localhost:9090` |
| Grafana | `http://localhost:3000` |

Startup fails when `PULSE_ADMIN_TOKEN` is absent, short, or the former sample value. Docker Compose also requires an explicit Grafana password.

## Adaptive configuration

```yaml
load_balancing:
  strategy: adaptive

adaptive:
  interval: 1.0
  window_size: 100
  exploration: 0.03
  min_probability: 0.005
  slow_start_seconds: 30
  smoothing: 0.30
  max_probability_step: 0.15
  weights:
    latency: 0.35
    connections: 0.25
    queue: 0.15
    errors: 0.20
    resources: 0.05
```

Each backend also has a static `weight` and configured `capacity`. Queue, CPU, and memory signals are optional and supplied through the admin telemetry endpoint.

## Operations API

All admin endpoints require `Authorization: Bearer <PULSE_ADMIN_TOKEN>`.

```text
GET    /admin/backends
POST   /admin/backends
PATCH  /admin/backends/{id}
DELETE /admin/backends/{id}
PUT    /admin/backends/{id}/telemetry
GET    /admin/routing/policy
PATCH  /admin/routing/weights
GET    /metrics
```

Example policy inspection:

```bash
curl -H "Authorization: Bearer $PULSE_ADMIN_TOKEN" \
  http://localhost:8080/admin/routing/policy
```

Example external telemetry update:

```bash
curl -X PUT -H "Authorization: Bearer $PULSE_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"queue_depth":12,"cpu":0.72,"memory":0.61}' \
  http://localhost:8080/admin/backends/backend-1/telemetry
```

## Validation

```bash
python -m pytest -q
python -m ruff check .
python -m mypy app
```

Run a measured workload against a live instance:

```bash
python benchmarks/run_benchmarks.py \
  --url http://127.0.0.1:8080/api/test \
  --concurrency 50 --warmup 2 --duration 10 \
  --label adaptive --output docs/benchmarks/adaptive.json
```

Use identical topology, workload, warm-up, and environment for comparisons. The runner records actual elapsed time, status counts, latency percentiles, and environment metadata. Simulation results must be labeled separately from end-to-end HTTP measurements. No performance improvement is claimed without committed raw results.

Run the deterministic identical-workload routing comparison:

```bash
python benchmarks/compare_routing.py --seed 7 \
  --output docs/benchmarks/routing-simulation-seed-7.json
```

## Design and status

- `ARCHITECTURE.md` describes the request path, adaptive policy, concurrency model, and trust boundaries.
- `IMPLEMENTATION_STATUS.md` distinguishes implemented, partial, and planned capabilities.
- `ROADMAP.md` orders the remaining work.
- `docs/adr/` records durable architectural decisions.

## Current limits

Pulse is an engineering platform, not a replacement for a mature edge proxy. It currently has single-process state, buffered request bodies, no TLS termination or service discovery, shared-token operations authentication, and no backend destination allowlist. SQLite policy history is local to one instance.

Licensed under MIT.
