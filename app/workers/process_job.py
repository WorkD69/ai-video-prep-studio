import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy import update

from app.config import settings
from app.database import SessionLocal
from app.models.job import Job, JobStatus
from app.pipeline.frames import get_frame_selectors
from app.pipeline.media import MediaError, get_media_prober
from app.pipeline.media_pipeline import run_media_pipeline
from app.pipeline.url_source import UrlSourceError
from app.services.cleanup import delete_download_leftovers, safe_delete

logger = structlog.get_logger()


def _stage_updater(db, job_id) -> Callable[[str], None]:
    """Persist the pipeline stage for the status page (best effort)."""

    def update_stage(stage: str) -> None:
        try:
            db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.processing)
                .values(stage=stage)
            )
            db.commit()
            logger.info("worker_stage", job_id=str(job_id), stage=stage)
        except Exception as exc:
            db.rollback()
            logger.warning("worker_stage_write_failed", job_id=str(job_id), stage=stage,
                           error_type=type(exc).__name__)

    return update_stage


def _failure_code(exc: Exception) -> str:
    """Safe, known failure codes are shown to the user; anything else is generic."""
    if isinstance(exc, (MediaError, UrlSourceError)):
        return str(exc)
    return "processing_failed"


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

        frame_selector, fallback_selector = get_frame_selectors()
        zip_path = run_media_pipeline(
            job,
            get_media_prober(),
            frame_selector,
            fallback_selector,
            on_stage=_stage_updater(db, job.id),
        )

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

    except Exception as exc:
        if job is not None:
            try:
                result = db.execute(
                    update(Job)
                    .where(Job.id == job.id, Job.status == JobStatus.processing)
                    .values(
                        status=JobStatus.failed,
                        error_message=_failure_code(exc),
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
            delete_download_leftovers(
                job.input_path,
                Path(settings.upload_dir).resolve(),
                log_context={"job_id": job_id, "stage": "worker"},
            )
        db.close()
