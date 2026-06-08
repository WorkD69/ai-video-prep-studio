import os
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

# Set env vars before importing app so pydantic-settings resolves them
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.main import app  # noqa: E402
from app.database import get_db  # noqa: E402


@pytest.fixture(autouse=True)
def _patch_active_job_limit():
    """Default: no active job, acquire_session_lock is a no-op.

    Individual test_job_limit.py tests override these with their own
    inner `with patch(...)` blocks, which take precedence over this fixture.
    """
    with patch("app.api.jobs.find_active_job", return_value=None), \
         patch("app.api.jobs.acquire_session_lock"):
        yield


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
