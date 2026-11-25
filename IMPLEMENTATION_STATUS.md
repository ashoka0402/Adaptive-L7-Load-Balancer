# Implementation status

FastAPI is the default entrypoint. The Python port provides eight routing strategies, route prefix matching, pooled HTTP forwarding, fixed admission, bounded safe-method retries, active health checks, circuit breakers, SSE metrics, real HTTP traffic generation, local backend controls, theme switching and a real HTTP comparison arena. Legacy aiohttp code, tests and historic measurements are retained separately from the active runtime. The upstream MIT notice is retained.

Not ported: Redis/shared rate limits, multiprocess clustering, hedged requests, adaptive admission and automatic infrastructure scaling. New tests are provided; runtime and benchmark results must be verified by running the commands in README. No historical performance claims apply to this version.
