from unittest.mock import patch
from fastapi.testclient import TestClient


def test_health_all_ok(client: TestClient) -> None:
    with patch("app.api.health.check_redis", return_value=True):
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "redis": "ok"}


def test_health_response_has_required_keys(client: TestClient) -> None:
    with patch("app.api.health.check_redis", return_value=True):
        response = client.get("/health")
    body = response.json()
    assert "status" in body
    assert "db" in body
    assert "redis" in body


def test_health_redis_down(client: TestClient) -> None:
    with patch("app.api.health.check_redis", return_value=False):
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["redis"] == "error"
    assert body["status"] == "error"
    assert body["db"] == "ok"


def test_health_db_down(client_broken_db: TestClient) -> None:
    with patch("app.api.health.check_redis", return_value=True):
        response = client_broken_db.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["db"] == "error"
    assert body["status"] == "error"
    assert body["redis"] == "ok"
