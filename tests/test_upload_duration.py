"""Upload-time duration enforcement tests (Milestone 009, ADR 005).

Fake prober injected via app.api.jobs.get_media_prober — no real ffprobe.
Covers: 422 over-limit, 3600 boundary, 400 unreadable, HX inline vs JSON,
file deletion and no-job guarantees on every reject path.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")
os.environ.setdefault("MEDIA_BACKEND", "fake")

from app.main import app  # noqa: E402
from app.database import get_db  # noqa: E402
from app.models.job import Job  # noqa: E402
from app.pipeline.media import FakeMediaProber, MediaError  # noqa: E402

# bytes[4:8] == b"ftyp" — valid MP4/MOV magic
VALID_MP4_CONTENT = b"\x00\x00\x00\x18" + b"ftypisom" + b"\x00" * 100


class MockResult:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount


class MockDB:
    def __init__(self):
        self._pending: dict[str, Job] = {}
        self._store: dict[str, Job] = {}

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

    def get(self, model, pk):
        return self._store.get(str(pk)) if model is Job else None

    def execute(self, stmt):
        return MockResult(1)


class FakeSettings:
    def __init__(self, upload_dir: str):
        self.upload_dir = upload_dir
        self.max_upload_bytes = 524288000
        self.max_duration_seconds = 3600
        self.rq_job_timeout = 600
        self.session_cookie_name = "aivps_session"
        self.session_cookie_max_age = 2592000
        self.session_cookie_secure = False


class RaisingProber:
    def probe_duration(self, input_path: str) -> float:
        raise MediaError("invalid_or_unreadable_media")


@pytest.fixture
def duration_setup(tmp_path):
    mock_db = MockDB()

    def override():
        yield mock_db

    app.dependency_overrides[get_db] = override
    yield mock_db, tmp_path
    app.dependency_overrides.clear()


def _post_upload(duration_or_prober, duration_setup, headers=None):
    """Run one upload with the given prober (or fixed duration). Returns
    (response, mock_db, upload_dir_path, mock_queue)."""
    mock_db, tmp_path = duration_setup
    upload_dir = str(tmp_path / "uploads")
    fake_settings = FakeSettings(upload_dir)
    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = MagicMock()

    if isinstance(duration_or_prober, (int, float)):
        prober = FakeMediaProber(duration=float(duration_or_prober))
    else:
        prober = duration_or_prober

    with patch("app.api.jobs.settings", fake_settings), \
         patch("app.api.jobs.get_queue", return_value=mock_queue), \
         patch("app.api.jobs.get_media_prober", return_value=prober):
        with TestClient(app) as client:
            response = client.post(
                "/jobs/upload",
                files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
                headers=headers or {},
            )

    return response, mock_db, Path(upload_dir), mock_queue


def _files_in(upload_dir: Path) -> list:
    return list(upload_dir.iterdir()) if upload_dir.exists() else []


# ---------------------------------------------------------------------------
# 422 — over the 60-minute limit
# ---------------------------------------------------------------------------


def test_duration_3601_rejected_422_json(duration_setup):
    response, mock_db, upload_dir, mock_queue = _post_upload(3601, duration_setup)

    assert response.status_code == 422
    assert response.json()["detail"] == "Video exceeds the 60-minute limit"
    assert _files_in(upload_dir) == []          # file deleted
    assert len(mock_db._store) == 0             # no job row
    mock_queue.enqueue.assert_not_called()      # no enqueue


def test_duration_3601_rejected_sets_session_cookie(duration_setup):
    response, _, _, _ = _post_upload(3601, duration_setup)
    assert response.status_code == 422
    assert "aivps_session" in response.cookies


# ---------------------------------------------------------------------------
# Boundary: exactly 3600 allowed (strict >)
# ---------------------------------------------------------------------------


def test_duration_exactly_3600_allowed(duration_setup):
    response, mock_db, upload_dir, mock_queue = _post_upload(3600, duration_setup)

    assert response.status_code == 201
    assert len(mock_db._store) == 1
    job = list(mock_db._store.values())[0]
    assert job.duration_seconds == 3600
    assert len(_files_in(upload_dir)) == 1
    mock_queue.enqueue.assert_called_once()


def test_duration_1800_persisted_on_job(duration_setup):
    response, mock_db, _, _ = _post_upload(1800, duration_setup)

    assert response.status_code == 201
    job = list(mock_db._store.values())[0]
    assert job.duration_seconds == 1800


# ---------------------------------------------------------------------------
# 400 — corrupt/unreadable media (passed magic bytes, ffprobe failed)
# ---------------------------------------------------------------------------


def test_unreadable_media_rejected_400_json(duration_setup):
    response, mock_db, upload_dir, mock_queue = _post_upload(RaisingProber(), duration_setup)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or unreadable media file"
    assert _files_in(upload_dir) == []
    assert len(mock_db._store) == 0
    mock_queue.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# HTMX rejects — inline fragment into #upload-error
# ---------------------------------------------------------------------------


def test_htmx_duration_reject_renders_inline_fragment(duration_setup):
    response, mock_db, upload_dir, _ = _post_upload(
        3601, duration_setup, headers={"HX-Request": "true"}
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "Video exceeds the 60-minute limit" in response.text
    assert _files_in(upload_dir) == []
    assert len(mock_db._store) == 0
    assert "aivps_session" in response.cookies


def test_htmx_unreadable_reject_renders_inline_fragment(duration_setup):
    response, mock_db, upload_dir, _ = _post_upload(
        RaisingProber(), duration_setup, headers={"HX-Request": "true"}
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "Invalid or unreadable media file" in response.text
    assert _files_in(upload_dir) == []
    assert len(mock_db._store) == 0


def test_non_htmx_duration_reject_returns_json(duration_setup):
    response, _, _, _ = _post_upload(3601, duration_setup)
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
