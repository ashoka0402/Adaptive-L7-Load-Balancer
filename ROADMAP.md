# Pulse Roadmap

Roadmap order follows risk and evidence, not feature count.

## Now: deepen integration and concurrency validation

- Add end-to-end proxy integration tests with real ephemeral `aiohttp` backends.
- Cover bursts, partial degradation, recovery, concurrent counter cleanup, fairness, starvation, slow start, and hysteresis.
- Repeat identical end-to-end workloads in one recovered Docker environment; the committed host/container measurements are intentionally not compared.
- Add cancellation tests and concurrent registry mutation coverage.

## Next: safe operations and experiments

- Add backend destination allowlisting or service discovery policy.
- Isolate or authenticate metrics and move admin traffic to a dedicated listener.
- Add admin access to persisted experiment summaries and policy history.
- Extend deterministic scenarios with timeout, queue saturation, and slow-start recovery cases.
- Add routing probability, score, capacity, and signal panels to the existing dashboard.

## Later: evidence-driven performance work

- Stream large request bodies when retries are disabled or an explicit replay buffer is available.
- Enforce aggregate and per-backend connection budgets rather than relying only on connector queuing.
- Add retry budgets and deadline propagation.
- Profile allocation, event-loop lag, connector contention, and controller cost before optimizing.
- Evaluate multi-process policy distribution only after a single process is measured as the limiting resource.

## Explicit non-goals

- Reimplementing Envoy or Nginx.
- Splitting modules into services without an independent scaling or trust-boundary requirement.
- Machine-learning routing before deterministic feedback control is measured and understood.
- Claiming production readiness from synthetic benchmarks alone.
