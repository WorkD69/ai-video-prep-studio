from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job

logger = structlog.get_logger()


@dataclass
class CleanupResult:
    deleted_zips: int
    deleted_inputs: int
    errors: int


def safe_delete(path_str: str, allowed_dir: Path, log_context: dict) -> str:
    """Validate path against allowed_dir and delete if it exists.

    Returns one of: "deleted" | "missing" | "traversal" | "error".
    Never raises.
    """
    try:
        path = Path(path_str).resolve()
        if not path.is_relative_to(allowed_dir.resolve()):
            logger.critical("cleanup_path_traversal_detected", **log_context, path=path_str)
            return "traversal"
        if not path.exists():
            return "missing"
        path.unlink()
        return "deleted"
    except Exception as exc:
        logger.error(
            "cleanup_delete_error",
            **log_context,
            path=path_str,
            error=str(exc),
        )
        return "error"


def delete_download_leftovers(input_path: str, upload_dir: Path, log_context: dict) -> int:
    """Delete <uuid-stem>.* siblings of a job's input file (partial yt-dlp
    downloads). The stem is a server-generated UUID, so the glob only matches
    this job's files. Returns the number of files deleted."""
    stem = Path(input_path).stem
    deleted = 0
    for leftover in upload_dir.glob(f"{stem}.*"):
        if leftover.name == Path(input_path).name:
            continue
        if safe_delete(str(leftover), allowed_dir=upload_dir, log_context=log_context) == "deleted":
            deleted += 1
    return deleted


def cleanup_expired_jobs(
    db: Session,
    *,
    output_dir: Path,
    upload_dir: Path,
) -> CleanupResult:
    """Delete files for expired jobs. Does not modify DB records."""
    deleted_zips = 0
    deleted_inputs = 0
    errors = 0

    expired_jobs = db.scalars(
        select(Job).where(Job.expires_at < datetime.utcnow())
    ).all()

    for job in expired_jobs:
        try:
            if job.output_path:
                outcome = safe_delete(
                    job.output_path,
                    allowed_dir=output_dir,
                    log_context={"job_id": str(job.id), "stage": "cleanup"},
                )
                if outcome == "deleted":
                    deleted_zips += 1
                elif outcome in ("traversal", "error"):
                    errors += 1

            if job.input_path:
                outcome = safe_delete(
                    job.input_path,
                    allowed_dir=upload_dir,
                    log_context={"job_id": str(job.id), "stage": "cleanup"},
                )
                if outcome == "deleted":
                    deleted_inputs += 1
                elif outcome in ("traversal", "error"):
                    errors += 1
                deleted_inputs += delete_download_leftovers(
                    job.input_path,
                    upload_dir,
                    log_context={"job_id": str(job.id), "stage": "cleanup"},
                )

        except Exception as exc:
            logger.error(
                "cleanup_job_error",
                job_id=str(job.id),
                stage="cleanup",
                error=str(exc),
            )
            errors += 1

    logger.info(
        "cleanup_run",
        deleted_zips=deleted_zips,
        deleted_inputs=deleted_inputs,
        errors=errors,
        stage="cleanup",
    )
    return CleanupResult(
        deleted_zips=deleted_zips,
        deleted_inputs=deleted_inputs,
        errors=errors,
    )
