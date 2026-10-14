import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from app.cache import redis_client
from app.db import SessionLocal

logger = logging.getLogger(__name__)

QUEUE_KEY = "clicks:queue"
PENDING_PREFIX = "clicks:pending:"
FLUSH_BATCH = 500


def record_click(code: str) -> None:
    """Queue a click in Redis. No Postgres write. Never breaks the redirect."""
    payload = json.dumps(
        {
            "event_id": str(uuid.uuid4()),
            "code": code,
            "clicked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        pipe = redis_client.pipeline(transaction=False)
        pipe.incr(f"{PENDING_PREFIX}{code}")
        pipe.rpush(QUEUE_KEY, payload)
        pipe.execute()
    except Exception:
        logger.exception("failed to record click for %s", code)


def pending_count(code: str) -> int:
    try:
        value = redis_client.get(f"{PENDING_PREFIX}{code}")
        return int(value) if value else 0
    except Exception:
        return 0


def flush_pending() -> int:
    """Drain the Redis queue into Postgres. Idempotent, safe to retry."""
    total_inserted = 0

    while True:
        raw = redis_client.lrange(QUEUE_KEY, 0, FLUSH_BATCH - 1)
        if not raw:
            break

        rows = [json.loads(item) for item in raw]
        processed: dict[str, int] = {}
        inserted: dict[str, int] = {}

        db = SessionLocal()
        try:
            for row in rows:
                code = row["code"]
                processed[code] = processed.get(code, 0) + 1

                link_id = db.execute(
                    text(
                        """
                        INSERT INTO click_events (link_id, clicked_at, event_id)
                        SELECT id, CAST(:clicked_at AS timestamptz), :event_id
                        FROM links WHERE code = :code
                        ON CONFLICT (event_id) DO NOTHING
                        RETURNING link_id
                        """
                    ),
                    {
                        "clicked_at": row["clicked_at"],
                        "event_id": row["event_id"],
                        "code": code,
                    },
                ).scalar()

                if link_id is not None:
                    inserted[code] = inserted.get(code, 0) + 1

            for code, count in inserted.items():
                db.execute(
                    text("UPDATE links SET clicks = clicks + :n WHERE code = :code"),
                    {"n": count, "code": code},
                )

            db.commit()
        except Exception:
            db.rollback()
            logger.exception("flush failed; queue left intact for retry")
            return total_inserted
        finally:
            db.close()

        # Only trim AFTER a successful commit
        redis_client.ltrim(QUEUE_KEY, len(raw), -1)
        for code, count in processed.items():
            redis_client.decrby(f"{PENDING_PREFIX}{code}", count)

        total_inserted += sum(inserted.values())

        if len(raw) < FLUSH_BATCH:
            break

    return total_inserted
