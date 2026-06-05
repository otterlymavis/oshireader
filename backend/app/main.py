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


@app.post("/api/admin/poll")
async def trigger_poll(_: None = Depends(require_admin_auth)) -> dict:
    started = queue_poll()
    if not started:
        return {"status": "poll already running"}
    return {"status": "poll started"}




@app.get("/api/admin/test-fetch")
async def test_fetch(_: None = Depends(require_admin_auth)) -> dict:
    """Run one keyword through every connector and report item counts + errors."""
    import asyncio
    from app.ingestion.scheduler import _build_connectors, _fetch_one
    db_sess = __import__("app.database", fromlist=["SessionLocal"]).SessionLocal()
    try:
        connectors = _build_connectors(db_sess)
        keyword = "吉沢亮"
        results = await asyncio.gather(
            *[_fetch_one(c, keyword, "all_info") for c in connectors]
        )
        return {c.PLATFORM: len(r) for c, r in zip(connectors, results)}
    finally:
        db_sess.close()


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
