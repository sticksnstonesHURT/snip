import secrets
import string
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import cache_delete, cache_get, cache_set
from app.config import settings
from app.db import get_db
from app.auth import require_api_key
from app.models import ApiKey, ClickEvent, Link
from app.schemas import LinkCreate, LinkOut, LinkStats
from app.clicks import flush_pending, pending_count, record_click

ALPHABET = string.ascii_letters + string.digits

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)
_stop_flusher = threading.Event()

def _flush_loop() -> None:
    while not _stop_flusher.wait(settings.flush_interval_seconds):
        try:
            flushed = flush_pending()
            if flushed:
                logger.info("flushed %d clicks", flushed)
        except Exception:
            logger.exception("flush loop error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _stop_flusher.clear()
    flusher = threading.Thread(target=_flush_loop, name="click-flusher", daemon=True)
    flusher.start()
    yield
    logger.info("shutting down: flushing clicks")
    _stop_flusher.set()
    flusher.join(timeout=5)
    try:
        flushed = flush_pending()
        logger.info("final flush: %d clicks", flushed)
    except Exception:
        logger.exception("final flush failed")


app = FastAPI(title="Snip", version="0.1.0", lifespan=lifespan)


def generate_code(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/links", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
def create_link(
    payload: LinkCreate,
    api_key: ApiKey = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    target = str(payload.url)

    for _ in range(5):
        code = generate_code(settings.code_length)
        if db.scalar(select(Link).where(Link.code == code)) is None:
            break
    else:
        raise HTTPException(
            status_code=500, detail="could not generate a unique code"
        )

    link = Link(code=code, target_url=target)
    db.add(link)
    db.commit()
    db.refresh(link)
    cache_set(code, target)
    return link


@app.get("/links", response_model=list[LinkOut])
def list_links(db: Session = Depends(get_db)):
    return db.scalars(select(Link).order_by(Link.created_at.desc())).all()


@app.get("/links/{code}/stats", response_model=LinkStats)
def link_stats(code: str, db: Session = Depends(get_db)):
    link = db.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")

    recent = db.scalars(
        select(ClickEvent.clicked_at)
        .where(ClickEvent.link_id == link.id)
        .order_by(ClickEvent.clicked_at.desc())
        .limit(20)
    ).all()

    return LinkStats(
        code=link.code,
        clicks=link.clicks,
        pending_clicks=pending_count(code),
        recent_clicks=list(recent),
    )


@app.delete("/links/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_link(
    code: str,
    api_key: ApiKey = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    link = db.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")

    db.delete(link)
    db.commit()
    cache_delete(code)
    return None


@app.get("/{code}")
def redirect(code: str, db: Session = Depends(get_db)):
    url = cache_get(code)
    if url is None:
        link = db.scalar(select(Link).where(Link.code == code))
        if link is None:
            raise HTTPException(status_code=404, detail="link not found")
        url = link.target_url
        cache_set(code, url)

    record_click(code)
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
