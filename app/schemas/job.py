from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.job import JobStatus


class UploadResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    session_id: str
    original_filename: str
    video_size_bytes: int
    created_at: datetime


class JobStatusResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    original_filename: str
    video_size_bytes: int | None
    created_at: datetime
    error_message: str | None
