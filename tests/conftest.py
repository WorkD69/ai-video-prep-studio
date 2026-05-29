import os
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

# Set env vars before importing app so pydantic-settings resolves them
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.main import app  # noqa: E402
from app.database import get_db  # noqa: E402


@pytest.fixture
def healthy_db() -> MagicMock:
    db = MagicMock()
    db.execute.return_value = MagicMock()
    return db


@pytest.fixture
def broken_db() -> MagicMock:
    db = MagicMock()
    db.execute.side_effect = Exception("DB connection refused")
    return db


@pytest.fixture
def client(healthy_db: MagicMock) -> TestClient:
    def override():
        yield healthy_db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def client_broken_db(broken_db: MagicMock) -> TestClient:
    def override():
        yield broken_db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
