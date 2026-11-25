# Engineering interview guide

## Project description

Pulse is a Python asyncio Layer 7 HTTP reverse proxy with pluggable baseline routing, immutable adaptive policy snapshots, bounded telemetry, forecast-assisted routing, an authenticated control plane, and a replayable simulation/HTTP benchmarking console.

## Resume bullets (implementation claims, not unverified speed claims)

- Built an asynchronous L7 reverse proxy with five selectable routing strategies, feedback-controlled policy publication, bounded admission, circuit breaking and connection reuse.
- Implemented a seeded experiment arena covering eight routing modes, scheduled fault injection, deterministic replay and JSON/CSV export, with separate simulated and measured HTTP execution.
- Developed a same-origin operations console with sampled animated topology, latency/throughput charts, synchronized policy playback and authenticated runtime SLO controls.
- Integrated Python matrix CI, static analysis, JavaScript and real-browser tests, raw benchmark artifacts and isolated Docker checks. Cite counts and results only from the exact passing workflow used in an application.

## Technical questions and model answers

**Why is EWMA alone not adaptive enough?** A latency-only selector is reactive, can concentrate traffic, and can mistake fast failures for good service. Pulse combines normalized latency, active load relative to capacity, queue, errors and optional resources. A periodic controller adjusts probabilities rather than greedily choosing the minimum.

**How is the policy computed?** For each eligible backend, a weighted normalized pressure score is transformed by an exponential penalty; backend weight and slow start influence the target. Exploration, smoothing and projection bound probabilities and changes. Prediction adds confidence-weighted future pressure. Equal scores do not imply identical capacities.

**What keeps the hot path fast?** An immutable snapshot is published via a reference swap on one event loop. Selection uses the published probabilities and bounded class adjustments. Aggregation and regression run periodically, not on every request. Current selection scans the eligible set; it is not O(1).

**What happens when a client disconnects?** Admission uses a bounded semaphore and cancellation-safe queue accounting. The reverse proxy decrements counters in finally blocks and releases a reserved recovery probe. A response that has already started is never retried.

**Why is shadow evaluation not a real counterfactual?** It evaluates alternatives on the primary policy's observed state. It never sends duplicate production requests. A separate trajectory would require different resulting queues and service outcomes, so estimates must remain labeled.

**How do you fairly compare strategies?** Use the same seeded arrival trace and fault schedule for simulation, and the same generator, machine and backend topology for HTTP runs. Record all outcomes, elapsed time, warmup and environment. Repeat and rotate order. Do not compare unmatched historical throughput or assume the adaptive policy wins.

**What is the key limitation?** In-process state and bounded windows are appropriate for this prototype but not distributed HA. Capacity inference, causal learning, long-duration testing, network egress isolation and external production security remain separate engineering problems.
