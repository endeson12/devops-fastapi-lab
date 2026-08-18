from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app, db


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db.path = tmp_path / "test.db"
    with TestClient(app) as test_client:
        yield test_client
