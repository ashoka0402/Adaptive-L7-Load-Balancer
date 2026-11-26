# Pulse — Adaptive Layer 7 Load Balancer

Pulse forwards real HTTP traffic to independently running applications. FastAPI is the proxy, HTTPX provides pooled upstream connections, and EWMA latency feedback with an outstanding-request penalty and periodic exploration adjusts routing. There are no generated requests or mock servers in **Live Mode**, which is the default.

## Start Live Mode

```bash
docker compose up --build -d traffic-center
```

Open http://localhost:8080/pulse/ for monitoring. Compose starts three separate application containers that perform real SQLite reads and PBKDF2 CPU computation (no sleep). Send actual application requests:

```bash
curl http://localhost:8080/api/items/7
curl http://localhost:8080/api/items/18
```

For local non-Docker use, install dependencies with `pip install -r requirements.txt`, launch each of the following commands in its own terminal, and then run the proxy:

```bash
python -m backends.workload --id backend-1 --port 8001
python -m backends.workload --id backend-2 --port 8002
python -m backends.workload --id backend-3 --port 8003
python -m app.main
```

### Register your own applications

Edit the trusted route registry in `config/live.local.json` or supply `python -m app.main --config path/to/routes.json`. Each route contains a prefix, strategy and list of origins (id, URL, optional weight, optional health_path). Set health_path to null for passive-only health handling when an origin has no health endpoint. Longer segment prefixes win; path and query are forwarded unchanged. Registry changes take effect on restart. The dashboard's strategy selector changes the first route only; configure other route strategies in the registry. Live traffic comes exclusively from clients; dashboard requests and health probes do not count as proxied application requests. No arbitrary remote backend-registration API is exposed.

Keep the token-free management interface bound to loopback (the default or the Compose loopback port mapping). Do not expose it publicly. Management lives under /pulse/ and Prometheus under /pulse/metrics. The proxy supports HTTP, not WebSocket upgrades. State is single-process; origins and traffic generators are independent processes.

## Prove adaptive behavior with actual measurements

```bash
python -m benchmarks.adaptive_proof --output reports/adaptive-proof.json
```

This command starts independent application and proxy processes and runs a paced external HTTP client. Each strategy sees normal, degraded, and recovery phases; the same SQL query is served throughout, while only origin B changes actual CPU work. No sleep or fabricated latency is injected. Strategy order alternates across repetitions and a new proxy starts for each run. The report records request-level timestamps/status/latency, per-second backend counts, p50/p95/p99, goodput, generator drops, process IDs, and direct-origin versus single-origin proxy measurements. Those last measurements are separate runs, not exact per-request proxy overhead.

The experiment is controlled synthetic traffic hitting real applications, not real user traffic. The JSON output is evidence only after execution; no performance improvement or recovery time is assumed. A healthy but slower backend may still receive occasional requests so it can demonstrate recovery.

## Latency metrics

- Proxy request latency: ingress to completed response streaming, includes upstream wait and downstream streaming.
- Routing decision: time inside the selection operation in microseconds, separately measured.
- Upstream response headers: pool/network/application wait until headers arrive; EWMA uses these observed timings, not browser animation or an artificial delay.
- Loop tick lag: elapsed delay beyond the expected telemetry tick; not CPU utilization.
- Recent topology share: successful completed responses in the last three seconds. The animation is an aggregate visualization, not an individual trace.
- Request X-Ray: first request then 1 in 20; no URL, body, or credentials recorded.

## Laboratory Mode (optional)

```bash
python -m app.main --lab
```

Explicit lab mode starts isolated local test origins and a separate HTTP generator process. All eight features remain: Breaking Point, Chaos Lab, Request X-Ray, scenario Arena, local autoscaling, bounded incident replay, canary rollout and rollback, resilience controls. Fault injection deliberately changes test behavior; lab results are not production user traffic. The dashboard disables destructive experiment controls in Live Mode. To run lab under Docker, use `docker compose run --rm --service-ports --no-deps load-balancer python -m app.main --lab --host 0.0.0.0` after stopping the live service.

## Verification

```bash
python -m pytest tests/unit/test_fastapi_routing.py tests/integration/test_fastapi_lab.py tests/integration/test_fastapi_experiments.py tests/integration/test_live_mode.py -q
python -m benchmarks.adaptive_proof --repetitions 1 --seconds 3 --output reports/adaptive-smoke.json
```

The original aiohttp implementation and historical benchmarks are preserved for comparison with `--legacy`. UI and architectural attribution: [codedpool/load-balancer](https://github.com/codedpool/load-balancer), [MIT notice](LICENSES/codedpool-MIT.txt). Upstream source remains in reference/codedpool. No historical measurement is claimed for this version.

## Traffic Control Center

Open http://localhost:8081/ in a second window alongside http://localhost:8080/pulse/. The center is a separate service that makes actual paced HTTP GET requests to the sample item API through the load balancer, not an in-process simulation. Choose 1–300 offered requests/sec, 5–120 seconds, up to 64 in-flight requests, and constant/ramp/burst patterns. The center shows end-to-end client timings, generator drops, HTTP statuses and successful response distribution. Pulse continues to measure proxy-side traffic, including other external clients.

To demonstrate adaptive routing, select latency-aware on Pulse, start constant traffic, increase backend-2 CPU work in the center, watch redistribution, then restore it. No sleep is injected by the center. Compare the same scenario under Round Robin. Work controls are for the bundled sample applications only. Compose sets an internal shared workload-control token (override with PULSE_BENCHMARK_TOKEN); no token is required in the browser, and the control endpoint is blocked at the proxy. Both browser ports are loopback only. For a formal repeatable comparison, use the independent adaptive_proof runner.
