# Benchmark methodology and evidence

Use Battle Arena to compare all four algorithms on the same seeded arrivals and backend profiles. HTTP mode measures real local requests; simulation models queueing. Both report offered requests, successful throughput, latency percentiles and errors. Raw results are available through the authenticated API and CI artifacts.

The release workflow compares historical EWMA, current EWMA and current Least Connections on the same runner, three synthetic backend service delays and the same closed-loop generator. Current EWMA uses a simpler stable tie-break. See the exact run's artifact for measured results; do not interpret this as maximum sustainable capacity.

The committed JSON files in this directory record the previous adaptive-engine experiments, not current performance. Those historical results include different runtime environments and simulation trade-offs; they are retained as historical evidence and should not be presented as current measurements. The corresponding implementation is available in Git history.
