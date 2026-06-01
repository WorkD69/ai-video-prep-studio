import structlog
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.job import Job, JobStatus
from app.pipeline.upload import (
    stream_save,
    validate_content_type,
    validate_extension,
    validate_magic_bytes,
)
from app.schemas.job import JobStatusResponse, UploadResponse

logger = structlog.get_logger()

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/upload", status_code=201, response_model=UploadResponse)
async def upload_video(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResponse:
    validate_content_type(file.content_type or "")
    safe_ext = validate_extension(file.filename or "")

    header = await file.read(8)
    if not header:
        raise HTTPException(status_code=400, detail="Empty payload")
    validate_magic_bytes(header, file.content_type or "")

    stored_filename = f"{uuid4()}{safe_ext}"
    upload_dir = Path(settings.upload_dir).resolve()
    dest_path = (upload_dir / stored_filename).resolve()

    if dest_path.parent != upload_dir:
        raise HTTPException(status_code=500, detail="Invalid upload path")

    upload_dir.mkdir(parents=True, exist_ok=True)

    size = await stream_save(file, dest_path, header, settings.max_upload_bytes)

    original_filename = Path(file.filename or "").name
    now = datetime.utcnow()
    job_id = uuid4()
    session_id = str(uuid4())

    job = Job(
        id=job_id,
        session_id=session_id,
        status=JobStatus.pending,
        original_filename=original_filename,
        stored_filename=stored_filename,
        input_path=str(dest_path),
        video_size_bytes=size,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )

    try:
        db.add(job)
        db.commit()
        db.refresh(job)
    except Exception as e:
        db.rollback()
        dest_path.unlink(missing_ok=True)
        logger.error("upload_db_failed", job_id=str(job_id), stage="upload", error_type=type(e).__name__)
        raise HTTPException(status_code=500, detail="Storage failure")

    logger.info("upload_saved", job_id=str(job.id), stage="upload", bytes=size)

    return UploadResponse(
        job_id=job.id,
        status=job.status,
        session_id=session_id,
        original_filename=job.original_filename,
        video_size_bytes=job.video_size_bytes,
        created_at=job.created_at,
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: UUID, db: Session = Depends(get_db)) -> JobStatusResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        original_filename=job.original_filename,
        video_size_bytes=job.video_size_bytes,
        created_at=job.created_at,
        error_message=job.error_message,
    )
