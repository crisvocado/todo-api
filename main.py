import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from logcore_middleware import LogcoreMiddleware


DB_PATH = "todos.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            completed BOOLEAN NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="TODO API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last, so it is the outermost user middleware and sees every exception
# that escapes a route handler.
app.add_middleware(LogcoreMiddleware)


class TodoCreate(BaseModel):
    title: str


class TodoUpdate(BaseModel):
    title: str | None = None
    completed: bool | None = None


@app.get("/todos")
def list_todos():
    conn = get_db()
    rows = conn.execute("SELECT * FROM todos ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _slug(title: str) -> str:
    """Search key for the todo: lowercase, ascii, spaces as hyphens."""
    return title.strip().lower().replace(" ", "-").encode("ascii", "ignore").decode("ascii")


@app.post("/todos", status_code=201)
def create_todo(data: TodoCreate):
    conn = get_db()
    cursor = conn.execute("INSERT INTO todos (title) VALUES (?)", (data.title,))
    conn.commit()
    todo = conn.execute("SELECT * FROM todos WHERE id = ?", (cursor.lastrowid,)).fetchone()
    conn.close()
    result = dict(todo)
    result["slug"] = _slug(result["title"])
    return result


@app.patch("/todos/{todo_id}")
def update_todo(todo_id: int, data: TodoUpdate):
    conn = get_db()
    todo = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    if not todo:
        conn.close()
        raise HTTPException(status_code=404, detail="Todo not found")
    updates = []
    values = []
    if data.title is not None:
        updates.append("title = ?")
        values.append(data.title)
    if data.completed is not None:
        updates.append("completed = ?")
        values.append(data.completed)
    if updates:
        values.append(todo_id)
        conn.execute(f"UPDATE todos SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()
    todo = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    conn.close()
    return dict(todo)


@app.post("/trigger-error")
def trigger_error():
    import logging
    logger = logging.getLogger("todo-api")
    logger.info("Log recibido - trigger-error endpoint called")
    return {"status": "ok", "message": "Log received server-side"}


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int):
    conn = get_db()
    todo = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    if not todo:
        conn.close()
        raise HTTPException(status_code=404, detail="Todo not found")
    conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    conn.commit()
    conn.close()
