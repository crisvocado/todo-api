import logging
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_trigger_error_does_not_log_error(caplog):
    caplog.set_level(logging.INFO)
    response = client.post("/trigger-error")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Log received server-side"}

    error_logs = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(error_logs) == 0, f"Unexpected error logs found: {[r.message for r in error_logs]}"
    assert "Test server error: something broke in the TODO API!" not in caplog.text
