import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.models.job import Job, JobStatus  # noqa: E402
from app.services.cleanup import cleanup_expired_jobs, CleanupResult, safe_delete  # noqa: E402
from app.workers.process_job import process_job  # noqa: E402


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class MockCleanupSession:
    """Minimal mock SQLAlchemy session for cleanup_expired_jobs tests.

    Simulates the DB WHERE expires_at < now() filter in Python.
    """
    def __init__(self, jobs: list):
        self._jobs = jobs

    def scalars(self, stmt):
        now = datetime.utcnow()
        expired = [j for j in self._jobs if j.expires_at < now]
        result = MagicMock()
        result.all.return_value = expired
        return result


class WorkerMock:
    """Minimal mock SQLAlchemy session for process_job tests."""
    def __init__(self, job: Job):
        self._job = job
        self._rowcounts: list[int] = [1, 1]

    def set_rowcounts(self, *counts: int) -> None:
        self._rowcounts = list(counts)

    def get(self, model, pk):
        return self._job if str(pk) == str(self._job.id) else None

    def execute(self, stmt):
        rc = self._rowcounts.pop(0) if self._rowcounts else 1
        if rc > 0 and self._job is not None:
            try:
                for col, val_expr in stmt._values.items():
                    attr = col.key if hasattr(col, "key") else str(col)
                    val = val_expr.value if hasattr(val_expr, "value") else val_expr
                    if hasattr(self._job, attr):
                        setattr(self._job, attr, val)
            except AttributeError:
                pass
        return type("R", (), {"rowcount": rc})()

    def commit(self): pass
    def close(self): pass


def _make_expired_job(
    input_path: str | None = None,
    output_path: str | None = None,
) -> Job:
    now = datetime.utcnow()
    return Job(
        id=uuid4(),
        session_id=str(uuid4()),
        status=JobStatus.done,
        original_filename="test.mp4",
        stored_filename=f"{uuid4()}.mp4",
        input_path=input_path,
        output_path=output_path,
        created_at=now - timedelta(hours=25),
        expires_at=now - timedelta(seconds=1),
    )


def _make_fresh_job(
    input_path: str | None = None,
    output_path: str | None = None,
) -> Job:
    now = datetime.utcnow()
    return Job(
        id=uuid4(),
        session_id=str(uuid4()),
        status=JobStatus.done,
        original_filename="test.mp4",
        stored_filename=f"{uuid4()}.mp4",
        input_path=input_path,
        output_path=output_path,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )


def _make_worker_job(input_path: str, status: JobStatus = JobStatus.queued) -> Job:
    now = datetime.utcnow()
    return Job(
        id=uuid4(),
        session_id=str(uuid4()),
        status=status,
        original_filename="test.mp4",
        stored_filename="test.mp4",
        input_path=input_path,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )


def _worker_settings(upload_dir: str) -> SimpleNamespace:
    return SimpleNamespace(
        mock_processing_delay_seconds=0,
        mock_force_fail=False,
        mock_failure_trigger_enabled=False,
        upload_dir=upload_dir,
    )


# ---------------------------------------------------------------------------
# cleanup_expired_jobs tests
# ---------------------------------------------------------------------------

def test_cleanup_deletes_expired_zip(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    zip_file = output_dir / "result.zip"
    zip_file.write_bytes(b"zip content")

    job = _make_expired_job(output_path=str(zip_file))
    db = MockCleanupSession([job])

    result = cleanup_expired_jobs(
        db, output_dir=output_dir, upload_dir=tmp_path / "uploads"
    )

    assert not zip_file.exists()
    assert result.deleted_zips == 1
    assert result.deleted_inputs == 0
    assert result.errors == 0


def test_cleanup_deletes_expired_input(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    input_file = upload_dir / "video.mp4"
    input_file.write_bytes(b"video content")

    job = _make_expired_job(input_path=str(input_file))
    db = MockCleanupSession([job])

    result = cleanup_expired_jobs(
        db, output_dir=tmp_path / "outputs", upload_dir=upload_dir
    )

    assert not input_file.exists()
    assert result.deleted_inputs == 1
    assert result.deleted_zips == 0
    assert result.errors == 0


def test_cleanup_skips_non_expired(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    zip_file = output_dir / "result.zip"
    zip_file.write_bytes(b"zip content")

    job = _make_fresh_job(output_path=str(zip_file))
    db = MockCleanupSession([job])

    result = cleanup_expired_jobs(
        db, output_dir=output_dir, upload_dir=tmp_path / "uploads"
    )

    assert zip_file.exists()
    assert result == CleanupResult(0, 0, 0)


def test_cleanup_missing_file_no_error(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    missing_path = output_dir / "gone.zip"  # does NOT exist

    job = _make_expired_job(output_path=str(missing_path))
    db = MockCleanupSession([job])

    result = cleanup_expired_jobs(
        db, output_dir=output_dir, upload_dir=tmp_path / "uploads"
    )

    assert result.errors == 0
    assert result.deleted_zips == 0


def test_cleanup_multiple_expired_jobs(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()

    jobs = []
    files = []
    for i in range(3):
        f = output_dir / f"job_{i}.zip"
        f.write_bytes(b"zip")
        jobs.append(_make_expired_job(output_path=str(f)))
        files.append(f)

    db = MockCleanupSession(jobs)
    result = cleanup_expired_jobs(
        db, output_dir=output_dir, upload_dir=tmp_path / "uploads"
    )

    assert result.deleted_zips == 3
    assert result.errors == 0
    for f in files:
        assert not f.exists()


def test_cleanup_one_error_continues(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()

    file1 = output_dir / "job1.zip"
    file2 = output_dir / "job2.zip"
    file1.write_bytes(b"zip1")
    file2.write_bytes(b"zip2")

    job1 = _make_expired_job(output_path=str(file1))
    job2 = _make_expired_job(output_path=str(file2))
    db = MockCleanupSession([job1, job2])

    call_count = {"n": 0}
    original_unlink = Path.unlink

    def patched_unlink(self, missing_ok=False):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated disk error")
        return original_unlink(self, missing_ok=missing_ok)

    with patch.object(Path, "unlink", patched_unlink):
        result = cleanup_expired_jobs(
            db, output_dir=output_dir, upload_dir=tmp_path / "uploads"
        )

    assert result.errors == 1
    assert result.deleted_zips == 1


def test_cleanup_path_traversal_rejected(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    evil_dir = tmp_path / "evil"
    evil_dir.mkdir()
    evil_file = evil_dir / "bad_file.txt"
    evil_file.write_bytes(b"secret")

    job = _make_expired_job(output_path=str(evil_file))
    db = MockCleanupSession([job])

    result = cleanup_expired_jobs(
        db, output_dir=output_dir, upload_dir=tmp_path / "uploads"
    )

    assert evil_file.exists()
    assert result.errors == 1


def test_cleanup_input_path_traversal_rejected(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    evil_dir = tmp_path / "evil"
    evil_dir.mkdir()
    evil_file = evil_dir / "shadow.txt"
    evil_file.write_bytes(b"secret")

    job = _make_expired_job(input_path=str(evil_file))
    db = MockCleanupSession([job])

    result = cleanup_expired_jobs(
        db, output_dir=tmp_path / "outputs", upload_dir=upload_dir
    )

    assert evil_file.exists()
    assert result.errors == 1


# ---------------------------------------------------------------------------
# Worker: input file cleanup tests
# ---------------------------------------------------------------------------

def test_worker_deletes_input_on_success(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    input_file = upload_dir / "video.mp4"
    input_file.write_bytes(b"fake video")

    job = _make_worker_job(input_path=str(input_file))
    mock_db = WorkerMock(job)

    fake_zip = tmp_path / "out.zip"
    fake_settings = _worker_settings(str(upload_dir))

    with patch("app.workers.process_job.run_mock_output_pipeline", return_value=fake_zip):
        with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
            with patch("app.workers.process_job.settings", fake_settings):
                process_job(str(job.id))

    assert not input_file.exists()


def test_worker_deletes_input_on_failure(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    input_file = upload_dir / "video.mp4"
    input_file.write_bytes(b"fake video")

    job = _make_worker_job(input_path=str(input_file))
    mock_db = WorkerMock(job)

    fake_settings = _worker_settings(str(upload_dir))
    fake_settings.mock_force_fail = True

    with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
        with patch("app.workers.process_job.settings", fake_settings):
            with pytest.raises(RuntimeError):
                process_job(str(job.id))

    assert not input_file.exists()


def test_worker_unclaimed_skip_does_not_delete_input(tmp_path):
    """Worker that loses the guarded transition (rowcount==0) must not delete input."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    input_file = upload_dir / "video.mp4"
    input_file.write_bytes(b"fake video")

    job = _make_worker_job(input_path=str(input_file))
    mock_db = WorkerMock(job)
    mock_db.set_rowcounts(0)  # guarded transition missed — another worker claimed it

    fake_settings = _worker_settings(str(upload_dir))

    with patch("app.workers.process_job.safe_delete") as mock_safe_delete:
        with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
            with patch("app.workers.process_job.settings", fake_settings):
                process_job(str(job.id))

    mock_safe_delete.assert_not_called()
    assert input_file.exists()


def test_worker_rejects_input_path_outside_upload_dir(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    evil_dir = tmp_path / "evil"
    evil_dir.mkdir()
    evil_file = evil_dir / "bad.mp4"
    evil_file.write_bytes(b"evil content")

    job = _make_worker_job(input_path=str(evil_file))
    mock_db = WorkerMock(job)

    fake_zip = tmp_path / "out.zip"
    fake_settings = _worker_settings(str(upload_dir))

    from app.services.cleanup import safe_delete as real_safe_delete  # noqa: E402

    with patch("app.workers.process_job.safe_delete", wraps=real_safe_delete) as spy:
        with patch("app.workers.process_job.run_mock_output_pipeline", return_value=fake_zip):
            with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
                with patch("app.workers.process_job.settings", fake_settings):
                    process_job(str(job.id))

    spy.assert_called_once_with(
        str(evil_file),
        allowed_dir=Path(fake_settings.upload_dir).resolve(),
        log_context={"job_id": str(job.id), "stage": "worker"},
    )
    assert evil_file.exists()
