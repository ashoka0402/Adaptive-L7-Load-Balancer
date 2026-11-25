# Interview guide

**What did you build?** An asynchronous HTTP reverse proxy with four selectable load balancing strategies, health checks, circuit breaking, bounded concurrency, connection pooling, live metrics and a benchmark comparison.

**Why four algorithms?** Round Robin is simple, weighted routing represents unequal server allocations, Least Connections reacts to outstanding work and EWMA reacts to measured response latency. Each has trade-offs. Demonstrate actual results instead of assuming a winner.

**How does it handle failure?** Active probes and passive outcomes inform backend health. The circuit breaker limits repeated failed attempts and permits controlled recovery probes. Eligible connection failures can be retried on a different backend before response headers are sent.

**What is backpressure?** Bound active requests and queued admissions; reject excessive work rather than allow unbounded memory growth.

**What did you measure?** Compare the same arrival schedule, seed and backend profiles. Report throughput, p95/p99 latency and errors, and distinguish simulated queues from actual local HTTP. No production-scale claim.

**Why simplify?** A focused, explainable project is easier to validate and demonstrate than an unproven predictive policy engine.
