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


@app.post("/api/admin/test-push")
async def test_push(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)) -> dict:
    from app.apns import send_test_push
    return await send_test_push(db)


@app.get("/api/admin/stats")
def get_stats(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)) -> dict:
    from app.apns import apns_configured
    from app.models import APNSDeviceToken

    items_total = db.query(func.count(SourceItem.id)).scalar()
    matches_total = db.query(func.count(Match.id)).scalar()
    terms = db.query(WatchTerm).all()
    by_platform = db.query(SourceItem.platform, func.count(SourceItem.id)).group_by(SourceItem.platform).all()
    device_tokens = db.query(APNSDeviceToken.environment, func.count(APNSDeviceToken.token)).group_by(
        APNSDeviceToken.environment
    ).all()
    return {
        "items_total": items_total,
        "matches_total": matches_total,
        "watch_terms": [
            {"id": t.id, "keyword": t.keyword, "is_active": t.is_active, "notify_on_new": t.notify_on_new}
            for t in terms
        ],
        "items_by_platform": {p: c for p, c in by_platform},
        "apns": {
            "configured": apns_configured(),
            "server_environment": "sandbox" if settings.apns_use_sandbox else "production",
            "device_tokens_by_environment": {env: c for env, c in device_tokens},
        },
    }
