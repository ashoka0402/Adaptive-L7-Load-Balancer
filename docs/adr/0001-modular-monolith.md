# ADR 0001: Use a modular monolith

- Status: Accepted
- Date: 2026-11-25

## Context

Pulse needs distinct data-plane, control-plane, telemetry, experiment, traffic-generation, persistence, and integration responsibilities. It does not yet need independent deployment, scaling, or ownership for those responsibilities. Routing feedback also benefits from local observations and an inexpensive policy handoff.

## Decision

Keep one deployable Python process and separate concerns with modules and explicit interfaces. Background controller and health tasks share the asyncio lifecycle. External systems remain Prometheus/Grafana and configured HTTP backends. Persistence is an adapter and must stay off the request hot path.

## Consequences

- Local policy reads and telemetry writes are inexpensive.
- Deployment, testing, and failure analysis remain simple.
- Process restart loses unwired in-memory state.
- Horizontal scaling is not transparent; adding workers requires an explicit state-distribution design.
- A future service split requires measured scaling, isolation, or ownership evidence and a new ADR.
