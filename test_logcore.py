import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import logcore_logger
from logcore_middleware import LogcoreMiddleware


SERVICE_ID = "92be1903-39cf-41eb-9046-3989ef7f5be2"


@pytest.fixture
def logcore_env(monkeypatch):
    monkeypatch.setenv("LOGCORE_SERVICE_ID", SERVICE_ID)
    monkeypatch.setenv("LOGCORE_ENV", "dev")
    monkeypatch.setenv("LOGCORE_SOURCE_PROJECT", "seed-prod-b9508c89")
    monkeypatch.setenv("LOGCORE_SERVICE_NAME", "todo-api")


def raise_wrapped():
    try:
        sqlite3.connect(":memory:").execute("SELECT * FROM missing_table")
    except sqlite3.OperationalError as driver_error:
        raise RuntimeError("no se pudo leer la lista de tareas") from driver_error


def test_entry_matches_the_stdout_wire_shape(logcore_env):
    entry = logcore_logger.build_entry("ERROR", "boom")

    assert entry["service_id"] == SERVICE_ID
    assert entry["timestamp"].endswith("Z")
    # env y source_project van anidados: Cloud Run solo promociona las claves
    # logging.googleapis.com/*, y arriba logcore no las lee.
    assert entry["logging.googleapis.com/labels"] == {
        "env": "dev",
        "source_project": "seed-prod-b9508c89",
    }
    assert "env" not in entry and "insert_id" not in entry
    # El esquema rechaza un digest completo de 64 caracteres.
    assert len(entry["logging.googleapis.com/insertId"]) == 32


def test_insert_id_is_deterministic_for_the_same_event(logcore_env):
    error = {"stack": [{"file": "main.py", "line": 42}]}
    first = logcore_logger.build_entry("ERROR", "boom", error=error)
    second = dict(first)

    assert first["logging.googleapis.com/insertId"] == logcore_logger._insert_id(
        first["timestamp"], "todo-api", "boom", error
    )
    assert second["logging.googleapis.com/insertId"] == first[
        "logging.googleapis.com/insertId"
    ]


def test_stack_puts_the_root_cause_first(logcore_env):
    with pytest.raises(RuntimeError) as caught:
        raise_wrapped()

    error = logcore_logger.error_from_exception(caught.value)

    # type y message son los de la excepción lanzada, pero el primer frame es
    # el de la causa raíz: es donde se rompió de verdad y por donde agrupa
    # logcore. Parando en el envoltorio, todo lo que relanza esa capa
    # colapsaría en un único issue.
    assert error["type"] == "RuntimeError"
    assert error["stack"][0]["function"] == "raise_wrapped"
    assert all(isinstance(frame["function"], str) for frame in error["stack"])
    # Nunca el traceback en bruto: una cadena aquí se rechaza y el log se pierde.
    assert isinstance(error["stack"], list)


def test_nothing_is_emitted_without_a_service_id(monkeypatch, capsys):
    monkeypatch.delenv("LOGCORE_SERVICE_ID", raising=False)

    logcore_logger.emit("ERROR", "boom")

    assert capsys.readouterr().out == ""


def test_middleware_reports_an_unhandled_exception_and_still_raises(
    logcore_env, capsys
):
    app = FastAPI()
    app.add_middleware(LogcoreMiddleware)

    @app.get("/boom")
    def boom():
        raise_wrapped()

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")

    assert response.status_code == 500
    entry = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert entry["severity"] == "ERROR"
    assert entry["error"]["type"] == "RuntimeError"
    assert entry["context"] == {"method": "GET", "path": "/boom"}
