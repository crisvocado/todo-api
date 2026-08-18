from fastapi.testclient import TestClient
from main import app, _slug


def test_create_todo_with_non_ascii_title():
    with TestClient(app) as client:
        response = client.post("/todos", json={"title": "Comprar café"})
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Comprar café"
        assert data["slug"] == "comprar-cafe"


def test_slug_non_ascii_handling():
    assert _slug("Comprar café") == "comprar-cafe"
