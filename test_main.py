import logging
from fastapi.testclient import TestClient
from main import app, init_db

init_db()
client = TestClient(app)


def test_trigger_error_does_not_log_error(caplog):
    with caplog.at_level(logging.INFO):
        response = client.post("/trigger-error")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Log received server-side"}

    error_records = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert len(error_records) == 0, f"Expected no ERROR log records, got: {error_records}"


def test_create_todo_with_non_ascii_title():
    response = client.post("/todos", json={"title": "Buy \U0001f951"})
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Buy \U0001f951"
    assert "slug" in data
    assert data["slug"] == "buy-"

