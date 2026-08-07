import logging
from fastapi.testclient import TestClient
from main import app, init_db

init_db()


def test_trigger_error_does_not_log_error(caplog):
    with TestClient(app) as client:
        with caplog.at_level(logging.ERROR, logger="todo-api"):
            response = client.post("/trigger-error")
            assert response.status_code == 200
            assert response.json() == {"status": "ok", "message": "Log received server-side"}
            error_logs = [
                record for record in caplog.records
                if record.levelname == "ERROR" and record.name == "todo-api"
            ]
            assert len(error_logs) == 0, f"Expected no error logs, but found: {error_logs}"


def test_todos_crud():
    with TestClient(app) as client:
        # Test list empty/initial todos
        res = client.get("/todos")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

        # Test create todo
        res = client.post("/todos", json={"title": "Test todo"})
        assert res.status_code == 201
        todo = res.json()
        assert todo["title"] == "Test todo"
        assert todo["completed"] == False
        todo_id = todo["id"]

        # Test update todo
        res = client.patch(f"/todos/{todo_id}", json={"completed": True})
        assert res.status_code == 200
        assert res.json()["completed"] == True

        # Test delete todo
        res = client.delete(f"/todos/{todo_id}")
        assert res.status_code == 204

        # Test update non-existent todo
        res = client.patch("/todos/99999", json={"title": "Non-existent"})
        assert res.status_code == 404

        # Test delete non-existent todo
        res = client.delete("/todos/99999")
        assert res.status_code == 404
