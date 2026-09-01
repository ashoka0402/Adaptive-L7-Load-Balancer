"""Locust load test against the load balancer."""

from locust import HttpUser, between, task


class LBUser(HttpUser):
    wait_time = between(0.01, 0.05)

    @task(10)
    def get_test(self):
        self.client.get("/api/test", name="/api/test")

    @task(2)
    def post_test(self):
        self.client.post("/api/test", json={"hello": "world"}, name="/api/test POST")

    @task(1)
    def health_via_lb(self):
        # Hits LB which routes to a backend health if path is /health on backend
        # Our backends expose /health; LB will forward /health too.
        self.client.get("/health", name="/health")
