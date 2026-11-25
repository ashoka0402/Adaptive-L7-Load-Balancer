# ADR 0003 — Predictive intelligence and bounded Autopilot

The controller samples normalized connection and external queue pressure at its
configured interval. Least-squares regression estimates pressure at a configurable
horizon. Confidence is R-squared scaled by sample count, not calibrated uncertainty.
Predicted congestion adds a bounded penalty to the existing adaptive pressure score.

Ordered method/path/header rules identify request classes and optionally intersect
backend eligibility with a configured allowlist. Class-specific rolling latency/error
estimates apply bounded routing multipliers. Health and circuit checks remain mandatory.

Autopilot convexly blends normalized weights toward objective presets. SLO violations
override preferences, a cooldown limits changes, and increased observed loss triggers
rollback. Optional contextual epsilon-greedy selection uses two pressure contexts
and four presets. It is disabled by default and cannot explore during observed SLO
violations. This feedback is observational, not causal evidence of improvement.

Fingerprints report observed latency/error distributions, peak concurrency and
successful RPS lower bounds, not maximum sustainable capacity. Client SLOs cover
completed adaptive proxy responses, admission rejections and some transport errors;
not all disconnects or admission cancellation paths are observed.
Availability means response success fraction, not uptime. Recovery ramping follows
sampled availability transitions. Retries cannot repeat a tried backend or continue
after a response starts.

GET /admin/intelligence and PATCH /admin/autopilot use existing bearer authentication.
Configuration, observations and history are process-local. Controlled comparative
benchmarks and runtime validation remain required.
