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
async def test_fetch() -> dict:
    import httpx, feedparser
    from urllib.parse import quote
    results: dict = {}
    kw = "星野源"
    enc = quote(f"{kw} site:mdpr.jp")
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"https://news.google.com/rss/search?q={enc}&hl=ja&gl=JP&ceid=JP%3Aja")
            results["gnews_status"] = r.status_code
            results["gnews_entries"] = len(feedparser.parse(r.content).entries) if r.is_success else 0
    except Exception as e:
        results["gnews_error"] = str(e)
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get("https://togetter.com/search", params={"q": kw})
            results["togetter_status"] = r.status_code
            results["togetter_len"] = len(r.text)
    except Exception as e:
        results["togetter_error"] = str(e)
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get("https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search",
                            params={"q": kw, "targets": "title", "fields": "contentId", "_limit": "5"},
                            headers={"Accept": "application/json"})
            results["nico_status"] = r.status_code
            results["nico_count"] = len(r.json().get("data", [])) if r.is_success else 0
    except Exception as e:
        results["nico_error"] = str(e)
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
