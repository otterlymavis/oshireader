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


@app.get("/api/admin/test-fetch")
async def test_fetch(db: Session = Depends(get_db)) -> dict:
    from datetime import datetime, timezone
    from app.connectors.togetter import TogetterConnector
    from app.models import SourceItem as SI, Match, WatchTerm
    results: dict = {}
    # 1. Fetch from togetter
    try:
        items = await TogetterConnector().fetch("星野源", "all_info")
        results["togetter_fetched"] = len(items)
    except Exception as e:
        results["togetter_fetch_error"] = str(e)
        return results
    # 2. Try to write first item to DB
    if items:
        raw = items[0]
        results["item_id"] = raw.composite_id
        try:
            existing = db.get(SI, raw.composite_id)
            results["already_exists"] = existing is not None
            if not existing:
                db.add(SI(
                    id=raw.composite_id,
                    platform=raw.platform,
                    item_id=raw.item_id,
                    url=raw.url,
                    published_at=raw.published_at,
                    media_type=raw.media_type,
                    title=raw.title,
                    content_text=raw.content_text,
                    author=raw.author,
                    thumbnail_url=raw.thumbnail_url,
                ))
                db.commit()
                results["write"] = "ok"
        except Exception as e:
            db.rollback()
            results["write_error"] = str(e)
    # 3. Check counts
    results["db_items_total"] = db.query(func.count(SI.id)).scalar()
    return results


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
