# Pulse Engineering Rules

These rules apply to every future Codex session in this repository. More specific `AGENTS.md` files may add local constraints.

## Product boundary

Pulse is a single-process, asynchronous L7 traffic engine. Keep it a modular monolith unless measured requirements prove that a process boundary is necessary. Preserve the selectable baseline strategies (`round_robin`, `weighted_round_robin`, `least_connections`, and `latency_aware`).

## Required workflow

1. Inspect the affected request path, callers, configuration, and tests before editing.
2. Prefer the smallest complete change that reuses existing modules and dependencies.
3. Give concurrent agents non-overlapping file ownership.
4. Run focused tests, then the full suite. Run Ruff and mypy when the change touches typed production code.
5. Record benchmark commands, workload, environment, and raw output. Never invent or extrapolate measurements.
6. Update `ARCHITECTURE.md`, `IMPLEMENTATION_STATUS.md`, or an ADR when behavior or a durable design decision changes.

## Data-plane invariants

- Do not add blocking I/O or aggregate routing calculations to the request hot path.
- Bound queues, retries, request bodies, telemetry windows, and traffic generation.
- Keep retries idempotent and decide them before committing a streamed client response.
- Increment and decrement in-flight counters exactly once, including cancellation and error paths.
- Treat health probes and application observations as distinct signals when changing their models.

## Concurrency and state

- The current safety model is one asyncio event loop and event-loop-local mutation. Do not imply thread or multi-process safety.
- Do not hold locks across network I/O.
- New worker-process support requires an explicit design for registry, circuit, telemetry, and policy state.
- Persistence belongs off the hot path and must fail without stopping request forwarding unless configured otherwise.

## Security

- Never commit credentials or use the sample admin token outside local development.
- Treat admin payloads, backend addresses, forwarded headers, and telemetry as untrusted input.
- Validate backend destinations and resource bounds before supporting an internet-exposed control plane.
- Strip static and `Connection`-nominated hop-by-hop headers on both request and response paths.
- Do not expose upstream exception details to clients.
- Do not deploy, publish, delete production data, or weaken security controls without explicit approval.

## Tests and benchmarks

- Proxy changes require integration coverage for forwarding, streaming, retries, cancellation, and counter cleanup.
- Concurrency-sensitive fixes require a regression test that fails under the old behavior.
- Store measured reports under `docs/benchmarks/`; label simulations separately from end-to-end HTTP measurements.
- Report failed and skipped checks, including unavailable Docker or load-test infrastructure.

## Documentation

Describe verified behavior, not intent. Mark unwired adapters and planned modules clearly. Keep operational commands copyable and keep known limitations current.
