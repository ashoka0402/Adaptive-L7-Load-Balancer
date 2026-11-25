# Contributing to Pulse

Read [AGENTS.md](AGENTS.md) and [architecture](ARCHITECTURE.md). Create a focused branch, reproduce the behavior with a meaningful test, implement the smallest complete fix and submit a pull request. Keep hot-path operations bounded; never fake benchmark outcomes or weaken assertions to pass CI.

```bash
python -m pip install -e '.[dev]'
python -m pytest -ra
ruff check .
mypy app
node --test frontend_tests/model.test.mjs
```

For browser checks install Playwright and Chromium as described in [console documentation](docs/console.md). Explain design choices and limitations. Include raw measurements for any performance claims and keep simulation separate from HTTP measurements.
