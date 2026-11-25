# Five-minute demo

1. Start Docker Compose and open the Dashboard. Connect with the admin token.
2. Choose Round Robin. Generate 200 requests at concurrency 10. Explain distribution and server latency.
3. Choose Weighted Round Robin and generate another batch. The configured weights are 1:2:1; backend counters are cumulative.
4. Disable one backend, generate traffic again and re-enable it. Explain the difference between manual disable, active health checks and circuit breaking.
5. Open Battle Arena. Run the same isolated HTTP workload for all four strategies and compare measured throughput, p95/p99 and errors. Repeat in simulation mode if desired.

For a real backend failure, run `docker compose stop backend-2`, allow the configured health-check thresholds to be reached, then run `docker compose start backend-2`. No production systems are involved. Never claim one algorithm always wins.

For a command-line test:
```bash
python -m benchmarks.run_benchmarks --url http://127.0.0.1:8080/api/test --concurrency 20 --duration 5
```
