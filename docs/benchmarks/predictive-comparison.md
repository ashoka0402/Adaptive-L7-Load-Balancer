# Phase 2 predictive comparison

Run from the repository root:

```bash
python -m benchmarks.compare_predictive --seeds 7 11 23 --output /tmp/pulse-predictive.json
python -m pytest tests/unit/test_intelligence.py
```

The runner uses identical arrival patterns, fault profiles and random seeds for
reactive and predictive adaptive policies. It reports error counts, latency
percentiles and selection distributions for each seed. It does not assume either
policy wins. The existing simulation uses dispatch-time observations; it is not
a real HTTP experiment. No results are reported until the command is executed.
