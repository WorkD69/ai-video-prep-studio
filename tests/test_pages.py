import os
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.main import app  # noqa: E402
from app.database import get_db  # noqa: E402
from app.models.job import Job, JobStatus  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_MP4_CONTENT = b"\x00\x00\x00\x18" + b"ftypisom" + b"\x00" * 100


class _PagesMockDB:
    """Minimal mock DB for page GET tests — only db.get() needed."""

    def __init__(self, jobs=None):
        self._store: dict[str, Job] = {}
        for job in (jobs or []):
            self._store[str(job.id)] = job

    def get(self, model, pk):
        if model is Job:
            return self._store.get(str(pk))
        return None


class _UploadMockDB(_PagesMockDB):
    """Extended mock DB for upload endpoint — needs add/commit/execute/refresh."""

    def __init__(self):
        super().__init__()
        self._pending: dict[str, Job] = {}
        self._execute_rowcounts: list[int] = []
        self.execute_calls: list = []

    def add(self, obj):
        if isinstance(obj, Job):
            self._pending[str(obj.id)] = obj

    def commit(self):
        self._store.update(self._pending)
        self._pending.clear()

    def rollback(self):
        self._pending.clear()

    def refresh(self, obj):
        stored = self._store.get(str(obj.id))
        if stored:
            obj.status = stored.status

    def execute(self, stmt):
        self.execute_calls.append(stmt)
        rc = self._execute_rowcounts.pop(0) if self._execute_rowcounts else 1
        if rc > 0:
            try:
                for col, val_expr in stmt._values.items():
                    col_key = col.key if hasattr(col, "key") else str(col)
                    val = val_expr.value if hasattr(val_expr, "value") else val_expr
                    for job in self._store.values():
                        if hasattr(job, col_key):
                            setattr(job, col_key, val)
            except AttributeError:
                pass

        class _MockResult:
            def __init__(self, rowcount):
                self.rowcount = rowcount

        return _MockResult(rc)


class _FakeSettings:
    def __init__(self, upload_dir: str, max_upload_bytes: int = 524288000):
        self.upload_dir = upload_dir
        self.max_upload_bytes = max_upload_bytes
        self.rq_job_timeout = 600


def _make_job(
    *,
    status: JobStatus = JobStatus.queued,
    error_message: str | None = None,
    original_filename: str = "lecture.mp4",
) -> Job:
    now = datetime.utcnow()
    job_id = uuid4()
    return Job(
        id=job_id,
        session_id=str(uuid4()),
        status=status,
        original_filename=original_filename,
        stored_filename=f"{job_id}.mp4",
        input_path="/uploads/test.mp4",
        video_size_bytes=1024,
        created_at=now,
        expires_at=now + timedelta(hours=24),
        error_message=error_message,
    )


def _make_client(jobs=None) -> TestClient:
    db = _PagesMockDB(jobs)

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _cleanup(client: TestClient) -> None:
    client.close()
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


def test_index_returns_html():
    client = _make_client()
    try:
        response = client.get("/")
    finally:
        _cleanup(client)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<form" in response.text


def test_index_has_htmx_form():
    client = _make_client()
    try:
        response = client.get("/")
    finally:
        _cleanup(client)

    assert 'hx-post="/jobs/upload"' in response.text


# ---------------------------------------------------------------------------
# GET /status/{job_id}
# ---------------------------------------------------------------------------


def test_status_page_valid_job():
    job = _make_job(status=JobStatus.queued)
    client = _make_client([job])
    try:
        response = client.get(f"/status/{job.id}")
    finally:
        _cleanup(client)

    assert response.status_code == 200
    assert str(job.id) in response.text


def test_status_page_unknown_job():
    client = _make_client()
    try:
        response = client.get(f"/status/{uuid4()}")
    finally:
        _cleanup(client)

    assert response.status_code == 404


def test_status_page_invalid_uuid():
    client = _make_client()
    try:
        response = client.get("/status/not-a-uuid")
    finally:
        _cleanup(client)

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /status/{job_id}/fragment — polling behavior
# ---------------------------------------------------------------------------


def test_fragment_pending_has_polling():
    job = _make_job(status=JobStatus.pending)
    client = _make_client([job])
    try:
        response = client.get(f"/status/{job.id}/fragment")
    finally:
        _cleanup(client)

    assert response.status_code == 200
    assert "hx-get" in response.text
    assert "hx-trigger" in response.text


def test_fragment_queued_has_polling():
    job = _make_job(status=JobStatus.queued)
    client = _make_client([job])
    try:
        response = client.get(f"/status/{job.id}/fragment")
    finally:
        _cleanup(client)

    assert response.status_code == 200
    assert "hx-get" in response.text
    assert "hx-trigger" in response.text


def test_fragment_processing_has_polling():
    job = _make_job(status=JobStatus.processing)
    client = _make_client([job])
    try:
        response = client.get(f"/status/{job.id}/fragment")
    finally:
        _cleanup(client)

    assert response.status_code == 200
    assert "hx-get" in response.text


def test_fragment_done_no_polling():
    job = _make_job(status=JobStatus.done)
    client = _make_client([job])
    try:
        response = client.get(f"/status/{job.id}/fragment")
    finally:
        _cleanup(client)

    assert response.status_code == 200
    assert "hx-trigger" not in response.text


def test_fragment_done_has_download_link():
    job = _make_job(status=JobStatus.done)
    client = _make_client([job])
    try:
        response = client.get(f"/status/{job.id}/fragment")
    finally:
        _cleanup(client)

    assert f"/download/{job.id}" in response.text


def test_fragment_failed_no_polling():
    job = _make_job(status=JobStatus.failed, error_message="transcription_failed")
    client = _make_client([job])
    try:
        response = client.get(f"/status/{job.id}/fragment")
    finally:
        _cleanup(client)

    assert response.status_code == 200
    assert "hx-trigger" not in response.text


def test_fragment_failed_shows_error():
    job = _make_job(status=JobStatus.failed, error_message="transcription_failed")
    client = _make_client([job])
    try:
        response = client.get(f"/status/{job.id}/fragment")
    finally:
        _cleanup(client)

    assert "transcription_failed" in response.text


def test_fragment_unknown_job():
    client = _make_client()
    try:
        response = client.get(f"/status/{uuid4()}/fragment")
    finally:
        _cleanup(client)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /jobs/upload — HTMX redirect
# ---------------------------------------------------------------------------


def test_upload_htmx_redirect(tmp_path):
    db = _UploadMockDB()
    upload_dir = str(tmp_path / "uploads")
    fake_settings = _FakeSettings(upload_dir)
    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = MagicMock()

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    try:
        with patch("app.api.jobs.settings", fake_settings):
            with patch("app.api.jobs.get_queue", return_value=mock_queue):
                with TestClient(app) as client:
                    response = client.post(
                        "/jobs/upload",
                        files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
                        headers={"HX-Request": "true"},
                    )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    hx_redirect = response.headers.get("hx-redirect")
    assert hx_redirect is not None
    assert hx_redirect.startswith("/status/")
    UUID(hx_redirect.split("/status/")[1])  # must be a valid UUID


def test_upload_non_htmx_unchanged(tmp_path):
    db = _UploadMockDB()
    upload_dir = str(tmp_path / "uploads")
    fake_settings = _FakeSettings(upload_dir)
    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = MagicMock()

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    try:
        with patch("app.api.jobs.settings", fake_settings):
            with patch("app.api.jobs.get_queue", return_value=mock_queue):
                with TestClient(app) as client:
                    response = client.post(
                        "/jobs/upload",
                        files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
                    )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "queued"
    assert "job_id" in data


# ---------------------------------------------------------------------------
# XSS — Jinja2 auto-escaping
# ---------------------------------------------------------------------------


def test_xss_filename_escaped():
    xss_filename = "<script>alert(1)</script>.mp4"
    job = _make_job(status=JobStatus.done, original_filename=xss_filename)
    client = _make_client([job])
    try:
        response = client.get(f"/status/{job.id}/fragment")
    finally:
        _cleanup(client)

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
