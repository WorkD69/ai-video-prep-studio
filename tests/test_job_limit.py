"""Tests for the 1-active-job-per-session limit (M008).

Seams: `app.api.jobs.find_active_job` and `app.api.jobs.acquire_session_lock`
are patched in every test. Real PostgreSQL is NOT required; the Docker gate
covers the advisory-lock serialisation.

All tests are deterministic and fast — no real video, no network.
"""
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.main import app  # noqa: E402
from app.database import get_db  # noqa: E402
from app.models.job import Job, JobStatus  # noqa: E402


# ---------------------------------------------------------------------------
# Test video fixtures
# ---------------------------------------------------------------------------
VALID_MP4_CONTENT = b"\x00\x00\x00\x18" + b"ftypisom" + b"\x00" * 100


# ---------------------------------------------------------------------------
# MockDB — minimal mock for upload endpoint tests
# ---------------------------------------------------------------------------

class MockResult:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount


class LimitMockDB:
    """Minimal SQLAlchemy session mock for job-limit tests."""

    def __init__(self):
        self._pending: dict = {}
        self._store: dict = {}
        self.rollback_called = False
        self.execute_calls: list = []
        self._execute_rowcounts: list[int] = []

    def set_execute_rowcounts(self, *counts: int):
        self._execute_rowcounts = list(counts)

    def add(self, obj):
        if isinstance(obj, Job):
            self._pending[str(obj.id)] = obj

    def commit(self):
        self._store.update(self._pending)
        self._pending.clear()

    def refresh(self, obj):
        stored = self._store.get(str(obj.id))
        if stored:
            obj.status = stored.status

    def rollback(self):
        self._pending.clear()
        self.rollback_called = True

    def get(self, model, pk):
        return self._store.get(str(pk)) if model is Job else None

    def execute(self, stmt):
        self.execute_calls.append(stmt)
        rc = self._execute_rowcounts.pop(0) if self._execute_rowcounts else 1
        return MockResult(rc)


class FakeSettings:
    def __init__(self, upload_dir: str, max_upload_bytes: int = 524288000):
        self.upload_dir = upload_dir
        self.max_upload_bytes = max_upload_bytes
        self.rq_job_timeout = 600
        self.session_cookie_name = "aivps_session"
        self.session_cookie_max_age = 2592000
        self.session_cookie_secure = False


def _make_mock_queue():
    q = MagicMock()
    q.enqueue.return_value = MagicMock()
    return q


def _fake_active_job(session_id: str | None = None) -> Job:
    job_id = uuid4()
    now = datetime.utcnow()
    return Job(
        id=job_id,
        session_id=session_id or str(uuid4()),
        status=JobStatus.processing,
        original_filename="running.mp4",
        stored_filename=f"{uuid4()}.mp4",
        input_path="/uploads/running.mp4",
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    return LimitMockDB()


@pytest.fixture
def limit_setup(tmp_path, mock_db):
    def override():
        yield mock_db
    app.dependency_overrides[get_db] = override
    yield mock_db, tmp_path
    app.dependency_overrides.clear()


@pytest.fixture
def limit_client(limit_setup):
    mock_db, tmp_path = limit_setup
    upload_dir = str(tmp_path / "uploads")
    fake_settings = FakeSettings(upload_dir)
    mock_queue = _make_mock_queue()
    with patch("app.api.jobs.settings", fake_settings):
        with patch("app.api.jobs.get_queue", return_value=mock_queue):
            with TestClient(app) as client:
                yield client, mock_db, Path(upload_dir), mock_queue, tmp_path


# ---------------------------------------------------------------------------
# Limit enforcement tests
# ---------------------------------------------------------------------------

def test_upload_blocked_when_active_job_json(limit_client):
    """Non-HX upload with an active job → 429 + detail=active_job_exists, no db.add, no file."""
    client, mock_db, upload_dir, _, _ = limit_client
    active = _fake_active_job()

    with patch("app.api.jobs.find_active_job", return_value=active), \
         patch("app.api.jobs.acquire_session_lock"):
        response = client.post(
            "/jobs/upload",
            files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "active_job_exists"
    # No job added to DB
    assert len(mock_db._store) == 0
    # No file saved to disk (early pre-check fired before stream_save)
    files = list(upload_dir.iterdir()) if upload_dir.exists() else []
    assert len(files) == 0


def test_upload_blocked_when_active_job_htmx(limit_client):
    """HX upload with active job → 429 HTML fragment, status link, no HX-Redirect."""
    client, mock_db, upload_dir, _, _ = limit_client
    active = _fake_active_job()

    with patch("app.api.jobs.find_active_job", return_value=active), \
         patch("app.api.jobs.acquire_session_lock"):
        response = client.post(
            "/jobs/upload",
            files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
            headers={"HX-Request": "true"},
        )

    assert response.status_code == 429
    # HTML fragment, not JSON
    assert "text/html" in response.headers.get("content-type", "")
    # Fragment contains a link to the active job's status page
    assert f"/status/{active.id}" in response.text
    # No HX-Redirect — page stays in place
    assert "HX-Redirect" not in response.headers
    # No job in DB
    assert len(mock_db._store) == 0


def test_upload_allowed_when_no_active_job(limit_client):
    """No active job → upload proceeds, 201 returned."""
    client, mock_db, _, mock_queue, _ = limit_client

    with patch("app.api.jobs.find_active_job", return_value=None), \
         patch("app.api.jobs.acquire_session_lock"):
        response = client.post(
            "/jobs/upload",
            files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
        )

    assert response.status_code == 201
    assert len(mock_db._store) == 1
    mock_queue.enqueue.assert_called_once()


def test_upload_allowed_when_previous_job_terminal(limit_client):
    """find_active_job returns None (terminal jobs not matched) → 201."""
    client, mock_db, _, _, _ = limit_client

    # find_active_job returns None because done/failed don't match the query
    with patch("app.api.jobs.find_active_job", return_value=None), \
         patch("app.api.jobs.acquire_session_lock"):
        response = client.post(
            "/jobs/upload",
            files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
        )

    assert response.status_code == 201
    assert len(mock_db._store) == 1


def test_lock_acquired_before_authoritative_check(limit_client):
    """acquire_session_lock is called before the second (authoritative) find_active_job."""
    client, _, _, _, _ = limit_client

    call_order = []

    def tracking_find(db, sid):
        call_order.append("find")
        return None  # both checks pass → upload succeeds

    def tracking_lock(db, sid):
        call_order.append("lock")

    with patch("app.api.jobs.find_active_job", side_effect=tracking_find), \
         patch("app.api.jobs.acquire_session_lock", side_effect=tracking_lock):
        response = client.post(
            "/jobs/upload",
            files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
        )

    assert response.status_code == 201
    # Expected: find (early), lock, find (authoritative)
    assert len(call_order) == 3
    assert call_order == ["find", "lock", "find"]


def test_race_reject_deletes_saved_file(limit_setup):
    """Early check passes (None), file saved, locked re-check finds active job →
    saved file deleted, db.rollback called, 429 returned."""
    mock_db, tmp_path = limit_setup
    upload_dir = str(tmp_path / "uploads_race")
    fake_settings = FakeSettings(upload_dir)
    mock_queue = _make_mock_queue()
    active = _fake_active_job()

    # first call (early): None, second call (authoritative): active job
    side_effects = [None, active]
    find_call_count = 0

    def find_side_effect(db, sid):
        nonlocal find_call_count
        result = side_effects[find_call_count]
        find_call_count += 1
        return result

    with patch("app.api.jobs.settings", fake_settings), \
         patch("app.api.jobs.get_queue", return_value=mock_queue), \
         patch("app.api.jobs.find_active_job", side_effect=find_side_effect), \
         patch("app.api.jobs.acquire_session_lock"):
        with TestClient(app) as client:
            response = client.post(
                "/jobs/upload",
                files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
            )

    assert response.status_code == 429
    # db.rollback must have been called
    assert mock_db.rollback_called
    # No job in DB store
    assert len(mock_db._store) == 0
    # Saved file must have been deleted (orphan cleanup)
    uploads = Path(upload_dir)
    files = list(uploads.iterdir()) if uploads.exists() else []
    assert len(files) == 0


def test_cookie_set_for_new_session(limit_client):
    """Request without aivps_session cookie → response sets the cookie."""
    client, _, _, _, _ = limit_client

    with patch("app.api.jobs.find_active_job", return_value=None), \
         patch("app.api.jobs.acquire_session_lock"):
        response = client.post(
            "/jobs/upload",
            files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
        )

    assert response.status_code == 201
    assert "aivps_session" in response.cookies


def test_session_id_from_cookie_used(limit_client):
    """Upload with a valid signed cookie → job.session_id == cookie's raw session id."""
    client, mock_db, _, _, _ = limit_client

    from app.services.session import mint_session_id, sign_session_id
    my_session = mint_session_id()
    signed = sign_session_id(my_session)

    with patch("app.api.jobs.find_active_job", return_value=None), \
         patch("app.api.jobs.acquire_session_lock"):
        response = client.post(
            "/jobs/upload",
            files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
            cookies={"aivps_session": signed},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["session_id"] == my_session
    # Also verify the stored job has correct session_id
    stored_job = list(mock_db._store.values())[0]
    assert stored_job.session_id == my_session


def test_cookie_set_on_429_json_response(limit_client):
    """Even on 429 (non-HX), the session cookie is refreshed on the response."""
    client, _, _, _, _ = limit_client
    active = _fake_active_job()

    with patch("app.api.jobs.find_active_job", return_value=active), \
         patch("app.api.jobs.acquire_session_lock"):
        response = client.post(
            "/jobs/upload",
            files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
        )

    assert response.status_code == 429
    assert "aivps_session" in response.cookies


def test_cookie_set_on_htmx_success(limit_client):
    """HX upload that succeeds → response sets aivps_session cookie."""
    client, _, _, _, _ = limit_client

    with patch("app.api.jobs.find_active_job", return_value=None), \
         patch("app.api.jobs.acquire_session_lock"):
        response = client.post(
            "/jobs/upload",
            files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )

    # HTMX success → HX-Redirect header
    assert response.headers.get("HX-Redirect") is not None
    assert "aivps_session" in response.cookies
