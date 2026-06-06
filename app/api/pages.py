import structlog
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.job import Job, JobStatus
from app.schemas.job import JobStatusResponse

logger = structlog.get_logger()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_NON_TERMINAL = {JobStatus.pending, JobStatus.queued, JobStatus.processing}

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@router.get("/status/{job_id}", response_class=HTMLResponse)
async def status_page(
    request: Request,
    job_id: UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    logger.info("status_page", job_id=str(job_id), stage="pages")

    job_response = JobStatusResponse(
        job_id=job.id,
        status=job.status,
        original_filename=job.original_filename,
        video_size_bytes=job.video_size_bytes,
        created_at=job.created_at,
        error_message=job.error_message,
    )
    return templates.TemplateResponse(request, "status.html", {
        "job": job_response,
        "job_id": str(job_id),
        "is_polling": job.status in _NON_TERMINAL,
    })


@router.get("/status/{job_id}/fragment", response_class=HTMLResponse)
async def status_fragment(
    request: Request,
    job_id: UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    logger.info("status_fragment", job_id=str(job_id), stage="pages")

    job_response = JobStatusResponse(
        job_id=job.id,
        status=job.status,
        original_filename=job.original_filename,
        video_size_bytes=job.video_size_bytes,
        created_at=job.created_at,
        error_message=job.error_message,
    )
    return templates.TemplateResponse(request, "partials/status_card.html", {
        "job": job_response,
        "job_id": str(job_id),
        "is_polling": job.status in _NON_TERMINAL,
    })
