import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import logcore
from logcore_middleware import LogcoreMiddleware


@pytest.fixture
def logcore_env(monkeypatch):
    monkeypatch.setenv("LOGCORE_SERVICE_ID", "792548c2-c128-460c-82ca-f26b9205b5cd")
    monkeypatch.setenv("LOGCORE_ENV", "dev")
    monkeypatch.setenv("LOGCORE_SOURCE_PROJECT", "persea-dev-2")


def emitted_entry(capsys):
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one JSON line, got {lines}"
    return json.loads(lines[0])


def test_log_puts_env_and_source_project_under_the_promoted_labels_key(
    logcore_env, capsys
):
    logcore.log("ERROR", "boom")

    entry = emitted_entry(capsys)
    assert entry["logging.googleapis.com/labels"] == {
        "env": "dev",
        "source_project": "persea-dev-2",
    }
    # A top-level env stays inside jsonPayload and every issue records "unknown".
    assert "env" not in entry
    assert "insert_id" not in entry


def test_insert_id_is_32_hex_chars(logcore_env, capsys):
    logcore.log("ERROR", "boom")

    insert_id = emitted_entry(capsys)["logging.googleapis.com/insertId"]
    assert len(insert_id) == 32
    assert all(char in "0123456789abcdef" for char in insert_id)


def test_error_stack_is_parsed_frames_innermost_first():
    def inner():
        raise ValueError("inner exploded")

    def outer():
        inner()

    try:
        outer()
    except ValueError as exc:
        error = logcore.error_from_exception(exc)

    assert error["type"] == "ValueError"
    assert [frame["function"] for frame in error["stack"]][:2] == ["inner", "outer"]
    assert all(isinstance(frame, dict) for frame in error["stack"])


def test_error_stack_follows_the_cause_chain_to_its_root():
    def read_row():
        raise KeyError("row missing")

    try:
        try:
            read_row()
        except KeyError as cause:
            raise RuntimeError("could not load todo") from cause
    except RuntimeError as exc:
        error = logcore.error_from_exception(exc)

    # The root cause leads, so the issue keys on where it actually broke rather
    # than on the layer that re-raised.
    assert error["stack"][0]["function"] == "read_row"
    # type and message stay those of the exception actually raised.
    assert error["type"] == "RuntimeError"


def test_log_is_a_noop_without_a_service_id(monkeypatch, capsys):
    monkeypatch.delenv("LOGCORE_SERVICE_ID", raising=False)
    monkeypatch.setattr(logcore, "_warned_about_missing_service_id", False)

    logcore.log("ERROR", "boom")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "LOGCORE_SERVICE_ID" in captured.err


def test_middleware_logs_unhandled_exceptions_and_reraises(logcore_env, capsys):
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise ValueError("kaboom")

    app.add_middleware(LogcoreMiddleware)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    entry = emitted_entry(capsys)
    assert entry["severity"] == "ERROR"
    assert entry["error"]["type"] == "ValueError"
    assert entry["context"] == {"method": "GET", "path": "/boom"}


def test_middleware_does_not_log_handled_http_errors(logcore_env, capsys):
    app = FastAPI()

    @app.get("/missing")
    def missing():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="nope")

    app.add_middleware(LogcoreMiddleware)
    client = TestClient(app)

    assert client.get("/missing").status_code == 404
    assert capsys.readouterr().out == ""
