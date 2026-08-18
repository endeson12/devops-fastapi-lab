import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request

from app.config import get_settings
from app.database import Database
from app.schemas import Task, TaskCreate, TaskUpdate

settings = get_settings()
db = Database(settings.database_path)
REQUESTS = Counter(
    "http_requests_total", "Total de requisições HTTP", ["method", "path", "status"]
)
LATENCY = Histogram(
    "http_request_duration_seconds", "Duração das requisições HTTP", ["method", "path"]
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.initialize()
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)


@app.middleware("http")
async def observe_request(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        REQUESTS.labels(request.method, path, str(status_code)).inc()
        LATENCY.labels(request.method, path).observe(time.perf_counter() - started)


@app.get("/", tags=["system"])
def root() -> dict[str, str]:
    return {"name": settings.app_name, "version": settings.app_version, "docs": "/docs"}


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready() -> dict[str, str]:
    if not db.is_ready():
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _task_or_404(task_id: int) -> Task:
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return Task.model_validate(dict(row))


@app.get("/api/v1/tasks", response_model=list[Task], tags=["tasks"])
def list_tasks() -> list[Task]:
    with db.connect() as connection:
        rows = connection.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    return [Task.model_validate(dict(row)) for row in rows]


@app.post(
    "/api/v1/tasks", response_model=Task, status_code=status.HTTP_201_CREATED, tags=["tasks"]
)
def create_task(payload: TaskCreate) -> Task:
    with db.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO tasks (title, description, completed) VALUES (?, ?, ?)",
            (payload.title, payload.description, payload.completed),
        )
        task_id = cursor.lastrowid
    assert task_id is not None
    return _task_or_404(task_id)


@app.get("/api/v1/tasks/{task_id}", response_model=Task, tags=["tasks"])
def get_task(task_id: int) -> Task:
    return _task_or_404(task_id)


@app.patch("/api/v1/tasks/{task_id}", response_model=Task, tags=["tasks"])
def update_task(task_id: int, payload: TaskUpdate) -> Task:
    current = _task_or_404(task_id)
    values = payload.model_dump(exclude_unset=True)
    merged = {
        "title": values.get("title", current.title),
        "description": values.get("description", current.description),
        "completed": values.get("completed", current.completed),
    }
    with db.connect() as connection:
        connection.execute(
            "UPDATE tasks SET title = ?, description = ?, completed = ? WHERE id = ?",
            (merged["title"], merged["description"], merged["completed"], task_id),
        )
    return _task_or_404(task_id)


@app.delete("/api/v1/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["tasks"])
def delete_task(task_id: int) -> Response:
    _task_or_404(task_id)
    with db.connect() as connection:
        connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
