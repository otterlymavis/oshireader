import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import credentials, devices, feed, watch_terms
from app.auth import require_admin_auth
from app.config import settings
from app.database import engine, get_db, SessionLocal
from app.ingestion.scheduler import _poll_lock, poll_once, queue_poll, scheduler, start_scheduler
from app.migrations import apply_startup_migrations
from app.models import CollectionMode, Match, SourceItem, WatchTerm

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    if not settings.admin_api_token:
        log.warning(
            "ADMIN_API_TOKEN is not set — all admin endpoints are unauthenticated. "
            "Set this env var before exposing the server to the internet."
        )
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


@app.post("/api/admin/poll")
async def trigger_poll(_: None = Depends(require_admin_auth)) -> dict:
    # Run the poll synchronously (awaited) rather than as a fire-and-forget
    # background task. On Render's free tier the instance is suspended shortly
    # after the HTTP response is sent, which would kill a backgrounded poll
    # mid-flight before most connectors finish. Holding the request open keeps
    # the instance alive for the duration. The scheduler commits per-connector,
    # so even if we hit the timeout, completed connectors are already persisted.
    if _poll_lock.locked():
        return {"status": "poll already running"}
    try:
        await asyncio.wait_for(poll_once(), timeout=80.0)
        return {"status": "poll completed"}
    except asyncio.TimeoutError:
        return {"status": "poll timed out (partial progress saved)"}


@app.get("/api/admin/test-fetch")
async def test_fetch(
    _: None = Depends(require_admin_auth),
    keyword: str = Query("吉沢亮"),
) -> dict:
    from app.ingestion.scheduler import _build_connectors, _fetch_one
    db_sess = SessionLocal()
    try:
        connectors = _build_connectors(db_sess)
        results = await asyncio.gather(
            *[_fetch_one(c, keyword, CollectionMode.ALL_INFO) for c in connectors]
        )
        return {c.PLATFORM: len(r) for c, r in zip(connectors, results)}
    finally:
        db_sess.close()


@app.get("/api/admin/probe")
async def probe_urls(_: None = Depends(require_admin_auth)) -> dict:
    """Diagnostic: fetch representative source URLs from the server's own network
    so we can see exactly what the Render datacenter IP can/can't reach (some
    sources block datacenter IPs and return empty feeds)."""
    import feedparser
    import httpx

    targets = {
        "google_news": "https://news.google.com/rss/search?q=%E5%90%89%E6%B2%A2%E4%BA%AE&hl=ja&gl=JP&ceid=JP:ja",
        "niconico_rss": "https://www.nicovideo.jp/tag/%E5%90%89%E6%B2%A2%E4%BA%AE?sort=f&order=d&rss=2.0",
        "yahoo_news": "https://news.yahoo.co.jp/rss/topics/entertainment.xml",
        "mdpr_rss": "https://mdpr.jp/rss",
        "oricon_rss": "https://www.oricon.co.jp/rss/news/rss.xml",
        "barks_rss": "https://www.barks.jp/news/rss/",
        "girlschannel": "https://girlschannel.net/topics/search/?q=%E5%90%89%E6%B2%A2%E4%BA%AE",
    }
    out: dict = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
        for name, url in targets.items():
            try:
                resp = await client.get(url)
                entries = len(feedparser.parse(resp.content).entries)
                out[name] = {"status": resp.status_code, "bytes": len(resp.content), "feed_entries": entries}
            except Exception as exc:
                out[name] = {"error": f"{type(exc).__name__}: {exc}"}
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
