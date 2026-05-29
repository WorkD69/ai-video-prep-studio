import redis as redis_lib
from app.config import settings


def get_redis() -> redis_lib.Redis:
    return redis_lib.from_url(settings.redis_url, decode_responses=True)


def check_redis() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
