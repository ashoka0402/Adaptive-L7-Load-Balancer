# Console

Start the proxy and visit `/pulse/`. Connect using the configured `PULSE_ADMIN_TOKEN`. The token is held only in page memory.

Dashboard: four live metrics, two rolling charts, backend health and distribution, routing algorithm selector and bounded traffic generation. Select a strategy and send actual HTTP requests. Disable/re-enable a server to demonstrate traffic exclusion. Runtime changes reset when the process restarts.

Battle Arena: select HTTP or simulation, request rate, duration and steady or burst traffic. Run all four strategies with the same seed and backend profiles. Inspect throughput and p95 latency charts, p99 and errors in the table. One experiment runs at a time. Cancel stops the isolated experiment.

Live latency is based on up to 4,096 outcomes in 30 seconds. Throughput is a one-second sampled count. The browser traffic generator sends only to the same-origin demo endpoint, up to 1,000 requests with at most 50 browser workers. Stop prevents new requests but allows in-flight work to finish.

Browser verification:
```bash
python -m pip install -e '.[dev]' playwright
python -m playwright install chromium
node --test frontend_tests/model.test.mjs
python -m pytest browser_tests -ra
```
