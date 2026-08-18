from fastapi.testclient import TestClient


def test_root_and_health(client: TestClient) -> None:
    assert client.get("/").json()["name"] == "devops-fastapi-lab"
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_task_crud(client: TestClient) -> None:
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


def test_validation_and_missing_task(client: TestClient) -> None:
    assert client.post("/api/v1/tasks", json={"title": ""}).status_code == 422
    assert client.patch("/api/v1/tasks/999", json={"completed": True}).status_code == 404


def test_metrics(client: TestClient) -> None:
    client.get("/health/live")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
