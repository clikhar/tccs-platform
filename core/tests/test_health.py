from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_conference_routes_are_registered() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/v1/group-calls" in paths
    assert "/api/v1/general-calls" in paths
    assert "/api/v1/calls/{call_id}/participants/{extension}/mute" in paths
    assert "/api/v1/calls/{call_id}/participants/{extension}/unmute" in paths
    assert "/api/v1/calls/{call_id}/participants/{extension}/disconnect" in paths
