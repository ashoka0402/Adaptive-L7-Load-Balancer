# Pulse console

Open **http://localhost:8080/pulse/** after launching the existing Pulse engine. The proxy's ordinary root path is unchanged. The console uses the same origin and the existing bearer-token control plane. Enter the token configured via `PULSE_ADMIN_TOKEN`; it is held only in page memory, not in local storage. The theme preference alone is stored locally.

## Operating modes

- Overview and Live topology are measured instance-local data. Rates use a bounded one-second sample of completed requests; rolling latency and error statistics use up to 4,096 outcomes in 30 seconds. A saturated sample is a lower bound, not a production-grade rate meter. Backend counters show backend attempts rather than necessarily unique clients.
- Traffic Lab, Arena and Chaos Studio use the existing validated experiment specification and isolated runners. HTTP mode is actual loopback HTTP; simulation is a model. Faults in HTTP mode are synthetic application faults.
- Replay displays recorded frames and measured/simulated completion series, with synchronized playback for identical seeds. Topology motion is a visual sample derived from observed counter differences; it is not a packet capture.
- Autopilot sets the existing objective and feature flags; runtime SLO targets, learning budget, cooldown, weight step and rollback margin can be edited within validated safety limits. Runtime edits are not persisted to YAML. Manual weights and backend capacity/weight/enabled state can be changed from Configuration. Network destinations remain in trusted deployment configuration.
- Shadow estimates are explicitly observational. No predicted benchmark superiority or fake dashboard numbers are shown.

The console is dependency-free ES modules, CSS, Canvas and SVG, packaged with the Python application and available through Docker. The same-origin API avoids a second web service, CORS and a JavaScript build toolchain.

## Verification

GitHub Actions executes JS unit tests and real Chromium browser tests against ephemeral Pulse/backend listeners. Screenshots (landing, live dashboard, battle arena and mobile) and JUnit reports are saved in the `pulse-console-browser-evidence` artifact. Test outcomes are recorded by CI, never manually set to passing.

For a local browser test:

```bash
python -m pip install -e '.[dev]' playwright
python -m playwright install chromium
node --test frontend_tests/model.test.mjs
python -m pytest browser_tests -ra
```

The UI does not yet implement arbitrary topology editing, live OS-level fault injection, or external production deployment. Pause is simulation-only and may be unavailable once a fast simulation finishes.
