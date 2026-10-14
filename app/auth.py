import hashlib
import logging
import secrets
import time

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cache import redis_client
from app.config import settings
from app.db import get_db
from app.models import ApiKey

logger = logging.getLogger(__name__)

KEY_PREFIX = "snip_"
RATELIMIT_PREFIX = "ratelimit:"
WINDOW_SECONDS = 60


def generate_key() -> str:
    """Fresh API key. Shown once; only its hash is ever stored."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def check_rate_limit(key_hash: str) -> bool:
    """Fixed-window counter. True if the caller is under the limit."""
    window = int(time.time()) // WINDOW_SECONDS
    redis_key = f"{RATELIMIT_PREFIX}{key_hash}:{window}"

    pipe = redis_client.pipeline(transaction=False)
    pipe.incr(redis_key)
    pipe.expire(redis_key, WINDOW_SECONDS * 2)
    count, _ = pipe.execute()

    return count <= settings.rate_limit_per_minute


def require_api_key(
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ApiKey:
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-API-Key header",
        )

    if not x_api_key.startswith(KEY_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
        )

    key_hash = hash_key(x_api_key)
    api_key = db.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))
    if api_key is None:
        logger.warning("invalid api key attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
        )

    if not check_rate_limit(key_hash):
        logger.warning("rate limit exceeded for key %s", api_key.name)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
        )

    api_key.last_used_at = func.now()
    db.commit()

    return api_key
