import secrets
import string
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import cache_delete, cache_get, cache_set
from app.config import settings
from app.db import Base, engine, get_db
from app.models import ClickEvent, Link
from app.schemas import LinkCreate, LinkOut, LinkStats

ALPHABET = string.ascii_letters + string.digits


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DEV ONLY: create tables on boot. Prod uses migrations (Alembic).
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Snip", version="0.1.0", lifespan=lifespan)


def generate_code(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/links", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
def create_link(payload: LinkCreate, db: Session = Depends(get_db)):
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

    return LinkStats(code=link.code, clicks=link.clicks, recent_clicks=list(recent))


@app.delete("/links/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_link(code: str, db: Session = Depends(get_db)):
    link = db.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")

    db.delete(link)
    db.commit()
    cache_delete(code)
    return None


@app.get("/{code}")
def redirect(code: str, db: Session = Depends(get_db)):
    link = db.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")

    url = cache_get(code)
    if url is None:
        url = link.target_url
        cache_set(code, url)

    link.clicks += 1
    db.add(ClickEvent(link_id=link.id))
    db.commit()

    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
