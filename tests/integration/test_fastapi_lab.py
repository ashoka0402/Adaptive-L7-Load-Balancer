from fastapi.testclient import TestClient

from app.fastapi_lb.server import create_app


def test_fastapi_real_http_proxy_and_controls():
    with TestClient(create_app(), base_url="http://localhost") as client:
        assert client.get("/pulse/").status_code == 200
        assert client.post("/api/strategy", json={"strategy": "latency_aware"}).status_code == 403
        headers = {"X-Pulse-Local": "1"}
        assert client.post(
            "/api/strategy", headers=headers, json={"strategy": "latency_aware"}
        ).status_code == 200
        response = client.get("/api/test?sample=1")
        assert response.status_code == 200
        assert response.headers["x-backend"] in "ABCD"
        assert client.get("/admin/console").json()["totals"]["total"] >= 1
        assert client.post("/api/backend/add", headers=headers).status_code == 200
        assert len(client.get("/admin/console").json()["backends"]) == 5
        assert client.post("/api/backend/A/toggle", headers=headers).status_code == 200
        assert client.get("/admin/console").json()["backends"][0]["dead"]
        assert client.get("/admin/console", headers={"Host": "example.com"}).status_code == 403
        assert client.get("/metrics").status_code == 200
