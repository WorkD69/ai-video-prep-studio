import time
import structlog
from datetime import datetime
from pathlib import Path
from sqlalchemy import update

from app.config import settings
from app.database import SessionLocal
from app.models.job import Job, JobStatus
from app.pipeline.mock_pipeline import run_mock_output_pipeline
from app.services.cleanup import safe_delete

logger = structlog.get_logger()


def process_job(job_id: str) -> None:
    db = SessionLocal()
    job = None
    claimed = False  # True only after this worker wins the guarded transition
    try:
        job = db.get(Job, job_id)
        if job is None:
            logger.warning("worker_job_not_found", job_id=job_id, stage="worker")
            return

        # Guarded transition: pending|queued -> processing
        result = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status.in_([JobStatus.pending, JobStatus.queued]))
            .values(status=JobStatus.processing)
        )
        db.commit()

        if result.rowcount == 0:
            logger.info(
                "worker_skip_already_advanced",
                job_id=job_id,
                stage="worker",
            )
            return

        claimed = True

        # Mock processing
        if settings.mock_processing_delay_seconds > 0:
            time.sleep(settings.mock_processing_delay_seconds)

        should_fail = settings.mock_force_fail
        if not should_fail and settings.mock_failure_trigger_enabled:
            should_fail = job.original_filename.lower().startswith("fail")

        if should_fail:
            raise RuntimeError("mock_processing_failure")

        zip_path = run_mock_output_pipeline(job)

        # Guarded success write: processing -> done
        result = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == JobStatus.processing)
            .values(
                status=JobStatus.done,
                completed_at=datetime.utcnow(),
                output_path=str(zip_path),
            )
        )
        db.commit()

        if result.rowcount == 0:
            logger.warning("worker_success_write_noop", job_id=job_id, stage="worker")
        else:
            logger.info("worker_done", job_id=job_id, stage="worker")

    except Exception:
        if job is not None:
            try:
                result = db.execute(
                    update(Job)
                    .where(Job.id == job.id, Job.status == JobStatus.processing)
                    .values(
                        status=JobStatus.failed,
                        error_message="processing_failed",
                        completed_at=datetime.utcnow(),
                    )
                )
                db.commit()
                if result.rowcount == 0:
                    logger.warning(
                        "worker_failure_write_noop", job_id=job_id, stage="worker"
                    )
                else:
                    logger.info("worker_failed", job_id=job_id, stage="worker")
            except Exception as db_exc:
                logger.error(
                    "worker_failure_db_error",
                    job_id=job_id,
                    stage="worker",
                    error_type=type(db_exc).__name__,
                )
        raise
    finally:
        if claimed and job is not None and job.input_path:
            outcome = safe_delete(
                job.input_path,
                allowed_dir=Path(settings.upload_dir).resolve(),
                log_context={"job_id": job_id, "stage": "worker"},
            )
            if outcome == "deleted":
                logger.info("worker_input_cleaned", job_id=job_id, stage="worker")
            elif outcome == "missing":
                logger.info("worker_input_already_gone", job_id=job_id, stage="worker")
        db.close()
