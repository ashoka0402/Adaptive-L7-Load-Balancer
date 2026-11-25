# Architecture

Pulse is a modular monolith on one asyncio event loop.

- `app/server.py`: configuration, lifecycle, strategy construction and runtime switching.
- `app/proxy.py`: admission, eligibility, selection, retry, header filtering, HTTP forwarding and response streaming.
- `app/routing/`: four routing algorithms with a common interface.
- `app/models/backend.py`: backend registry, request counters, EWMA and health state.
- `app/health/`, `app/resilience/`, `app/pool/`: active/passive health, circuit breaker, retry, backpressure and shared aiohttp connection pooling.
- `app/admin/`, `app/ui/`: local-only token-free management and live dashboard, served on the same origin.
- `app/experiments/`: isolated real loopback HTTP and seeded simulation; identical workload per algorithm.
- `app/persistence/`: bounded SQLite experiment result retention.
- `app/metrics/`: optional Prometheus monitoring; Grafana is optional infrastructure.

The request path acquires admission capacity, selects a healthy eligible backend, increments in-flight counters, forwards through a reused connection and releases counters in finally blocks. A retry cannot use a previously attempted backend or run after response headers have been committed. Active probes are separate from application EWMA.

Algorithm changes replace the strategy instance at runtime, without restarting the proxy. Server health and counters remain intact. No automatic strategy selection, predictor, learning, multidimensional scoring or policy publication remains. Configuration changes do not persist to YAML.

The arena starts isolated local listeners, uses bounded client concurrency and collects actual request outcomes. Its simulation is a model, not a network benchmark. Shared process-level Prometheus metrics may include arena activity; the Dashboard's instance-local metrics do not.

No multi-process state sharing, native TLS or HTTP/2, WebSocket proxying or production readiness is claimed.
