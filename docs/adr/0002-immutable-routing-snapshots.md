# ADR 0002: Publish immutable adaptive routing snapshots

- Status: Accepted
- Date: 2026-11-25

## Context

Multidimensional normalization, capacity estimation, smoothing, and explanations are too expensive and stateful to recompute for every request. Reading mutable controller structures during publication could also produce inconsistent routing decisions.

## Decision

A periodic controller aggregates bounded telemetry and constructs a complete, frozen `RoutingPolicySnapshot`. Publication replaces one in-process reference. Adaptive request routing reads the current snapshot and performs a weighted random choice among eligible backends. Baseline routing strategies remain selectable.

Policies include version, timestamp, per-backend score, probability, effective capacity, normalized metrics, and a compact explanation. Exploration, minimum probability, slow start, smoothing, and maximum probability movement constrain the feedback loop.

## Consequences

- The hot path avoids normalization and score calculation.
- Requests observe either the previous or next complete policy, never a partial update.
- Controller interval adds bounded reaction delay.
- Snapshot immutability does not make the rest of backend state thread-safe.
- Multi-process deployment will need snapshot distribution and version coordination.
