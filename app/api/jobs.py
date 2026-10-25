import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.job import Job, JobStatus
from app.pipeline.media import MediaError, get_media_prober
from app.pipeline.url_source import UrlSourceError, probe_url, validate_url
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


_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
_MAX_TERMS_CHARS = 500

_URL_ERROR_MESSAGES = {
    "invalid_url": "Invalid link: use a full http(s) URL",
    "forbidden_host": "This link points to a local or private address",
    "unreadable_link": "Could not read this link (unsupported site, private or removed video)",
    "playlist_not_supported": "Playlists are not supported: link a single video",
    "live_not_supported": "Live streams are not supported",
    "unknown_duration": "Could not determine the video duration",
}


def normalize_language(value: str | None) -> str | None:
    """'auto'/empty -> None (whisper auto-detect); otherwise a 2-3 letter code."""
    value = (value or "").strip().lower()
    if value in ("", "auto"):
        return None
    if not _LANGUAGE_RE.match(value):
        raise HTTPException(status_code=422, detail="Invalid language code")
    return value


def normalize_terms(value: str | None) -> str | None:
    """Per-job whisper terms: printable text, capped length."""
    cleaned = "".join(ch for ch in (value or "") if ch.isprintable() or ch == "\n").strip()
    return cleaned[:_MAX_TERMS_CHARS] or None


def duration_limit_message() -> str:
    """Safe user-facing text for the duration limit, derived from settings."""
    minutes = settings.max_duration_seconds // 60
    if minutes % 60 == 0:
        return f"Video exceeds the {minutes // 60}-hour limit"
    return f"Video exceeds the {minutes}-minute limit"


def _upload_reject_response(
    request: Request, session_id: str, status_code: int, message: str
) -> HTMLResponse | JSONResponse:
    """Build the 400/422 upload-reject response — HTML fragment for HX, JSON otherwise.

    The message is a fixed safe string — never raw ffprobe stderr or tracebacks.
    """
    if request.headers.get("HX-Request") == "true":
        resp = _templates.TemplateResponse(
            request,
            "partials/upload_error_message.html",
            {"message": message},
            status_code=status_code,
        )
        set_session_cookie(resp, session_id)
        return resp
    resp = JSONResponse(status_code=status_code, content={"detail": message})
    set_session_cookie(resp, session_id)
    return resp


@router.post("/upload", status_code=201, response_model=UploadResponse)
async def upload_video(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    language: str = Form("auto"),
    terms: str = Form(""),
    db: Session = Depends(get_db),
) -> UploadResponse | Response:
    language = normalize_language(language)
    glossary = normalize_terms(terms)
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

    # --- Upload-time duration enforcement (M009, ADR 005) ---
    # ffprobe runs on the trusted UUID-named path under upload_dir; rejects
    # delete the saved file before any job row is created (no orphans).
    prober = get_media_prober()
    try:
        duration = prober.probe_duration(str(dest_path))
    except MediaError:
        dest_path.unlink(missing_ok=True)
        logger.info("upload_rejected_unreadable_media", stage="upload")
        return _upload_reject_response(
            request, session_id, 400, "Invalid or unreadable media file"
        )

    if duration > settings.max_duration_seconds:
        dest_path.unlink(missing_ok=True)
        logger.info(
            "upload_rejected_duration_limit",
            stage="upload",
            duration_seconds=duration,
        )
        return _upload_reject_response(
            request, session_id, 422, duration_limit_message()
        )

    return _create_and_enqueue_job(
        request,
        response,
        db,
        session_id,
        cleanup=lambda: dest_path.unlink(missing_ok=True),
        original_filename=Path(file.filename or "").name,
        stored_filename=stored_filename,
        input_path=str(dest_path),
        video_size_bytes=size,
        duration_seconds=duration,
        source_type="file",
        language=language,
        glossary=glossary,
    )


@router.post("/from-url", status_code=201, response_model=UploadResponse)
def create_job_from_url(
    request: Request,
    response: Response,
    url: str = Form(...),
    language: str = Form("auto"),
    terms: str = Form(""),
    db: Session = Depends(get_db),
) -> UploadResponse | Response:
    """Queue a job for a link (any yt-dlp site). Metadata is probed now so the
    duration limit is enforced before enqueue; the worker downloads."""
    language_code = normalize_language(language)
    glossary = normalize_terms(terms)
    session_id, _ = resolve_session(request)

    early_active = find_active_job(db, session_id)
    if early_active:
        return _429_response(request, early_active, session_id)

    try:
        safe_url = validate_url(url)
        probe = probe_url(safe_url)
    except UrlSourceError as exc:
        logger.info("url_rejected", stage="url_intake", reason=str(exc))
        message = _URL_ERROR_MESSAGES.get(str(exc), "Could not read this link")
        return _upload_reject_response(request, session_id, 400, message)

    if probe.duration > settings.max_duration_seconds:
        logger.info("url_rejected_duration_limit", stage="url_intake", duration_seconds=probe.duration)
        return _upload_reject_response(request, session_id, 422, duration_limit_message())

    # The worker downloads to this trusted UUID-named path under upload_dir, so
    # retention cleanup and safe_delete treat it exactly like an upload.
    stored_filename = f"{uuid4()}.mp4"
    upload_dir = Path(settings.upload_dir).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    return _create_and_enqueue_job(
        request,
        response,
        db,
        session_id,
        cleanup=lambda: None,
        original_filename=probe.title[:500],
        stored_filename=stored_filename,
        input_path=str(upload_dir / stored_filename),
        video_size_bytes=None,
        duration_seconds=probe.duration,
        source_type="url",
        source_url=safe_url,
        language=language_code,
        glossary=glossary,
    )


def _create_and_enqueue_job(
    request: Request,
    response: Response,
    db: Session,
    session_id: str,
    cleanup: Callable[[], None],
    **job_fields,
) -> UploadResponse | Response:
    """Shared tail of both intake endpoints: session lock, authoritative
    active-job re-check, job row, enqueue, pending -> queued, response.

    cleanup() removes intake artefacts (the uploaded file) on reject/failure.
    """
    # --- Advisory xact lock: serialises same-session concurrent submissions ---
    acquire_session_lock(db, session_id)

    # --- Authoritative re-check (under lock, TOCTOU-safe) ---
    locked_active = find_active_job(db, session_id)
    if locked_active:
        cleanup()
        db.rollback()
        return _429_response(request, locked_active, session_id)

    now = datetime.utcnow()
    job_id = uuid4()

    job = Job(
        id=job_id,
        session_id=session_id,
        status=JobStatus.pending,
        created_at=now,
        expires_at=now + timedelta(hours=24),
        **job_fields,
    )

    try:
        db.add(job)
        db.commit()
        db.refresh(job)
    except Exception as e:
        db.rollback()
        cleanup()
        logger.error("upload_db_failed", job_id=str(job_id), stage="upload", error_type=type(e).__name__)
        raise HTTPException(status_code=500, detail="Storage failure")

    logger.info(
        "upload_saved",
        job_id=str(job.id),
        stage="upload",
        source_type=job.source_type,
        bytes=job.video_size_bytes,
    )

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
