import structlog
from fastapi import FastAPI
from app.api.download import router as download_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

app = FastAPI(title="AI Video Prep Studio", version="0.1.0")
app.include_router(health_router)
app.include_router(jobs_router)
app.include_router(download_router)
