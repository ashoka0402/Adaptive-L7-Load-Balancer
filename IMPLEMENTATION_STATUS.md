# Implementation Status

Status reflects the repository contents, not planned behavior.

## Implemented

- Single-process async L7 proxy with response streaming, bounded request size, connection reuse, structured logs, Prometheus metrics, active/passive health, circuit breaking, idempotent connection-failure retries, and global backpressure.
- Selectable round-robin, smooth weighted round-robin, least-connections, legacy EWMA latency-aware, and adaptive routing strategies.
- Bounded per-backend latency/error telemetry plus externally supplied queue depth, CPU, and memory utilization.
- Periodic adaptive controller with normalized multidimensional scores, capacity estimation, configurable weights, cold-start prior, slow start, smoothing, bounded probability movement, minimum traffic, and exploration.
- Immutable, versioned policy snapshots and weighted probabilistic selection on the request path. Administrative backend mutations republish policies immediately; if eligible backends change between publications, selection temporarily falls back to uniform selection rather than starving newly registered backends.
- Authenticated JSON endpoints for inspecting policy, changing metric weights, managing backends, and supplying backend telemetry.
- Bounded asynchronous traffic generator with warm-up, percentiles, environment metadata, and JSON output.
- SQLite persistence wired to controller publication with bounded policy history, plus experiment-result storage.
- Deterministic simulation covering normal load, bursts, fast-error degradation, and recovery.
- Unit coverage for baseline routing, adaptive degradation/distribution, telemetry bounds, health, circuit breaking, proxy header safety, simulation, and persistence.

## Partially implemented

- The admin API is the frontend integration layer; no browser UI is included.
- End-to-end measurements are committed, but the baseline used Docker while the upgraded run used host processes after Docker became unresponsive; they are not treated as a controlled performance comparison.
- Capacity estimation uses configured capacity reduced by observed errors and optional resource pressure; it does not infer a backend's service curve or maximum sustainable throughput.
- Queue depth, CPU, and memory require an external telemetry publisher.
- The connection pool configures per-host connector limits, but its explicit accounting is observational rather than a separate admission gate.

## Not implemented

- Multi-process or distributed control-plane state.
- TLS termination, HTTP/2 upstream support, service discovery, tracing, and production-grade admin identity.
- Backend destination allowlisting, isolated admin listener, and persisted dynamic registry changes.
- Automated deployment or publication.

## Validation state

- Original commit `027d578`: 14 tests passed; Ruff reported 77 findings and mypy failed.
- Previous Pulse run: 26 tests passed; Ruff, mypy, Black, compileall, and `git diff --check` passed before the subsequent backend-registration fix. Follow-up regression tests are committed but were not executed in this review environment, so the updated tree is not yet independently test-verified.
- The deterministic identical-workload simulation reduced errors from 118 to 66, while average latency rose from 11.41 ms to 13.47 ms. Raw results are in `docs/benchmarks/`.
- No end-to-end throughput improvement is claimed because the runtime environments were not identical.

## Known risks

- Backend registration can target arbitrary destinations after authentication; production deployments still need destination allowlisting or service discovery.
- Request bodies are buffered, so memory use grows with concurrent large uploads within configured limits.
- Backend mutable state is event-loop-local, not thread-safe or multi-process safe.
- Active health and live traffic still share some backend health counters and EWMA fields.
- Metrics remains unauthenticated on the data-plane listener; isolate it at the network boundary.

## Phase 2 implementation (runtime validation pending)

- Implemented bounded congestion forecasting, predictive scoring, rolling backend/class fingerprints, class-aware eligibility, SLO feedback, objective blending, cooldown, rollback and optional contextual exploration.
- Added authenticated intelligence/autopilot endpoints, sampled recovery ramping, retry protection after streaming starts, and retry exclusion of previously attempted backends.
- Added focused regression tests; the GitHub connector cannot execute Python and the local environment cannot clone GitHub. No new test results or performance gains are claimed.
- Remaining: controlled predictive/reactive comparison, sustainable capacity identification, complete client SLO accounting (admission rejection and some transport errors now captured), forecast calibration, comprehensive recovery/learning tests and validation.
- Added a reproducible synthetic comparison runner: `python -m benchmarks.compare_predictive --seeds 7 11 23`. It has not been executed here; the simulator currently observes outcomes at dispatch rather than completion.

## Phase 3 implementation (validation pending)

- Added isolated seeded queue simulation, HTTP loopback benchmark runner, scheduled synthetic faults, multi-algorithm arena, sampled simulated shadow comparisons, request trace and replay, authenticated lifecycle/SSE/export APIs and bounded persistence.
- Added genuine unit and real HTTP integration tests and a GitHub Actions workflow running the full suite and static analysis. Verify the Actions results for actual pass/fail status; no local full-suite run was possible due to blocked GitHub DNS.
- Remaining: production metadata shadowing, Docker-separated real benchmarking, OS-level faults, resource overhead, recovery time, statistical intervals/regression detection and full runtime validation. Saved-run replay/export and separate adaptive shadow controller snapshots were added; shadow controllers still observe the primary run's outcomes. Simulation and real HTTP results are separately labeled.

CI history: initial pre-Phase-3 workflow reported 43 passing tests on both Python versions, but lint failed. Initial Phase 3 run reported 49 passing tests and one invalid HTTP fixture configuration; the fixture now explicitly specifies allowed concurrency while keeping the original assertions. Subsequent CI is authoritative for the updated code.
