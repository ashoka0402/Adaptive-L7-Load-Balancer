# Benchmark API

Authenticated POST /admin/experiments accepts a bounded local specification:
```json
{"mode":"http","algorithms":["round_robin","weighted_round_robin","least_connections","latency_aware"],"rps":50,"duration":3,"concurrency":32,"pattern":"steady","seed":7}
```

GET /admin/experiments/{id} returns state and summaries; append ?full=true for raw records. POST /admin/experiments/{id}/cancel stops the run. Existing history, replay and export endpoints are available for CLI/reproducibility but are not separate UI pages. Old adaptive-era saved specifications are not runnable in this version.

HTTP mode uses freshly created loopback listeners; external targets are not accepted. Simulation models finite-capacity queues with seeded request/backend outcomes. Scheduled synthetic faults are available in the API and offline benchmark config. Neither mode represents independent production machines.

Run `python -m benchmarks.run_arena --output reports/arena.json` or `docker compose -f docker-compose.lab.yml run --rm arena` (create the reports directory first).

On Linux, use `PULSE_UID=$(id -u) PULSE_GID=$(id -g) docker compose -f docker-compose.lab.yml run --rm arena` to write reports as your local user. The runner retains no network and no extra capabilities.
