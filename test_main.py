import logging
import pytest
from fastapi.testclient import TestClient
from main import app, init_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_trigger_error_does_not_log_error(caplog):
    with caplog.at_level(logging.ERROR):
        response = client.post("/trigger-error")

    assert response.status_code == 200
    error_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR
    ]
    assert "Test server error: something broke in the TODO API!" not in error_messages
    assert response.json() == {"status": "ok", "message": "Log received server-side"}


def test_todos_crud():
    # Create todo
    create_res = client.post("/todos", json={"title": "Test todo"})
    assert create_res.status_code == 201
    todo = create_res.json()
    assert todo["title"] == "Test todo"
    assert todo["completed"] == 0
    todo_id = todo["id"]

    # List todos
    list_res = client.get("/todos")
    assert list_res.status_code == 200
    todos = list_res.json()
    assert any(t["id"] == todo_id for t in todos)

    # Update todo
    update_res = client.patch(f"/todos/{todo_id}", json={"completed": True})
    assert update_res.status_code == 200
    updated_todo = update_res.json()
    assert updated_todo["completed"] == 1

    # Delete todo
    delete_res = client.delete(f"/todos/{todo_id}")
    assert delete_res.status_code == 204

