from fastapi.testclient import TestClient

from app import main


client = TestClient(main.app)


def test_liveness_does_not_require_database() -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_reports_database_failure(monkeypatch) -> None:
    async def database_down() -> bool:
        return False

    monkeypatch.setattr(main, "check_database", database_down)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "database unavailable"
