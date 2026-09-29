import redis

from app.config import settings

redis_client = redis.from_url(settings.redis_url, decode_responses=True)

CACHE_PREFIX = "link:"


def cache_get(code: str) -> str | None:
    return redis_client.get(f"{CACHE_PREFIX}{code}")


def cache_set(code: str, url: str, ttl: int = 3600) -> None:
    redis_client.set(f"{CACHE_PREFIX}{code}", url, ex=ttl)


def cache_delete(code: str) -> None:
    redis_client.delete(f"{CACHE_PREFIX}{code}")
