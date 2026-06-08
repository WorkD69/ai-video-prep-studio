import structlog
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import update
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
from app.redis_client import get_queue
from app.schemas.job import JobStatusResponse, UploadResponse
from app.services.active_job import acquire_session_lock, find_active_job
from app.services.session import resolve_session, set_session_cookie
from app.workers.process_job import process_job

logger = structlog.get_logger()

router = APIRouter(prefix="/jobs", tags=["jobs"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _429_response(request: Request, active: Job, session_id: str) -> HTMLResponse | JSONResponse:
    """Build the 429 response — HTML fragment for HX, JSON for plain requests."""
    if request.headers.get("HX-Request") == "true":
        resp = _templates.TemplateResponse(
            request,
            "partials/upload_error.html",
            {"active_job_id": str(active.id)},
            status_code=429,
        )
        set_session_cookie(resp, session_id)
        return resp
    resp = JSONResponse(status_code=429, content={"detail": "active_job_exists"})
    set_session_cookie(resp, session_id)
    return resp


@router.post("/upload", status_code=201, response_model=UploadResponse)
async def upload_video(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResponse | Response:
    validate_content_type(file.content_type or "")
    safe_ext = validate_extension(file.filename or "")

    header = await file.read(8)
    if not header:
        raise HTTPException(status_code=400, detail="Empty payload")
    validate_magic_bytes(header, file.content_type or "")

    # --- Session resolution (ADR 004) ---
    session_id, _ = resolve_session(request)

    # --- Early pre-check (no lock, fast-fail before saving file) ---
    early_active = find_active_job(db, session_id)
    if early_active:
        return _429_response(request, early_active, session_id)

    stored_filename = f"{uuid4()}{safe_ext}"
    upload_dir = Path(settings.upload_dir).resolve()
    dest_path = (upload_dir / stored_filename).resolve()

    if dest_path.parent != upload_dir:
        raise HTTPException(status_code=500, detail="Invalid upload path")

    upload_dir.mkdir(parents=True, exist_ok=True)

    size = await stream_save(file, dest_path, header, settings.max_upload_bytes)

    # --- Advisory xact lock: serialises same-session concurrent uploads ---
    acquire_session_lock(db, session_id)

    # --- Authoritative re-check (under lock, TOCTOU-safe) ---
    locked_active = find_active_job(db, session_id)
    if locked_active:
        dest_path.unlink(missing_ok=True)
        db.rollback()
        return _429_response(request, locked_active, session_id)

    original_filename = Path(file.filename or "").name
    now = datetime.utcnow()
    job_id = uuid4()

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

    # Enqueue
    try:
        q = get_queue()
        q.enqueue(
            process_job,
            str(job.id),
            job_id=str(job.id),
            job_timeout=settings.rq_job_timeout,
        )
    except Exception as e:
        logger.error("upload_enqueue_failed", job_id=str(job.id), stage="upload", error_type=type(e).__name__)
        try:
            db.execute(
                update(Job)
                .where(Job.id == job.id, Job.status == JobStatus.pending)
                .values(
                    status=JobStatus.failed,
                    error_message="enqueue_failed",
                    completed_at=datetime.utcnow(),
                )
            )
            db.commit()
        except Exception as compensation_error:
            db.rollback()
            logger.error(
                "upload_enqueue_compensation_failed",
                job_id=str(job.id),
                stage="upload",
                error_type=type(compensation_error).__name__,
            )
        raise HTTPException(status_code=503, detail="job_enqueue_failed")

    # Guarded flip: pending -> queued
    result = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == JobStatus.pending)
        .values(status=JobStatus.queued)
    )
    db.commit()

    if result.rowcount == 0:
        db.refresh(job)
    else:
        job.status = JobStatus.queued

    # --- Cookie contract: set on the actual returned response object ---
    if request.headers.get("HX-Request") == "true":
        htmx_resp = Response(
            status_code=200,
            headers={"HX-Redirect": f"/status/{job.id}"},
        )
        set_session_cookie(htmx_resp, session_id)
        return htmx_resp

    set_session_cookie(response, session_id)
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
