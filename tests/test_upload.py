import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.main import app  # noqa: E402
from app.database import get_db  # noqa: E402
from app.models.job import Job, JobStatus  # noqa: E402


def _extract_stmt_values(stmt) -> dict:
    """Extract {attr_name: python_value} from a SQLAlchemy Update statement.

    Keys in _values are Column objects (with .key); values are BindParameters
    (with .value). Gracefully returns {} if the statement does not match.
    """
    result = {}
    try:
        for col, val_expr in stmt._values.items():
            col_key = col.key if hasattr(col, "key") else str(col)
            result[col_key] = val_expr.value if hasattr(val_expr, "value") else val_expr
    except AttributeError:
        pass
    return result

# --- Test video content ---
# bytes[4:8] == b"ftyp" - valid MP4/MOV magic
VALID_MP4_CONTENT = b"\x00\x00\x00\x18" + b"ftypisom" + b"\x00" * 100
# bytes[0:4] == \x1a\x45\xdf\xa3 - valid WebM/MKV magic
VALID_WEBM_CONTENT = b"\x1a\x45\xdf\xa3" + b"\x00" * 100
# No video magic - triggers 400 on magic-byte check
INVALID_CONTENT = b"This is not a video file content."
# Large MP4: valid magic but > 1024 bytes for small-limit tests
LARGE_MP4_CONTENT = b"\x00\x00\x00\x18" + b"ftypisom" + b"\x00" * 2000


class MockResult:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount


class MockDB:
    """Two-phase MockDB: add() - pending, commit() - store. Supports rollback tracking."""

    def __init__(self):
        self._pending: dict[str, Job] = {}
        self._store: dict[str, Job] = {}
        self.rollback_called = False
        self._execute_rowcounts: list[int] = []
        self.execute_calls: list = []

    def set_execute_rowcounts(self, *counts: int) -> None:
        self._execute_rowcounts = list(counts)

    def add(self, obj):
        if isinstance(obj, Job):
            self._pending[str(obj.id)] = obj

    def commit(self):
        self._store.update(self._pending)
        self._pending.clear()

    def refresh(self, obj):
        # Re-read from store so race-condition tests work
        stored = self._store.get(str(obj.id))
        if stored:
            obj.status = stored.status
            obj.error_message = stored.error_message
            obj.completed_at = stored.completed_at

    def rollback(self):
        self._pending.clear()
        self.rollback_called = True

    def get(self, model, pk):
        return self._store.get(str(pk)) if model is Job else None

    def execute(self, stmt):
        self.execute_calls.append(stmt)
        rc = self._execute_rowcounts.pop(0) if self._execute_rowcounts else 1

        if rc > 0:
            values = _extract_stmt_values(stmt)
            for job in self._store.values():
                for attr, val in values.items():
                    if hasattr(job, attr):
                        setattr(job, attr, val)

        return MockResult(rc)


class FakeSettings:
    def __init__(self, upload_dir: str, max_upload_bytes: int):
        self.upload_dir = upload_dir
        self.max_upload_bytes = max_upload_bytes
        self.rq_job_timeout = 600


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


def _make_mock_queue():
    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = MagicMock()
    return mock_queue


@pytest.fixture
def upload_client(upload_setup):
    mock_db, tmp_path = upload_setup
    upload_dir = str(tmp_path / "uploads")
    fake_settings = FakeSettings(upload_dir, 524288000)
    mock_queue = _make_mock_queue()
    with patch("app.api.jobs.settings", fake_settings):
        with patch("app.api.jobs.get_queue", return_value=mock_queue):
            with TestClient(app) as client:
                yield client, mock_db, Path(upload_dir), mock_queue


@pytest.fixture
def small_limit_client(upload_setup):
    mock_db, tmp_path = upload_setup
    upload_dir = str(tmp_path / "uploads")
    fake_settings = FakeSettings(upload_dir, 1024)
    mock_queue = _make_mock_queue()
    with patch("app.api.jobs.settings", fake_settings):
        with patch("app.api.jobs.get_queue", return_value=mock_queue):
            with TestClient(app) as client:
                yield client, mock_db, Path(upload_dir), mock_queue


# ---------------------------------------------------------------------------
# Existing tests (updated for M003: happy path returns queued, not pending)
# ---------------------------------------------------------------------------

def test_upload_happy_path(upload_client):
    client, mock_db, upload_dir, mock_queue = upload_client
    response = client.post(
        "/jobs/upload",
        files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "queued"
    assert data["original_filename"] == "lecture.mp4"
    assert data["video_size_bytes"] == len(VALID_MP4_CONTENT)
    assert "job_id" in data
    assert "session_id" in data
    UUID(data["session_id"])
    assert len(mock_db._store) == 1
    # File saved on disk
    assert upload_dir.exists()
    files = list(upload_dir.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith(".mp4")


def test_upload_invalid_type(upload_client):
    client, mock_db, upload_dir, _ = upload_client
    response = client.post(
        "/jobs/upload",
        files={"file": ("test.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 415
    assert len(mock_db._store) == 0


def test_upload_magic_mismatch(upload_client):
    client, mock_db, upload_dir, _ = upload_client
    response = client.post(
        "/jobs/upload",
        files={"file": ("video.mp4", INVALID_CONTENT, "video/mp4")},
    )
    assert response.status_code == 400
    assert len(mock_db._store) == 0
    files = list(upload_dir.iterdir()) if upload_dir.exists() else []
    assert len(files) == 0


def test_upload_too_large(small_limit_client):
    client, mock_db, upload_dir, _ = small_limit_client
    response = client.post(
        "/jobs/upload",
        files={"file": ("big.mp4", LARGE_MP4_CONTENT, "video/mp4")},
    )
    assert response.status_code == 413
    assert len(mock_db._store) == 0
    files = list(upload_dir.iterdir()) if upload_dir.exists() else []
    assert len(files) == 0


def test_upload_path_traversal_filename(upload_client):
    client, mock_db, upload_dir, _ = upload_client
    response = client.post(
        "/jobs/upload",
        files={"file": ("../../evil.mp4", VALID_MP4_CONTENT, "video/mp4")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["original_filename"] == "evil.mp4"
    job = list(mock_db._store.values())[0]
    assert ".." not in job.stored_filename
    assert job.stored_filename.endswith(".mp4")
    assert job.input_path.startswith(str(upload_dir))
    files = list(upload_dir.iterdir())
    assert len(files) == 1
    assert files[0].parent == upload_dir


def test_upload_missing_file(upload_client):
    client, mock_db, upload_dir, _ = upload_client
    response = client.post("/jobs/upload")
    assert response.status_code == 422
    assert len(mock_db._store) == 0


def test_job_lookup_found(upload_client):
    client, mock_db, upload_dir, _ = upload_client
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
    client, mock_db, upload_dir, _ = upload_client
    fake_uuid = uuid4()
    response = client.get(f"/jobs/{fake_uuid}")
    assert response.status_code == 404


def test_job_lookup_invalid_uuid(upload_client):
    client, mock_db, upload_dir, _ = upload_client
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
        with patch("app.api.jobs.get_queue", return_value=_make_mock_queue()):
            with TestClient(app) as client:
                response = client.post(
                    "/jobs/upload",
                    files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
                )

    assert response.status_code == 500
    assert mock_db.rollback_called
    assert len(mock_db._store) == 0
    upload_path = Path(upload_dir)
    files_on_disk = list(upload_path.iterdir()) if upload_path.exists() else []
    assert len(files_on_disk) == 0


def test_upload_too_large_in_initial_header(upload_setup):
    mock_db, tmp_path = upload_setup
    upload_dir = str(tmp_path / "uploads")
    fake_settings = FakeSettings(upload_dir, 4)

    with patch("app.api.jobs.settings", fake_settings):
        with patch("app.api.jobs.get_queue", return_value=_make_mock_queue()):
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


# ---------------------------------------------------------------------------
# M003: enqueue behaviour tests
# ---------------------------------------------------------------------------

def test_upload_enqueues_and_flips_to_queued(upload_setup, tmp_path):
    """Happy path: enqueue called once, guarded flip rowcount=1, response status=queued."""
    mock_db, _ = upload_setup
    upload_dir = str(tmp_path / "uploads2")
    fake_settings = FakeSettings(upload_dir, 524288000)
    mock_queue = _make_mock_queue()

    with patch("app.api.jobs.settings", fake_settings):
        with patch("app.api.jobs.get_queue", return_value=mock_queue):
            with TestClient(app) as client:
                response = client.post(
                    "/jobs/upload",
                    files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
                )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "queued"
    mock_queue.enqueue.assert_called_once()
    # Verify job_id was passed as argument (not the ORM object)
    call_args = mock_queue.enqueue.call_args
    enqueued_job_id = call_args[0][1]  # positional arg after the function
    assert enqueued_job_id == data["job_id"]


def test_upload_enqueue_failure_marks_failed_503(upload_setup, tmp_path):
    """enqueue raises -> job status=failed, error_message=enqueue_failed, HTTP 503."""
    mock_db, _ = upload_setup
    upload_dir = str(tmp_path / "uploads3")
    fake_settings = FakeSettings(upload_dir, 524288000)

    def raising_enqueue(*args, **kwargs):
        raise ConnectionError("Redis down")

    mock_queue = MagicMock()
    mock_queue.enqueue.side_effect = raising_enqueue

    with patch("app.api.jobs.settings", fake_settings):
        with patch("app.api.jobs.get_queue", return_value=mock_queue):
            with TestClient(app) as client:
                response = client.post(
                    "/jobs/upload",
                    files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
                )

    assert response.status_code == 503
    assert response.json()["detail"] == "job_enqueue_failed"
    # execute() was called exactly once for the compensation write (pending -> failed)
    assert len(mock_db.execute_calls) == 1
    # compensation write must have applied correct state to the job in store
    assert len(mock_db._store) == 1
    saved_job = list(mock_db._store.values())[0]
    assert saved_job.status == JobStatus.failed
    assert saved_job.error_message == "enqueue_failed"
    assert saved_job.completed_at is not None


def test_upload_flip_noop_returns_actual_status(upload_setup, tmp_path):
    """Worker advanced job to processing before flip -> flip returns 0 rows ->
    upload re-reads job and returns actual status (processing), not queued."""
    mock_db, _ = upload_setup
    upload_dir = str(tmp_path / "uploads4")
    fake_settings = FakeSettings(upload_dir, 524288000)
    mock_queue = _make_mock_queue()

    # Guarded flip will return rowcount=0 (worker already advanced)
    mock_db.set_execute_rowcounts(0)

    # Pre-set job status to processing in store (simulates worker having advanced it)
    # We do this by patching refresh to update the job's status
    original_commit = mock_db.commit

    def commit_and_advance():
        original_commit()
        # After first commit (the initial pending save), update job status in store
        for job in mock_db._store.values():
            if job.status == JobStatus.pending:
                job.status = JobStatus.processing

    mock_db.commit = commit_and_advance

    with patch("app.api.jobs.settings", fake_settings):
        with patch("app.api.jobs.get_queue", return_value=mock_queue):
            with TestClient(app) as client:
                response = client.post(
                    "/jobs/upload",
                    files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
                )

    assert response.status_code == 201
    data = response.json()
    # Status must reflect actual DB state (processing), not forced queued
    assert data["status"] == "processing"


def test_upload_enqueue_failure_compensation_db_failure_still_503(upload_setup, tmp_path):
    """enqueue raises AND compensation DB write fails -> still returns 503, no raw error leaked."""
    mock_db, _ = upload_setup
    upload_dir = str(tmp_path / "uploads5")
    fake_settings = FakeSettings(upload_dir, 524288000)

    mock_queue = MagicMock()
    mock_queue.enqueue.side_effect = ConnectionError("Redis down")

    def failing_execute(stmt):
        raise Exception("DB compensation failure")

    mock_db.execute = failing_execute

    with patch("app.api.jobs.settings", fake_settings):
        with patch("app.api.jobs.get_queue", return_value=mock_queue):
            with TestClient(app) as client:
                response = client.post(
                    "/jobs/upload",
                    files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
                )

    assert response.status_code == 503
    assert response.json()["detail"] == "job_enqueue_failed"
    assert mock_db.rollback_called
