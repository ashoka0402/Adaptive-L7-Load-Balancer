# Pulse Architecture

Pulse — Adaptive Traffic Intelligence Engine

_Don't just distribute traffic. Understand it._

## System shape

Pulse is a modular monolith built on `aiohttp` and one asyncio event loop. The data plane, control plane, telemetry, health, resilience, admin API, traffic generator, and persistence adapter share a process but have explicit module boundaries. This keeps routing state local and request handling inexpensive without introducing operationally unnecessary microservices.

| Concern | Module | Current responsibility |
|---|---|---|
| L7 data plane | `app/proxy.py`, `app/pool/` | Admission, backend selection, HTTP forwarding, response streaming, counters, retries |
| Adaptive control plane | `app/control/`, `app/routing/adaptive.py` | Periodic score aggregation, immutable policy publication, probabilistic selection |
| Telemetry pipeline | `app/telemetry/`, `app/metrics/` | Bounded observations, external backend signals, Prometheus export |
| Health and resilience | `app/health/`, `app/resilience/` | Active/passive health, circuit breaking, bounded admission, retry policy |
| Frontend integration | `app/admin/routes.py` | JSON APIs for backends, routing policies, weights, and external telemetry |
| Traffic generation | `app/traffic/`, `benchmarks/` | Bounded asynchronous HTTP workloads and reproducible reports |
| Persistence | `app/persistence/` | SQLite storage for bounded policy history and experiment results; controller publications are wired at startup |
| Experiments/simulation | `app/experiments/`, `benchmarks/compare_routing.py` | Deterministic normal, burst, fast-error degradation, and recovery scenarios |

## Request lifecycle

1. `aiohttp` accepts the request on the wildcard route.
2. The global backpressure controller either admits it or returns `503` after bounded queuing.
3. The proxy creates a request context and reads the body within `client_max_size`.
4. Health and circuit state determine eligible backends.
5. The selected strategy chooses one backend. Adaptive routing reads the controller's current immutable snapshot and performs one weighted random draw.
6. The proxy increments the backend's in-flight count, forwards the request through the shared client session, and streams the upstream response.
7. Completion records latency and outcome telemetry, updates health/circuit state and Prometheus metrics, then decrements counters in `finally`.
8. Structured logs and adaptive response metadata explain the selected policy when the adaptive strategy is active.

Request bodies are currently buffered to support replayable, bounded requests; responses are streamed. Status-code retries are disabled after an upstream response is available because the client response may already be committed. Connection-level failures may be retried for configured idempotent methods.

## Adaptive policy model

The telemetry pipeline maintains a bounded rolling window per backend. The controller reads:

- EWMA application latency;
- active connections relative to estimated capacity;
- externally supplied queue depth relative to capacity;
- rolling request error rate;
- optional normalized CPU and memory utilization.

Every controller interval, metrics are normalized across currently available backends. A configured weighted sum produces a pressure score. The controller then applies:

- a neutral prior for backends without samples;
- EWMA-style score smoothing;
- capacity reduction based on errors and resource pressure;
- slow-start ramping for newly registered backends;
- a bounded uniform exploration share and minimum probability;
- a maximum probability step between snapshots.

Scores become probabilities with `exp(-5 × score)`, adjusted by static backend weight and slow-start ramp. The controller publishes a frozen `RoutingPolicySnapshot` by replacing one reference. The hot path does not normalize metrics or calculate scores.

Policy explanations and normalized metric contributions are available through authenticated `GET /admin/routing/policy`. Weights can be changed at runtime with `PATCH /admin/routing/weights`. External queue, CPU, and memory values can be supplied with `PUT /admin/backends/{id}/telemetry`.

## Concurrency model

Pulse currently relies on event-loop-local mutation. Immutable policy snapshots prevent a request from observing a partially built policy, but backend registry, health, circuit, and counter state are not shared-memory or multi-process safe. Network awaits can interleave tasks, so counter cleanup and state transitions must remain explicit and tested.

The architecture supports one process today. Multiple workers or nodes require a separate ADR covering state ownership, observation aggregation, and policy distribution.

## Failure behavior

- Active and passive health can remove degraded backends from the normal candidate set.
- Circuit breakers stop repeated requests during sustained failures and allow bounded recovery probes.
- Adaptive error, saturation, and resource pressure lower a backend's traffic probability before hard removal.
- Exploration preserves recovery observations; slow start and probability-step limits avoid a recovery stampede.
- Global backpressure sheds load rather than allowing an unbounded queue.

## Trust boundaries

Client HTTP, admin API input, external backend telemetry, and upstream HTTP are trust boundaries. Startup requires `PULSE_ADMIN_TOKEN`, admin reads and writes use constant-time bearer-token comparison, backend count and identifiers are bounded, and static plus `Connection`-nominated hop headers are stripped. Before internet exposure, Pulse still needs destination allowlisting or service discovery, an isolated operations listener, TLS at the edge, and stronger identity than a shared token.

## Deployment

`docker-compose.yml` runs one Pulse instance, three demonstration backends, Prometheus, and Grafana. Admin and Grafana secrets must be supplied through environment variables, monitoring binds to loopback, and policy history is stored in a named SQLite volume. This is a local evaluation topology, not a highly available deployment; backend registry and live telemetry remain process-local.

See `docs/adr/` for durable decisions and `IMPLEMENTATION_STATUS.md` for verified gaps.
