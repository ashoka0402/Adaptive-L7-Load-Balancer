# Benchmark Results

Measurements are committed as raw JSON. They are evidence for this machine and workload, not general capacity claims.

## Controlled routing simulation

`routing-simulation-seed-7.json` runs both strategies through the same 1,500-request deterministic sequence: normal load, a burst, fast-error partial degradation, and recovery.

| Strategy | Errors | Average | p95 | p99 | Distribution |
|---|---:|---:|---:|---:|---|
| Original EWMA latency-aware | 118 | 11.41 ms | 15.22 ms | 18.81 ms | b1 1034 / b2 425 / b3 41 |
| Pulse adaptive | 66 | 13.47 ms | 19.38 ms | 25.37 ms | b1 686 / b2 483 / b3 331 |

Pulse reduced errors by 44.1% in this fast-failure scenario and avoided starving b3, at the cost of 18.1% higher average latency and higher tail latency. This is a routing simulation, not an HTTP throughput benchmark.

Reproduce it with:

```bash
python benchmarks/compare_routing.py --seed 7 \
  --output docs/benchmarks/routing-simulation-seed-7.json
```

## End-to-end HTTP runs

`original-normal-run-*.json` and `pulse-adaptive-local-run-*.json` use the corrected generator at concurrency 50, with two seconds of warm-up and five seconds measured. All recorded responses were HTTP 200.

The original service was an existing Docker container; Pulse was run as host processes because Docker became unresponsive after isolated baseline validation. Host/container networking and process placement differ, so these files are retained as smoke/performance samples and are **not** an original-versus-Pulse throughput comparison.

The separately audited original commit `027d578` passed 14 tests. Its old benchmark runner produced 203.2, 239.2, and 80.9 reported RPS over three 10-second runs, but that runner divided by requested rather than actual elapsed time and excluded exception latency. Those figures must not be compared to the corrected JSON artifacts.
