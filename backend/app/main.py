import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import credentials, devices, feed, watch_terms
from app.auth import require_admin_auth
from app.config import settings
from app.database import engine, get_db
from app.ingestion.scheduler import queue_poll, scheduler, start_scheduler
from app.migrations import apply_startup_migrations
from app.models import Match, SourceItem, WatchTerm

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    apply_startup_migrations(engine)
    start_scheduler()
    queue_poll()
    yield
    scheduler.shutdown()


app = FastAPI(title="Otterpia", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(watch_terms.router)
app.include_router(feed.router)
app.include_router(credentials.router)
app.include_router(devices.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/admin/poll")
@app.post("/api/admin/poll")
async def trigger_poll(_: None = Depends(require_admin_auth)) -> dict:
    started = queue_poll()
    if not started:
        return {"status": "poll already running"}
    return {"status": "poll started"}




@app.get("/api/admin/debug-poll")
async def debug_poll(db: Session = Depends(get_db)) -> dict:
    from app.ingestion.scheduler import _build_connectors, _search_terms_for
    from app.models import SourceItem as SI, Match, WatchTerm
    out: dict = {}
    try:
        connectors = _build_connectors(db)
        out["connectors"] = len(connectors)
    except Exception as e:
        out["build_error"] = str(e)
        return out
    try:
        terms = db.query(WatchTerm).filter(WatchTerm.is_active == True).all()  # noqa: E712
        out["terms"] = [t.keyword for t in terms]
    except Exception as e:
        out["terms_error"] = str(e)
        return out
    if not terms:
        out["note"] = "no active terms"
        return out
    term = terms[0]
    connector = next((c for c in connectors if c.PLATFORM == "togetter"), None)
    if not connector:
        out["note"] = "togetter not found"
        return out
    try:
        items = await connector.fetch(term.keyword, term.collection_mode)
        out["fetched"] = len(items)
    except Exception as e:
        out["fetch_error"] = str(e)
        return out
    added = 0
    for raw in items[:3]:
        try:
            if not db.get(SI, raw.composite_id):
                db.add(SI(id=raw.composite_id, platform=raw.platform, item_id=raw.item_id,
                          url=raw.url, published_at=raw.published_at, media_type=raw.media_type,
                          title=raw.title, content_text=raw.content_text, raw_payload=raw.raw_payload))
                db.flush()
            if not db.query(Match).filter_by(watch_term_id=term.id, source_item_id=raw.composite_id).first():
                db.add(Match(watch_term_id=term.id, source_item_id=raw.composite_id))
                added += 1
        except Exception as e:
            db.rollback()
            out.setdefault("item_errors", []).append(str(e)[:200])
    try:
        db.commit()
        out["committed"] = added
    except Exception as e:
        db.rollback()
        out["commit_error"] = str(e)[:300]
    out["items_total"] = db.query(func.count(SI.id)).scalar()
    out["matches_total"] = db.query(func.count(Match.id)).scalar()
    return out


@app.get("/api/admin/stats")
def get_stats(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)) -> dict:
    items_total = db.query(func.count(SourceItem.id)).scalar()
    matches_total = db.query(func.count(Match.id)).scalar()
    terms = db.query(WatchTerm).all()
    by_platform = db.query(SourceItem.platform, func.count(SourceItem.id)).group_by(SourceItem.platform).all()
    return {
        "items_total": items_total,
        "matches_total": matches_total,
        "watch_terms": [{"id": t.id, "keyword": t.keyword, "is_active": t.is_active} for t in terms],
        "items_by_platform": {p: c for p, c in by_platform},
    }
