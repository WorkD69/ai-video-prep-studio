import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.main import app  # noqa: E402
from app.database import get_db  # noqa: E402
from app.models.job import Job, JobStatus  # noqa: E402

# --- Test video content ---
# bytes[4:8] == b"ftyp" - valid MP4/MOV magic
VALID_MP4_CONTENT = b"\x00\x00\x00\x18" + b"ftypisom" + b"\x00" * 100
# bytes[0:4] == \x1a\x45\xdf\xa3 - valid WebM/MKV magic
VALID_WEBM_CONTENT = b"\x1a\x45\xdf\xa3" + b"\x00" * 100
# No video magic - triggers 400 on magic-byte check
INVALID_CONTENT = b"This is not a video file content."
# Large MP4: valid magic but > 1024 bytes for small-limit tests
LARGE_MP4_CONTENT = b"\x00\x00\x00\x18" + b"ftypisom" + b"\x00" * 2000


class MockDB:
    """Two-phase MockDB: add() - pending, commit() - store. Supports rollback tracking."""

    def __init__(self):
        self._pending: dict[str, Job] = {}
        self._store: dict[str, Job] = {}
        self.rollback_called = False

    def add(self, obj):
        if isinstance(obj, Job):
            self._pending[str(obj.id)] = obj

    def commit(self):
        self._store.update(self._pending)
        self._pending.clear()

    def refresh(self, obj):
        pass

    def rollback(self):
        self._pending.clear()
        self.rollback_called = True

    def get(self, model, pk):
        return self._store.get(str(pk)) if model is Job else None


class FakeSettings:
    def __init__(self, upload_dir: str, max_upload_bytes: int):
        self.upload_dir = upload_dir
        self.max_upload_bytes = max_upload_bytes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    return MockDB()


@pytest.fixture
def upload_setup(tmp_path, mock_db):
    def override():
        yield mock_db

    app.dependency_overrides[get_db] = override
    yield mock_db, tmp_path
    app.dependency_overrides.clear()


@pytest.fixture
def upload_client(upload_setup):
    mock_db, tmp_path = upload_setup
    upload_dir = str(tmp_path / "uploads")
    fake_settings = FakeSettings(upload_dir, 524288000)
    with patch("app.api.jobs.settings", fake_settings):
        with TestClient(app) as client:
            yield client, mock_db, Path(upload_dir)


@pytest.fixture
def small_limit_client(upload_setup):
    mock_db, tmp_path = upload_setup
    upload_dir = str(tmp_path / "uploads")
    fake_settings = FakeSettings(upload_dir, 1024)
    with patch("app.api.jobs.settings", fake_settings):
        with TestClient(app) as client:
            yield client, mock_db, Path(upload_dir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_upload_happy_path(upload_client):
    client, mock_db, upload_dir = upload_client
    response = client.post(
        "/jobs/upload",
        files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "pending"
    assert data["original_filename"] == "lecture.mp4"
    assert data["video_size_bytes"] == len(VALID_MP4_CONTENT)
    assert "job_id" in data
    assert "session_id" in data
    # session_id must be a valid UUID
    UUID(data["session_id"])
    # DB row created in store (not just pending)
    assert len(mock_db._store) == 1
    job = list(mock_db._store.values())[0]
    assert job.status == JobStatus.pending
    # File saved on disk
    assert upload_dir.exists()
    files = list(upload_dir.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith(".mp4")


def test_upload_invalid_type(upload_client):
    client, mock_db, upload_dir = upload_client
    response = client.post(
        "/jobs/upload",
        files={"file": ("test.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 415
    assert len(mock_db._store) == 0


def test_upload_magic_mismatch(upload_client):
    client, mock_db, upload_dir = upload_client
    response = client.post(
        "/jobs/upload",
        files={"file": ("video.mp4", INVALID_CONTENT, "video/mp4")},
    )
    assert response.status_code == 400
    assert len(mock_db._store) == 0
    # No file written (magic check happens before stream_save)
    files = list(upload_dir.iterdir()) if upload_dir.exists() else []
    assert len(files) == 0


def test_upload_too_large(small_limit_client):
    client, mock_db, upload_dir = small_limit_client
    response = client.post(
        "/jobs/upload",
        files={"file": ("big.mp4", LARGE_MP4_CONTENT, "video/mp4")},
    )
    assert response.status_code == 413
    assert len(mock_db._store) == 0
    # Partial file must be cleaned up
    files = list(upload_dir.iterdir()) if upload_dir.exists() else []
    assert len(files) == 0


def test_upload_path_traversal_filename(upload_client):
    client, mock_db, upload_dir = upload_client
    response = client.post(
        "/jobs/upload",
        files={"file": ("../../evil.mp4", VALID_MP4_CONTENT, "video/mp4")},
    )
    assert response.status_code == 201
    data = response.json()
    # original_filename must be sanitized to just the base name
    assert data["original_filename"] == "evil.mp4"
    # Stored file must be inside upload_dir, no path traversal
    job = list(mock_db._store.values())[0]
    assert ".." not in job.stored_filename
    assert job.stored_filename.endswith(".mp4")
    assert job.input_path.startswith(str(upload_dir))
    # File is physically inside upload_dir
    files = list(upload_dir.iterdir())
    assert len(files) == 1
    assert files[0].parent == upload_dir


def test_upload_missing_file(upload_client):
    client, mock_db, upload_dir = upload_client
    response = client.post("/jobs/upload")
    assert response.status_code == 422
    assert len(mock_db._store) == 0


def test_job_lookup_found(upload_client):
    client, mock_db, upload_dir = upload_client
    job_id = uuid4()
    now = datetime.utcnow()
    job = Job(
        id=job_id,
        session_id=str(uuid4()),
        status=JobStatus.pending,
        original_filename="lecture.mp4",
        stored_filename=f"{uuid4()}.mp4",
        input_path="/uploads/test.mp4",
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    mock_db._store[str(job_id)] = job

    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == str(job_id)
    assert data["status"] == "pending"
    assert data["original_filename"] == "lecture.mp4"
    assert data["error_message"] is None


def test_job_lookup_not_found(upload_client):
    client, mock_db, upload_dir = upload_client
    fake_uuid = uuid4()
    response = client.get(f"/jobs/{fake_uuid}")
    assert response.status_code == 404


def test_job_lookup_invalid_uuid(upload_client):
    client, mock_db, upload_dir = upload_client
    response = client.get("/jobs/not-a-uuid")
    assert response.status_code == 422


def test_upload_db_failure_deletes_saved_file(upload_setup):
    mock_db, tmp_path = upload_setup
    upload_dir = str(tmp_path / "uploads")
    fake_settings = FakeSettings(upload_dir, 524288000)

    def failing_commit():
        raise Exception("DB connection refused")

    mock_db.commit = failing_commit

    with patch("app.api.jobs.settings", fake_settings):
        with TestClient(app) as client:
            response = client.post(
                "/jobs/upload",
                files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
            )

    assert response.status_code == 500
    assert mock_db.rollback_called
    # No orphan DB row
    assert len(mock_db._store) == 0
    # No orphan file on disk
    upload_path = Path(upload_dir)
    files_on_disk = list(upload_path.iterdir()) if upload_path.exists() else []
    assert len(files_on_disk) == 0


def test_upload_too_large_in_initial_header(upload_setup):
    # max_upload_bytes=4 is less than the 8-byte header we always read,
    # so stream_save must raise 413 before creating any file.
    mock_db, tmp_path = upload_setup
    upload_dir = str(tmp_path / "uploads")
    fake_settings = FakeSettings(upload_dir, 4)

    with patch("app.api.jobs.settings", fake_settings):
        with TestClient(app) as client:
            response = client.post(
                "/jobs/upload",
                files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
            )

    assert response.status_code == 413
    assert len(mock_db._store) == 0
    upload_path = Path(upload_dir)
    files_on_disk = list(upload_path.iterdir()) if upload_path.exists() else []
    assert len(files_on_disk) == 0
