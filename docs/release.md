# Security and limitations

Pulse is a local learning and interview project. The Compose proxy port binds to loopback. Use a unique bearer token, and keep management and metrics on a trusted network. Dynamic backend destinations are restricted to startup-configured hosts and ports; DNS and network egress remain trusted. No native TLS, HTTP/2 or WebSocket upgrades. The proxy buffers bounded request bodies and streams responses. Safe retries stop once response headers are committed.

One asyncio process owns registry and circuit state. Runtime strategy and backend changes do not persist to YAML. SQLite keeps the most recent eight experiment results. Old policy tables are not modified or read; no existing volume is deleted as part of the simplification.

HTTP Arena uses synthetic loopback backends in the same host; process-level Prometheus counters may include its activity. Live dashboard counters are instance-local. Latency and throughput depend on workload, runtime and hardware.

No production-readiness or new benchmark superiority claim is made. Check exact-commit CI evidence for validation.
