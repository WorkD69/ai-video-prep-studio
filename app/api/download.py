import structlog
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.job import Job, JobStatus

logger = structlog.get_logger()

router = APIRouter(tags=["download"])


@router.get("/download/{job_id}")
def download_job(job_id: UUID, db: Session = Depends(get_db)) -> FileResponse:
    job_id_str = str(job_id)
    job = db.get(Job, job_id)
    if job is None:
        logger.info("download_job_not_found", job_id=job_id_str, stage="download")
        raise HTTPException(status_code=404, detail="job_not_found")

    if job.status != JobStatus.done:
        logger.info(
            "download_job_not_ready",
            job_id=job_id_str,
            stage="download",
            status=job.status.value,
        )
        raise HTTPException(status_code=409, detail="job_not_ready")

    if datetime.utcnow() > job.expires_at:
        logger.info(
            "download_expired",
            job_id=job_id_str,
            stage="download",
            expires_at=job.expires_at.isoformat(),
        )
        raise HTTPException(status_code=410, detail="job_expired")

    if job.output_path is None:
        logger.error("download_output_path_null", job_id=job_id_str, stage="download")
        raise HTTPException(status_code=500, detail="output_path_missing")

    output_dir = Path(settings.output_dir).resolve()
    resolved_path = Path(job.output_path).resolve()

    if not resolved_path.is_relative_to(output_dir):
        logger.critical(
            "download_path_traversal_detected",
            job_id=job_id_str,
            stage="download",
        )
        raise HTTPException(status_code=500, detail="internal_error")

    if not resolved_path.exists():
        logger.error(
            "download_file_missing",
            job_id=job_id_str,
            stage="download",
            output_path=str(resolved_path),
        )
        raise HTTPException(status_code=500, detail="output_file_missing")

    filename = resolved_path.name
    logger.info(
        "download_served",
        job_id=job_id_str,
        stage="download",
        filename=filename,
        file_size_bytes=resolved_path.stat().st_size,
    )
    return FileResponse(
        path=str(resolved_path),
        media_type="application/zip",
        filename=filename,
    )
