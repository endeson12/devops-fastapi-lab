import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
request_logger = logging.getLogger("app.request")
request_logger.setLevel(logging.INFO)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(provided_key: str | None = Depends(api_key_header)) -> None:
    if settings.api_key is None:
        return
    if provided_key is None or not hmac.compare_digest(provided_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.initialize()
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)


class RequestContextMiddleware:
    def __init__(self, wrapped_app: ASGIApp) -> None:
        self.app = wrapped_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        raw_headers = dict(scope.get("headers", []))
        supplied_request_id = raw_headers.get(b"x-request-id", b"").decode(
            "ascii", errors="ignore"
        )
        request_id = (
            supplied_request_id
            if supplied_request_id.isprintable() and 0 < len(supplied_request_id) <= 128
            else str(uuid.uuid4())
        )
        scope.setdefault("state", {})["request_id"] = request_id
        status_code = 500

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            route = scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            duration = time.perf_counter() - started
            method = scope.get("method", "UNKNOWN")
            REQUESTS.labels(method, route_path, str(status_code)).inc()
            LATENCY.labels(method, route_path).observe(duration)
            request_logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": method,
                        "path": scope.get("path", ""),
                        "route": route_path,
                        "status": status_code,
                        "duration_ms": round(duration * 1000, 3),
                    },
                    separators=(",", ":"),
                )
            )


app.add_middleware(RequestContextMiddleware)


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"name": settings.app_name, "version": settings.app_version, "docs": "/docs"}


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", tags=["health"])
async def ready() -> dict[str, str]:
    if not db.is_ready():
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready"}


app.add_api_route("/health/ready", ready, methods=["GET"], tags=["health"])


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _task_or_404(task_id: int) -> Task:
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return Task.model_validate(dict(row))


@app.get("/api/v1/tasks", response_model=list[Task], tags=["tasks"])
async def list_tasks() -> list[Task]:
    with db.connect() as connection:
        rows = connection.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    return [Task.model_validate(dict(row)) for row in rows]


@app.post(
    "/api/v1/tasks", response_model=Task, status_code=status.HTTP_201_CREATED, tags=["tasks"]
)
async def create_task(
    payload: TaskCreate,
    response: Response,
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
    _: None = Depends(require_api_key),
) -> Task:
    request_json = json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    request_hash = hashlib.sha256(request_json.encode()).hexdigest()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if idempotency_key is not None:
            existing = connection.execute(
                "SELECT request_hash, response_json FROM idempotency_keys WHERE key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if not hmac.compare_digest(existing["request_hash"], request_hash):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="idempotency key already used with another payload",
                    )
                response.headers["Idempotency-Replayed"] = "true"
                return Task.model_validate_json(existing["response_json"])
        cursor = connection.execute(
            "INSERT INTO tasks (title, description, completed) VALUES (?, ?, ?)",
            (payload.title, payload.description, payload.completed),
        )
        task_id = cursor.lastrowid
        assert task_id is not None
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert row is not None
        task = Task.model_validate(dict(row))
        if idempotency_key is not None:
            connection.execute(
                """INSERT INTO idempotency_keys (key, request_hash, response_json)
                VALUES (?, ?, ?)""",
                (idempotency_key, request_hash, task.model_dump_json()),
            )
    return task


@app.get("/api/v1/tasks/{task_id}", response_model=Task, tags=["tasks"])
async def get_task(task_id: int) -> Task:
    return _task_or_404(task_id)


@app.patch(
    "/api/v1/tasks/{task_id}",
    response_model=Task,
    tags=["tasks"],
    dependencies=[Depends(require_api_key)],
)
async def update_task(task_id: int, payload: TaskUpdate) -> Task:
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


@app.delete(
    "/api/v1/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["tasks"],
    dependencies=[Depends(require_api_key)],
)
async def delete_task(task_id: int) -> Response:
    _task_or_404(task_id)
    with db.connect() as connection:
        connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
