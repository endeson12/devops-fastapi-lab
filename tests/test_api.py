import json
import logging

from conftest import AppClient

from app.main import db, settings


def test_root_and_health(client: AppClient) -> None:
    assert client.get("/").json()["name"] == "devops-fastapi-lab"
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").json() == {"status": "ready"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_readyz_reports_database_failure(client: AppClient, monkeypatch: object) -> None:
    monkeypatch.setattr(db, "is_ready", lambda: False)  # type: ignore[attr-defined]
    assert client.get("/readyz").status_code == 503


def test_task_crud(client: AppClient) -> None:
    created = client.post(
        "/api/v1/tasks", json={"title": "Publicar API", "description": "validar CI"}
    )
    assert created.status_code == 201
    task = created.json()
    assert task["completed"] is False

    task_id = task["id"]
    assert client.get(f"/api/v1/tasks/{task_id}").json()["title"] == "Publicar API"
    assert len(client.get("/api/v1/tasks").json()) == 1

    updated = client.patch(f"/api/v1/tasks/{task_id}", json={"completed": True})
    assert updated.json()["completed"] is True
    assert updated.json()["title"] == "Publicar API"

    assert client.delete(f"/api/v1/tasks/{task_id}").status_code == 204
    assert client.get(f"/api/v1/tasks/{task_id}").status_code == 404


def test_validation_and_missing_task(client: AppClient) -> None:
    assert client.post("/api/v1/tasks", json={"title": ""}).status_code == 422
    assert client.patch("/api/v1/tasks/999", json={"completed": True}).status_code == 404


def test_metrics(client: AppClient) -> None:
    client.get("/health/live")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text


def test_request_id_security_headers_and_json_log(
    client: AppClient, caplog: object
) -> None:
    request_id = "trace-test-123"
    with caplog.at_level(logging.INFO, logger="app.request"):  # type: ignore[attr-defined]
        response = client.get("/health/live", headers={"X-Request-ID": request_id})

    assert response.headers["X-Request-ID"] == request_id
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    event = json.loads(caplog.records[-1].message)  # type: ignore[attr-defined]
    assert event["request_id"] == request_id
    assert event["path"] == "/health/live"
    assert event["status"] == 200


def test_generated_request_id_is_present_on_errors(client: AppClient) -> None:
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.headers["X-Request-ID"]


def test_mutations_require_configured_api_key_but_reads_remain_public(
    client: AppClient,
) -> None:
    settings.api_key = "test-secret"
    assert client.get("/api/v1/tasks").status_code == 200
    assert client.post("/api/v1/tasks", json={"title": "blocked"}).status_code == 401
    assert (
        client.post(
            "/api/v1/tasks",
            json={"title": "allowed"},
            headers={"X-API-Key": "wrong"},
        ).status_code
        == 401
    )
    response = client.post(
        "/api/v1/tasks",
        json={"title": "allowed"},
        headers={"X-API-Key": "test-secret"},
    )
    assert response.status_code == 201
    task_id = response.json()["id"]
    assert client.patch(f"/api/v1/tasks/{task_id}", json={"completed": True}).status_code == 401
    assert client.delete(f"/api/v1/tasks/{task_id}").status_code == 401


def test_post_idempotency_replays_same_task(client: AppClient) -> None:
    headers = {"Idempotency-Key": "create-release-task"}
    first = client.post("/api/v1/tasks", json={"title": "Release"}, headers=headers)
    replay = client.post("/api/v1/tasks", json={"title": "Release"}, headers=headers)

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert len(client.get("/api/v1/tasks").json()) == 1


def test_post_idempotency_rejects_key_reuse_with_different_payload(
    client: AppClient,
) -> None:
    headers = {"Idempotency-Key": "same-key"}
    assert client.post("/api/v1/tasks", json={"title": "one"}, headers=headers).status_code == 201
    conflict = client.post("/api/v1/tasks", json={"title": "two"}, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "idempotency key already used with another payload"


def test_post_without_idempotency_key_keeps_existing_behavior(client: AppClient) -> None:
    first = client.post("/api/v1/tasks", json={"title": "duplicate"})
    second = client.post("/api/v1/tasks", json={"title": "duplicate"})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
