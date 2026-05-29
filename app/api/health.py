from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.redis_client import check_redis

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    redis_status = "ok" if check_redis() else "error"
    overall = "ok" if db_status == "ok" and redis_status == "ok" else "error"

    return {"status": overall, "db": db_status, "redis": redis_status}
