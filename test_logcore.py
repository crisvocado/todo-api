import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from logcore_logger import error_from_exception
from logcore_middleware import LogcoreMiddleware


def build_failing_app():
    app = FastAPI()
    app.add_middleware(LogcoreMiddleware)

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    return app


def test_main_app_registers_the_middleware():
    registered = [middleware.cls for middleware in main.app.user_middleware]
    assert LogcoreMiddleware in registered


def test_unhandled_exception_emits_one_json_line(capsys, monkeypatch):
    monkeypatch.setenv("LOGCORE_SERVICE_ID", "service-under-test")
    monkeypatch.setenv("LOGCORE_ENV", "test")
    client = TestClient(build_failing_app(), raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    entry = json.loads(capsys.readouterr().out.strip())
    assert entry["severity"] == "ERROR"
    assert entry["service_id"] == "service-under-test"
    assert entry["logging.googleapis.com/labels"]["env"] == "test"
    assert len(entry["logging.googleapis.com/insertId"]) == 32
    assert entry["error"]["type"] == "RuntimeError"
    assert entry["context"] == {"method": "GET", "path": "/boom"}


def test_nothing_is_emitted_without_a_service_id(capsys, monkeypatch):
    monkeypatch.delenv("LOGCORE_SERVICE_ID", raising=False)
    client = TestClient(build_failing_app(), raise_server_exceptions=False)

    client.get("/boom")

    assert capsys.readouterr().out == ""


def test_stack_puts_the_root_cause_first():
    def read_row():
        raise ValueError("no such column: titl")

    try:
        try:
            read_row()
        except ValueError as cause:
            raise RuntimeError("could not list todos") from cause
    except RuntimeError as exc:
        error = error_from_exception(exc)

    assert error["type"] == "RuntimeError"
    assert error["stack"][0]["function"] == "read_row"
    assert error["stack"][0]["inApp"] is True
