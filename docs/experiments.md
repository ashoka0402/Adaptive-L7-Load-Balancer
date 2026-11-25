# Lab, Chaos, Arena and Replay

All experiment APIs use the existing bearer admin token. The experiment server
creates its own registry and never accepts a target URL. HTTP mode uses new
ephemeral loopback backend/proxy listeners; it never targets the live registry.
Resource/network faults are synthetic capacity/latency changes, not OS network
or CPU exhaustion. HTTP mode is measured local HTTP; simulation is a model.

POST /admin/experiments accepts:
```json
{"mode":"simulation","algorithms":["round_robin","adaptive","predictive","autopilot"],
 "rps":300,"duration":5,"pattern":"burst","seed":7,
 "faults":[{"at":2,"backend":"b1","kind":"down"},{"at":3,"backend":"b1","kind":"restore"}]}
```

GET /admin/experiments lists recent experiments; GET /admin/experiments/{id}?full=true
returns request records and timeline frames. GET /admin/experiments/{id}/stream
streams status via SSE. POST /admin/experiments/{id}/pause, resume, cancel, replay
and reset manage the run; pause/resume are simulation only. Replay creates a new
experiment with identical input and seeds. GET /admin/experiments/{id}/export?format=csv
exports per-request records. Persisted completed results can be read by ID.
The simulator records modeled arrivals, queueing, completions, timeouts, faults and
routing outcomes; no sensitive traffic payloads are collected.

Shadow selection is sampled only within simulations. It uses observed primary-state
candidates, estimates queue delay and configured error-rate deltas, and never sends
duplicate requests. Adaptive shadow policies currently share the primary controller
snapshot, so they are not independent adaptive counterfactual controllers.

Arena uses repeated identical seeded traces and separate runtime state. Reports
include success rate, offered/successful RPS, latency percentiles, drops, timeouts,
SLO violations, distribution, fairness, peak simulated queue and policy changes.
Ranking uses one configured objective and descriptive sample standard deviation.
No benchmark winner is assumed; confidence intervals, resource profiling and formal
recovery time estimation remain future work. The real HTTP mode has wall-clock
scheduling variation, synthetic application faults and shared process-level metrics;
it is not a controlled deployment performance claim.

Use GitHub Actions "Pulse verification" for actual pytest, coverage, lint and type
check outcomes. No test or benchmark results are fabricated.
