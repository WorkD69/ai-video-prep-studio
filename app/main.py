import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI

from app.api.download import router as download_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.pages import router as pages_router
from app.config import settings
from app.database import SessionLocal
from app.services.cleanup import cleanup_expired_jobs

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


def _run_cleanup_sync() -> None:
    db = SessionLocal()
    try:
        result = cleanup_expired_jobs(
            db,
            output_dir=Path(settings.output_dir).resolve(),
            upload_dir=Path(settings.upload_dir).resolve(),
        )
        logger.info(
            "cleanup_run",
            deleted_zips=result.deleted_zips,
            deleted_inputs=result.deleted_inputs,
            errors=result.errors,
            stage="cleanup",
        )
    except Exception:
        logger.exception("cleanup_sync_error", stage="cleanup")
    finally:
        db.close()


async def _cleanup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _run_cleanup_sync)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("cleanup_loop_unexpected_error", stage="cleanup")


@asynccontextmanager
async def lifespan(app_: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="AI Video Prep Studio", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(jobs_router)
app.include_router(download_router)
app.include_router(pages_router)
