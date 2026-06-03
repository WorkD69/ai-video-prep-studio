import redis as redis_lib
import rq
from app.config import settings


def get_redis() -> redis_lib.Redis:
    return redis_lib.from_url(settings.redis_url, decode_responses=True)


def check_redis() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False


def get_queue() -> rq.Queue:
    conn = redis_lib.from_url(settings.redis_url)
    return rq.Queue(settings.rq_queue_name, connection=conn)
