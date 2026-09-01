# Adaptive L7 Load Balancer

An asynchronous, fault-tolerant **Layer 7 HTTP load balancer** built in Python.

It features adaptive latency-aware routing, multiple routing strategies, health-based failover, circuit breaking, connection pooling, backpressure, dynamic backend management, Prometheus/Grafana observability, and benchmark-driven design.

> Portfolio / systems-engineering project. Not a production internet-facing proxy.

---

## Architecture

```
Clients
   |
   v
+---------------------------+
| Adaptive L7 Load Balancer |
|  - HTTP reverse proxy     |
|  - Routing engine         |
|  - Health monitor         |
|  - Circuit breaker        |
|  - Connection pool        |
|  - Backpressure           |
|  - Metrics                |
+------------+--------------+
             |
   +---------+---------+
   |         |         |
   v         v         v
Backend-1 Backend-2 Backend-3
   |         |         |
   +---------+---------+
             |
             v
        Prometheus → Grafana
```

---

## Features

| Area | Capability |
|------|------------|
| Proxy | Full HTTP method support, header handling, streaming, X-Request-ID |
| Routing | Round Robin, Weighted RR, Least Connections, Latency-Aware (EWMA) |
| Health | Active `/health` probes + passive traffic learning |
| Resilience | Circuit breaker (CLOSED/OPEN/HALF_OPEN), controlled retries, timeouts |
| Performance | Connection pooling, keep-alive, backpressure / load shedding |
| Ops | Dynamic backend add/remove/drain via admin API |
| Observability | Structured JSON logs, Prometheus metrics, Grafana dashboard |

---

## Quick Start

```bash
docker compose up --build
```

| Service | URL |
|---------|-----|
| Load Balancer | http://localhost:8080 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / admin) |

Grafana dashboard **Adaptive L7 Load Balancer** is auto-provisioned.

### CLI (local, without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Start backends in separate terminals
python -m backends.server --port 8001 --id backend-1
python -m backends.server --port 8002 --id backend-2 --mode slow --slow-ms 100
python -m backends.server --port 8003 --id backend-3
# Edit config/config.yaml hosts to 127.0.0.1
python -m app.main -c config/config.yaml
```

---

## Load-Balancing Algorithms

### Round Robin
Cycles evenly through available backends. O(1).

### Weighted Round Robin
Smooth WRR (Nginx-style). Distribution ≈ weight proportions without list duplication.

### Least Connections
Selects backend with fewest active connections (tie-break: latency, id).

### Latency-Aware
Score = `EWMA_latency × (1 + active_conns / capacity)`.  
Avoids stampeding the currently fastest backend. Small exploration term prevents starvation.

---

## Reliability

- **Active health checks**: configurable interval / thresholds → HEALTHY / UNHEALTHY
- **Passive monitoring**: timeouts, connection errors, 5xx influence state
- **Circuit breaker**: failure threshold → OPEN → recovery timeout → HALF_OPEN → CLOSED
- **Retries**: only GET/HEAD by default, limited attempts, retryable status codes
- **Graceful shutdown**: SIGTERM stops accept, drains in-flight, closes pools

---

## Admin API

All mutating endpoints require `Authorization: Bearer <admin_token>`.

```bash
# List
curl http://localhost:8080/admin/backends

# Add
curl -X POST http://localhost:8080/admin/backends \
  -H "Authorization: Bearer admin-secret-token-change-me" \
  -H "Content-Type: application/json" \
  -d '{"host":"backend-4","port":8004,"weight":2}'

# Drain
curl -X PATCH http://localhost:8080/admin/backends/backend-2 \
  -H "Authorization: Bearer admin-secret-token-change-me" \
  -H "Content-Type: application/json" \
  -d '{"status":"DRAINING"}'

# Delete
curl -X DELETE http://localhost:8080/admin/backends/backend-2 \
  -H "Authorization: Bearer admin-secret-token-change-me"
```

---

## Configuration

See `config/config.yaml`. Validated with Pydantic at startup (fail-fast).

Key sections: `server`, `load_balancing.strategy`, `backends`, `health_check`, `circuit_breaker`, `connection_pool`, `timeouts`, `retry`, `backpressure`.

---

## Observability

### Metrics (`/metrics`)

- `lb_requests_total`, `lb_request_latency_seconds`, `lb_backend_latency_seconds`
- `lb_backend_active_connections`, `lb_backend_health_status`, `lb_backend_circuit_state`
- `lb_timeouts_total`, `lb_retries_total`, `lb_rejected_requests_total`, `lb_queue_size`

### Structured logs

```json
{"timestamp":"...","request_id":"...","method":"GET","path":"/api/test","backend":"backend-1","status_code":200,"latency_ms":12.3,"retry_count":0}
```

---

## Load Testing

```bash
# Against running LB
locust -f tests/load/locustfile.py --host http://localhost:8080

# Or simple concurrent benchmark
python benchmarks/run_benchmarks.py --url http://localhost:8080/api/test --concurrency 100 --duration 15
```

---

## Tests

```bash
pytest tests/unit -q
```

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Python + asyncio | Clear code for interviews; native concurrency without threads-per-request |
| L7 | Inspect method/path/headers/status; enable intelligent routing |
| EWMA latency | Smooth noise; adapt without overreacting to single samples |
| Circuit breaker | Fail fast on bad backends; protect remaining capacity |
| Bounded queue + 503 | Controlled degradation vs unbounded latency |
| Restricted retries | Avoid storms and non-idempotent replay |
| Own routing / health / CB | Core concepts implemented in-project, not hidden behind a library |

---

## Project Structure

Matches the specification under `app/`, `backends/`, `tests/`, `monitoring/`, `config/`, `docker/`.

---

## License

MIT (portfolio use)
