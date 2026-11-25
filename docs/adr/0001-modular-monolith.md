# ADR 0001: Modular monolith

Keep a single asynchronous Python process with explicit modules for forwarding, routing, health, resilience, metrics and experiments. Keep persistence off the request path. Avoid distributed state and additional services unless measured requirements justify them. This supports a straightforward local demonstration and testing; horizontal scaling requires an explicit state-sharing design.
