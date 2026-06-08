"""Active-job limit helpers (M008).

These functions are seams: the upload endpoint imports them by name so tests can
patch `app.api.jobs.find_active_job` / `app.api.jobs.acquire_session_lock`.
"""
from hashlib import blake2b

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus

_ACTIVE_STATUSES = [JobStatus.pending, JobStatus.queued, JobStatus.processing]


def session_lock_key(session_id: str) -> int:
    """Deterministic signed int64 for pg_advisory_xact_lock keyed on session_id.

    Uses blake2b-8 so the full 64-bit space is used; collisions are benign
    (rare contention, not incorrect limit, since WHERE session_id matches exactly).
    """
    digest = blake2b(session_id.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def acquire_session_lock(db: Session, session_id: str) -> None:
    """Acquire a transaction-level advisory lock keyed on session_id.

    Released automatically at the first commit or rollback on the same
    connection — spans exactly the check→insert window.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(:k)"),
        {"k": session_lock_key(session_id)},
    )


def find_active_job(db: Session, session_id: str) -> Job | None:
    """Return the first active (pending/queued/processing) job for the session, or None."""
    stmt = (
        select(Job)
        .where(
            Job.session_id == session_id,
            Job.status.in_(_ACTIVE_STATUSES),
        )
        .limit(1)
    )
    result = db.execute(stmt)
    return result.scalars().first()
