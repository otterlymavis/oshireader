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


@app.get("/api/admin/test-poll")
async def test_poll(db: Session = Depends(get_db)) -> dict:
    """Simulate one poll iteration with full error reporting."""
    from app.ingestion.scheduler import _build_connectors, _search_terms_for
    from app.models import SourceItem as SI, Match, WatchTerm
    results: dict = {"steps": [], "errors": []}
    try:
        connectors = _build_connectors(db)
        results["steps"].append(f"built {len(connectors)} connectors")
    except Exception as e:
        results["errors"].append(f"build_connectors: {e}")
        return results
    try:
        terms = db.query(WatchTerm).filter(WatchTerm.is_active == True).all()  # noqa: E712
        results["steps"].append(f"found {len(terms)} terms")
    except Exception as e:
        results["errors"].append(f"query_terms: {e}")
        return results
    for term in terms:
        for connector in connectors:
            try:
                items = await connector.fetch(term.keyword, term.collection_mode)
                new_count = 0
                for raw in items:
                    if not db.get(SI, raw.composite_id):
                        db.add(SI(id=raw.composite_id, platform=raw.platform, item_id=raw.item_id,
                                  url=raw.url, published_at=raw.published_at, media_type=raw.media_type,
                                  title=raw.title, content_text=raw.content_text,
                                  thumbnail_url=raw.thumbnail_url, raw_payload=raw.raw_payload))
                    if not db.query(Match).filter_by(watch_term_id=term.id, source_item_id=raw.composite_id).first():
                        db.add(Match(watch_term_id=term.id, source_item_id=raw.composite_id))
                        new_count += 1
                db.commit()
                if items:
                    results["steps"].append(f"{connector.PLATFORM}/{term.keyword}: {len(items)} fetched, {new_count} new")
            except Exception as e:
                db.rollback()
                results["errors"].append(f"{connector.PLATFORM}/{term.keyword}: {e}")
    results["items_total"] = db.query(func.count(SI.id)).scalar()
    results["matches_total"] = db.query(func.count(Match.id)).scalar()
    return results


@app.get("/api/admin/test-fetch")
async def test_fetch(db: Session = Depends(get_db)) -> dict:
    from app.connectors.togetter import TogetterConnector
    from app.models import SourceItem as SI, Match, WatchTerm
    results: dict = {}
    kw = "星野源"
    # 1. Fetch
    try:
        items = await TogetterConnector().fetch(kw, "all_info")
        results["fetched"] = len(items)
    except Exception as e:
        results["fetch_error"] = str(e)
        return results
    if not items:
        return results
    raw = items[0]
    results["item_id"] = raw.composite_id
    # 2. Ensure SourceItem exists
    try:
        if not db.get(SI, raw.composite_id):
            db.add(SI(id=raw.composite_id, platform=raw.platform, item_id=raw.item_id,
                      url=raw.url, published_at=raw.published_at, media_type=raw.media_type,
                      title=raw.title, content_text=raw.content_text))
            db.commit()
            results["source_item_write"] = "created"
        else:
            results["source_item_write"] = "already_existed"
    except Exception as e:
        db.rollback()
        results["source_item_error"] = str(e)
        return results
    # 3. Try to create a Match record
    try:
        term = db.query(WatchTerm).filter_by(keyword=kw).first()
        results["term_id"] = term.id if term else None
        if term:
            match_exists = db.query(Match).filter_by(watch_term_id=term.id, source_item_id=raw.composite_id).first()
            if not match_exists:
                db.add(Match(watch_term_id=term.id, source_item_id=raw.composite_id))
                db.commit()
                results["match_write"] = "created"
            else:
                results["match_write"] = "already_existed"
    except Exception as e:
        db.rollback()
        results["match_error"] = str(e)
    results["items_total"] = db.query(func.count(SI.id)).scalar()
    results["matches_total"] = db.query(func.count(Match.id)).scalar()
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
