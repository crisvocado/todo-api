import logging
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_trigger_error_does_not_log_error(caplog):
    with caplog.at_level(logging.INFO):
        response = client.post("/trigger-error")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Log received server-side"}

    error_records = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert len(error_records) == 0, f"Expected no ERROR log records, got: {error_records}"
