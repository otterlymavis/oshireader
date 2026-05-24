import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import credentials, feed, push, watch_terms
from app.database import Base, SessionLocal, engine, get_db
from app.ingestion.scheduler import poll_once, scheduler, start_scheduler
from app.models import Match, SourceItem, WatchTerm

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    Base.metadata.create_all(bind=engine)
    start_scheduler()
    asyncio.create_task(poll_once())
    yield
    scheduler.shutdown()


app = FastAPI(title="Otterpia", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(watch_terms.router)
app.include_router(feed.router)
app.include_router(credentials.router)
app.include_router(push.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/admin/poll")
@app.post("/api/admin/poll")
async def trigger_poll() -> dict:
    asyncio.create_task(poll_once())
    return {"status": "poll started"}


@app.get("/api/admin/stats")
def get_stats(db: Session = Depends(get_db)) -> dict:
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
