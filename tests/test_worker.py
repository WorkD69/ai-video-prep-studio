import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.models.job import Job, JobStatus  # noqa: E402
from app.workers.process_job import process_job  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


class MockResult:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount


class WorkerMockSession:
    """Minimal mock of a SQLAlchemy session for worker tests.

    execute() applies the UPDATE values to the in-memory Job when rowcount > 0,
    so post-process_job assertions on job.status/error_message/completed_at
    prove the production code actually emits the correct .values(…) clauses.
    """

    def __init__(self, job=None):
        self._job = job
        self._rowcounts: list[int] = []
        self.execute_calls: list = []
        self.commit_count = 0
        # Optional per-call hooks fired before values are applied. Each entry is
        # a callable (called with no args) or None. Lets tests simulate an
        # external race that modifies job state between the guard read and the
        # guarded UPDATE.
        self._execute_side_effects: list = []

    def set_rowcounts(self, *counts: int) -> None:
        self._rowcounts = list(counts)

    def get(self, model, pk):
        if self._job and str(pk) == str(self._job.id):
            return self._job
        return None

    def execute(self, stmt):
        # Fire race-simulation hook for this call position (before values applied)
        if self._execute_side_effects:
            hook = self._execute_side_effects.pop(0)
            if callable(hook):
                hook()

        self.execute_calls.append(stmt)
        rc = self._rowcounts.pop(0) if self._rowcounts else 1

        if rc > 0 and self._job is not None:
            values = _extract_stmt_values(stmt)
            for attr, val in values.items():
                if hasattr(self._job, attr):
                    setattr(self._job, attr, val)

        return MockResult(rc)

    def commit(self):
        self.commit_count += 1

    def close(self):
        pass


def make_job(status: JobStatus = JobStatus.queued, filename: str = "lecture.mp4") -> Job:
    now = datetime.utcnow()
    return Job(
        id=uuid4(),
        session_id=str(uuid4()),
        status=status,
        original_filename=filename,
        stored_filename=f"{uuid4()}.mp4",
        input_path="/uploads/test.mp4",
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_worker_happy_path_done():
    job = make_job(JobStatus.queued)
    mock_db = WorkerMockSession(job)
    # execute call 1: guarded -> processing (rowcount=1)
    # execute call 2: guarded -> done (rowcount=1)
    mock_db.set_rowcounts(1, 1)

    fake_zip = Path("/tmp/fake.zip")
    with patch("app.workers.process_job.run_mock_output_pipeline", return_value=fake_zip):
        with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
            process_job(str(job.id))

    assert mock_db.commit_count == 2
    assert len(mock_db.execute_calls) == 2
    assert job.status == JobStatus.done
    assert job.completed_at is not None
    assert job.output_path == str(fake_zip)


def test_worker_picks_pending_or_queued():
    fake_zip = Path("/tmp/fake.zip")
    for initial_status in (JobStatus.pending, JobStatus.queued):
        job = make_job(initial_status)
        mock_db = WorkerMockSession(job)
        mock_db.set_rowcounts(1, 1)

        with patch("app.workers.process_job.run_mock_output_pipeline", return_value=fake_zip):
            with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
                process_job(str(job.id))

        assert mock_db.commit_count == 2
        assert job.status == JobStatus.done
        assert job.completed_at is not None


def test_worker_idempotent_skip_if_done():
    job = make_job(JobStatus.done)
    mock_db = WorkerMockSession(job)
    mock_db.set_rowcounts(0)  # guarded transition returns 0 rows

    with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
        process_job(str(job.id))

    assert mock_db.commit_count == 1
    assert len(mock_db.execute_calls) == 1
    # Job must NOT have been moved to processing (guard was a no-op)
    assert job.status == JobStatus.done


def test_worker_skip_if_already_processing_or_failed():
    for status in (JobStatus.processing, JobStatus.failed):
        job = make_job(status)
        mock_db = WorkerMockSession(job)
        mock_db.set_rowcounts(0)

        with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
            process_job(str(job.id))

        assert mock_db.commit_count == 1
        # Status must be unchanged (guard was a no-op)
        assert job.status == status


def test_worker_failure_trigger_marks_failed():
    job = make_job(JobStatus.queued)
    mock_db = WorkerMockSession(job)
    mock_db.set_rowcounts(1, 1)  # transition + failure write

    fake_settings = type("S", (), {
        "mock_processing_delay_seconds": 0,
        "mock_force_fail": True,
        "mock_failure_trigger_enabled": False,
        "upload_dir": "./uploads",
    })()

    with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
        with patch("app.workers.process_job.settings", fake_settings):
            with pytest.raises(RuntimeError):
                process_job(str(job.id))

    assert len(mock_db.execute_calls) == 2
    assert mock_db.commit_count == 2
    assert job.status == JobStatus.failed
    assert job.error_message == "processing_failed"
    assert job.completed_at is not None


def test_worker_job_not_found():
    mock_db = WorkerMockSession(job=None)

    with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
        process_job(str(uuid4()))  # must not raise

    assert mock_db.commit_count == 0


def test_worker_filename_fail_trigger_disabled_by_default():
    job = make_job(JobStatus.queued, filename="fail_video.mp4")
    mock_db = WorkerMockSession(job)
    mock_db.set_rowcounts(1, 1)

    fake_settings = type("S", (), {
        "mock_processing_delay_seconds": 0,
        "mock_force_fail": False,
        "mock_failure_trigger_enabled": False,  # default production config
        "upload_dir": "./uploads",
    })()

    fake_zip = Path("/tmp/fake.zip")
    with patch("app.workers.process_job.run_mock_output_pipeline", return_value=fake_zip):
        with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
            with patch("app.workers.process_job.settings", fake_settings):
                process_job(str(job.id))  # must NOT raise

    assert len(mock_db.execute_calls) == 2
    assert mock_db.commit_count == 2
    assert job.status == JobStatus.done
    assert job.completed_at is not None


def test_worker_filename_fail_trigger_enabled():
    job = make_job(JobStatus.queued, filename="fail_video.mp4")
    mock_db = WorkerMockSession(job)
    mock_db.set_rowcounts(1, 1)

    fake_settings = type("S", (), {
        "mock_processing_delay_seconds": 0,
        "mock_force_fail": False,
        "mock_failure_trigger_enabled": True,
        "upload_dir": "./uploads",
    })()

    with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
        with patch("app.workers.process_job.settings", fake_settings):
            with pytest.raises(RuntimeError):
                process_job(str(job.id))

    assert len(mock_db.execute_calls) == 2
    assert mock_db.commit_count == 2
    assert job.status == JobStatus.failed
    assert job.error_message == "processing_failed"
    assert job.completed_at is not None


def test_worker_success_guarded_does_not_overwrite_non_processing():
    """Final success UPDATE WHERE status=processing returns 0 rows because an
    external race already set the job to failed -> done must NOT be written."""
    job = make_job(JobStatus.queued)
    mock_db = WorkerMockSession(job)
    # execute 1: transition rowcount=1  -> job becomes processing
    # execute 2: success write rowcount=0 -> guard WHERE status=processing missed
    mock_db.set_rowcounts(1, 0)

    race_dt = datetime(2025, 1, 1, 12, 0, 0)

    def simulate_race_failed():
        # External process already moved the job to failed before the final write
        job.status = JobStatus.failed
        job.error_message = "external_failure"
        job.completed_at = race_dt

    # No hook for execute 1; race fires just before execute 2
    mock_db._execute_side_effects = [None, simulate_race_failed]

    fake_zip = Path("/tmp/fake.zip")
    with patch("app.workers.process_job.run_mock_output_pipeline", return_value=fake_zip):
        with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
            process_job(str(job.id))  # must NOT raise

    assert len(mock_db.execute_calls) == 2
    # done was NOT written — existing failed state is preserved
    assert job.status == JobStatus.failed
    assert job.error_message == "external_failure"
    assert job.completed_at == race_dt


def test_worker_failure_guarded_does_not_overwrite_non_processing():
    """Final failure UPDATE WHERE status=processing returns 0 rows because an
    external race already set the job to done -> failed must NOT be written."""
    job = make_job(JobStatus.queued)
    mock_db = WorkerMockSession(job)
    # execute 1: transition rowcount=1  -> job becomes processing
    # execute 2: failure write rowcount=0 -> guard WHERE status=processing missed
    mock_db.set_rowcounts(1, 0)

    race_dt = datetime(2025, 1, 1, 12, 0, 0)

    def simulate_race_done():
        # External process already moved the job to done before the failure handler
        job.status = JobStatus.done
        job.completed_at = race_dt

    # No hook for execute 1; race fires just before execute 2
    mock_db._execute_side_effects = [None, simulate_race_done]

    fake_settings = type("S", (), {
        "mock_processing_delay_seconds": 0,
        "mock_force_fail": True,
        "mock_failure_trigger_enabled": False,
        "upload_dir": "./uploads",
    })()

    with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
        with patch("app.workers.process_job.settings", fake_settings):
            with pytest.raises(RuntimeError):
                process_job(str(job.id))

    assert len(mock_db.execute_calls) == 2
    # failed was NOT written — existing done state is preserved
    assert job.status == JobStatus.done
    assert job.error_message is None
    assert job.completed_at == race_dt


def test_worker_success_sets_output_path():
    """After a successful run, job.output_path must be a non-null string."""
    job = make_job(JobStatus.queued)
    mock_db = WorkerMockSession(job)
    mock_db.set_rowcounts(1, 1)

    fake_zip = Path("/tmp/output/llm_package.zip")
    with patch("app.workers.process_job.run_mock_output_pipeline", return_value=fake_zip):
        with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
            process_job(str(job.id))

    assert job.status == JobStatus.done
    assert job.output_path is not None
    assert isinstance(job.output_path, str)
    assert job.output_path == str(fake_zip)


def test_worker_pipeline_failure_marks_failed():
    """If run_mock_output_pipeline raises, job ends up failed and exception re-raised."""
    job = make_job(JobStatus.queued)
    mock_db = WorkerMockSession(job)
    mock_db.set_rowcounts(1, 1)  # transition + failure write

    with patch(
        "app.workers.process_job.run_mock_output_pipeline",
        side_effect=RuntimeError("pipeline_exploded"),
    ):
        with patch("app.workers.process_job.SessionLocal", return_value=mock_db):
            with pytest.raises(RuntimeError, match="pipeline_exploded"):
                process_job(str(job.id))

    assert job.status == JobStatus.failed
    assert job.error_message == "processing_failed"
    assert job.completed_at is not None
