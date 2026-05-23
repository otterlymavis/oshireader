import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api import credentials, feed, watch_terms
from app.api import twitter as twitter_api
from app.connectors.twitter import init_twitter_api
from app.database import Base, SessionLocal, engine, get_db
from app.ingestion.scheduler import poll_once, scheduler, start_scheduler
from app.models import Match, PlatformCredential, SourceItem, WatchTerm

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

log = logging.getLogger(__name__)


def _run_migrations() -> None:
    """Idempotently add new columns introduced after the initial schema."""
    with engine.connect() as conn:
        # Detect dialect
        dialect = engine.dialect.name

        if dialect == "postgresql":
            stmts = [
                "ALTER TABLE platform_credentials ADD COLUMN IF NOT EXISTS username VARCHAR",
                "ALTER TABLE platform_credentials ADD COLUMN IF NOT EXISTS password VARCHAR",
                "ALTER TABLE platform_credentials ADD COLUMN IF NOT EXISTS email VARCHAR",
            ]
        else:
            # SQLite does not support IF NOT EXISTS on ALTER TABLE;
            # check via PRAGMA before adding.
            cols_result = conn.execute(text("PRAGMA table_info(platform_credentials)"))
            existing = {row[1] for row in cols_result}
            stmts = []
            for col in ("username", "password", "email"):
                if col not in existing:
                    stmts.append(f"ALTER TABLE platform_credentials ADD COLUMN {col} VARCHAR")

        for stmt in stmts:
            try:
                conn.execute(text(stmt))
            except Exception as exc:
                log.debug("Migration stmt skipped (%s): %s", exc, stmt)

        conn.commit()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    Base.metadata.create_all(bind=engine)
    _run_migrations()

    # Restore Twitter session from stored credentials (non-blocking)
    db = SessionLocal()
    try:
        cred = db.get(PlatformCredential, "twitter")
        if cred and cred.username and cred.password:
            log.info("Restoring Twitter session for @%s …", cred.username)
            asyncio.create_task(
                init_twitter_api(
                    username=cred.username,
                    password=cred.password,
                    email=cred.email or "",
                )
            )
    finally:
        db.close()

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
app.include_router(twitter_api.router)


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
