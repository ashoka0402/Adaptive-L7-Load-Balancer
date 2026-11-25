# Release verification and known limits

Pulse is a production-inspired engineering project, not a certified or internet-ready service. Keep the default deployment local.

## Evidence

GitHub Actions is the source of truth for this commit. The verification workflow runs the entire pytest suite on Python 3.12 and 3.13, lint/type checking, six JavaScript model tests, real Chromium integration, a seeded Arena smoke run, a historical HTTP comparison and a Docker smoke/isolated Arena job. Artifacts contain JUnit, coverage, screenshots and raw benchmark JSON. A successful earlier workflow does not certify a newer commit.

- Prior Phase 4 workflow: https://github.com/ashoka0402/Adaptive-L7-Load-Balancer/actions/runs/45621598569
- Latest runs: https://github.com/ashoka0402/Adaptive-L7-Load-Balancer/actions

## Hardening decisions

- Dynamic backend additions can target only exact DNS-name/port pairs in the operator-controlled startup configuration or `allowed_dynamic_destinations`. Never expose the admin API to untrusted users. DNS and network egress policy remain trusted deployment dependencies; this is not DNS-rebinding protection.
- Health probes and forwarding do not follow redirects. The shared client session does not retain cookies; compressed response bytes remain encoded. Reserved admin paths do not fall through to upstreams.
- Cancelled queue waiters release queue occupancy. Zero queue length permits idle capacity but rejects overload. Connection limits apply globally and per host; SQLite connections close deterministically.
- The UI credential stays in page memory. It is not forwarded when it matches the admin token. Request paths are omitted from structured completion logs.
- Docker binds host-facing services to loopback. For remote deployments, use TLS, network segmentation, separate control-plane access, secret rotation, egress controls, process limits, and an independently audited authentication layer.

## Remaining limitations

Single-process single-event-loop control and state; no HA or multi-region design. No WebSockets/CONNECT proxy upgrades, native TLS termination or HTTP/2 data plane. Retry after response headers is intentionally prohibited. Request bodies are buffered. DNS allowlists depend on trusted operator-controlled resolution. Prometheus metrics in isolated in-process HTTP experiments share process-level instrumentation. Per-backend metrics label churn, memory/CPU isolation, formal long-duration soak, rate-limiting administrative operations, and comprehensive production attack/load testing remain open. Forecast confidence is a fit heuristic; observed capacity is not maximum sustainable throughput. Shadow estimates are not causal. Online learning remains opt-in; no universal improvement is claimed. Synthetic network/resource faults do not alter OS resources. SLO edits are runtime-only; persistent configuration stays operator-managed.

## Release procedure

Review the latest exact-commit CI status and artifacts; run the guided demo, inspect actual browser screenshots, and review changes before creating a public version tag. The repository is prepared for review, not automatically published as a release.
