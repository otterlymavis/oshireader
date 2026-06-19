import asyncio
import logging
import os
import struct
import zlib
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import credentials, devices, feed, watch_terms
from app.auth import require_admin_auth
from app.config import settings
from app.database import engine, get_db, SessionLocal
from app.ingestion.scheduler import _poll_lock, poll_once, queue_poll, scheduler, start_scheduler
from app.migrations import apply_startup_migrations
from app.models import BackendEvent, CollectionMode, Match, SourceItem, WatchTerm
from app.schemas import ClientDiagnosticIn

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    if not settings.admin_api_token:
        if settings.allow_unauthenticated_admin:
            log.warning(
                "ADMIN_API_TOKEN is not set and ALLOW_UNAUTHENTICATED_ADMIN=true — "
                "admin endpoints are unauthenticated. Use this only for local development."
            )
        else:
            raise RuntimeError(
                "ADMIN_API_TOKEN must be set. For local development only, set "
                "ALLOW_UNAUTHENTICATED_ADMIN=true."
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
    payload = {"status": "ok"}
    if commit := os.getenv("RENDER_GIT_COMMIT"):
        payload["commit"] = commit
    return payload


@app.get("/api/notification-preview.png")
def notification_preview_image() -> Response:
    return Response(
        content=_notification_preview_png(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@lru_cache(maxsize=1)
def _notification_preview_png() -> bytes:
    width, height = 640, 360
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            glow = max(0, 110 - int(((x - 470) ** 2 + (y - 90) ** 2) ** 0.5))
            rows.extend((
                min(255, 25 + x * 55 // width + glow),
                min(255, 20 + y * 35 // height + glow // 3),
                min(255, 52 + x * 75 // width + glow),
            ))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + chunk(b"IEND", b"")
    )


@app.post("/api/client-diagnostics")
def client_diagnostics(report: ClientDiagnosticIn) -> dict:
    failed_events = [event for event in report.events if event.status not in {"ok", "items"}]
    log.warning(
        "client diagnostic reason=%s env=%s terms=%d cached=%d platforms=%s failed_events=%d events=%s",
        report.reason,
        report.environment,
        report.active_terms_count,
        report.cached_feed_count,
        ",".join(report.subscribed_platforms),
        len(failed_events),
        [event.model_dump() for event in report.events],
    )
    return {"status": "received"}


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
    recent_events = db.query(BackendEvent).order_by(
        BackendEvent.created_at.desc(),
        BackendEvent.id.desc(),
    ).limit(20).all()
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
            "backend_public_url": settings.backend_public_url,
            "device_tokens_by_environment": {env: c for env, c in device_tokens},
        },
        "recent_events": [
            {
                "id": event.id,
                "kind": event.kind,
                "status": event.status,
                "message": event.message,
                "payload": event.payload or {},
                "created_at": event.created_at,
            }
            for event in recent_events
        ],
    }
