# Guided demo: observe, disrupt, verify

Allow roughly three to five minutes after the initial Docker build. The time is a suggested demonstration pace, not a benchmark result.

1. Set strong local secrets and start `docker compose up --build`. Open http://localhost:8080/pulse/ and connect using the admin token.
2. Overview: generate a few ordinary HTTP requests to `/api/test` and observe the live nodes and routing explanations. Do not confuse this with simulated traffic.
3. Traffic Lab: choose simulation, 240 RPS, 8 seconds, burst pattern, seed 7, Round Robin, Pulse Adaptive, and Pulse Predictive. Add a fourth backend with capacity 5 and latency 45 ms. Use the burst + failure preset, or schedule a latency spike at 2.5s, shutdown at 4s and restore at 6s.
4. Launch. Read the actual performance ledger, error and tail latency metrics. In the synchronized comparison scrub to before/after failure. Inspect route-count density, queues and capacity. Note any policy that outperforms Pulse.
5. Traffic Replay: select the experiment and replay exactly the same specification. Change playback speed and seek the fault markers. Export raw JSON/CSV.
6. Autopilot: inspect objective, SLO targets and observed history. Explain why forecast confidence and bounded changes matter. Do not enable online learning without a reason and guardrails.
7. Optional real HTTP experiment: choose HTTP mode, 50 RPS, 4 seconds, concurrency 24, one repeat, two algorithms. This uses isolated local listeners and synthetic application faults, not the live fleet.
8. Finish with the [Actions evidence](https://github.com/ashoka0402/Adaptive-L7-Load-Balancer/actions) and [benchmarks](benchmarks/README.md). Present real values from the actual run, including unfavorable outcomes.

The UI is a sampled visualization, not packet capture. The demo has no predetermined winner.
