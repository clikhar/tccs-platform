from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_call_returns_call_id() -> None:
    response = client.post(
        "/api/v1/calls",
        json={"source": "1001", "target": "2001", "mode": "individual"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "initiated"
    assert body["source"] == "1001"
    assert body["target"] == "2001"
    assert body["call_id"]
