from __future__ import annotations

import asyncio
import logging
import os
import struct
import traceback
import zlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import credentials, devices, feed, watch_terms
from app.auth import require_admin_auth
from app.config import settings
from app.database import engine, get_db, SessionLocal
from app.diagnostics import record_backend_event
from app.ingestion.scheduler import (
    _connector_batches,
    _poll_lock,
    create_poll_task,
    poll_once,
    queue_poll,
    scheduler,
    start_scheduler,
)
from app.migrations import apply_startup_migrations
from app.models import APNSDeviceToken, BackendEvent, CollectionMode, Match, PendingNotification, SourceItem, WatchTerm
from app.schemas import ClientDiagnosticIn

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

log = logging.getLogger(__name__)
_ADMIN_POLL_TIMEOUT_SECONDS = 210.0


def _backend_event_payload(event: BackendEvent) -> dict:
    return {
        "id": event.id,
        "kind": event.kind,
        "status": event.status,
        "message": event.message,
        "payload": event.payload or {},
        "created_at": event.created_at,
    }


def _jsonable_backend_event_payload(event: BackendEvent) -> dict:
    payload = _backend_event_payload(event)
    created_at = payload.get("created_at")
    if hasattr(created_at, "isoformat"):
        payload["created_at"] = created_at.isoformat()
    return payload


def _latest_relevant_apns_event(db: Session) -> BackendEvent | None:
    """Return the newest APNs event that still describes an active notification path."""
    events = (
        db.query(BackendEvent)
        .filter(BackendEvent.kind == "apns")
        .order_by(BackendEvent.created_at.desc(), BackendEvent.id.desc())
        .limit(50)
        .all()
    )
    if not events:
        return None

    term_ids = {
        event.payload.get("term_id")
        for event in events
        if isinstance(event.payload, dict) and event.payload.get("term_id") is not None
    }
    active_term_ids = set()
    if term_ids:
        active_term_ids = {
            term_id
            for (term_id,) in (
                db.query(WatchTerm.id)
                .filter(
                    WatchTerm.id.in_(term_ids),
                    WatchTerm.is_active == True,  # noqa: E712
                )
                .all()
            )
        }

    for event in events:
        term_id = event.payload.get("term_id") if isinstance(event.payload, dict) else None
        if term_id is None or term_id in active_term_ids:
            return event
    return None


def _term_verified_device_count(db: Session, term: WatchTerm) -> int:
    query = db.query(APNSDeviceToken).filter(APNSDeviceToken.is_verified == True)  # noqa: E712
    if term.owner_device_secret:
        query = query.filter(APNSDeviceToken.device_secret == term.owner_device_secret)
    return query.count()


def _notification_canary_preview() -> dict:
    now = datetime.now(timezone.utc)
    backend_url = settings.backend_public_url.rstrip("/")
    return {
        "id": f"oshireader:canary:{int(now.timestamp())}",
        "platform": "OshiReader",
        "url": backend_url,
        "title": "OshiReader notification canary",
        "content_text": "Synthetic new-feed notification canary.",
        "author": "OshiReader",
        "thumbnail_url": f"{backend_url}/api/notification-preview.png",
        "media_type": "article",
        "published_at": now.isoformat(),
    }


def _notification_canary_term(db: Session) -> WatchTerm | None:
    candidates = (
        db.query(WatchTerm)
        .filter(WatchTerm.is_active == True)  # noqa: E712
        .filter(WatchTerm.notify_on_new == True)  # noqa: E712
        .order_by(WatchTerm.owner_device_secret.isnot(None), WatchTerm.id)
        .all()
    )
    return next((term for term in candidates if _term_verified_device_count(db, term) > 0), None)


def _record_poll_request_timeout(timeout_seconds: float) -> None:
    db_sess = SessionLocal()
    try:
        record_backend_event(
            db_sess,
            "poll",
            "running_past_request_timeout",
            "Scheduled/backend poll exceeded the request budget",
            {"timeout_seconds": timeout_seconds},
        )
        db_sess.commit()
    finally:
        db_sess.close()


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


@app.head("/api/health")
def health_head() -> Response:
    return Response(status_code=200)


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
def client_diagnostics(report: ClientDiagnosticIn, db: Session = Depends(get_db)) -> dict:
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
    record_backend_event(
        db,
        "client_diagnostic",
        "reported",
        "Client diagnostic reported",
        {
            "reason": report.reason,
            "environment": report.environment,
            "api_base": report.api_base,
            "app_version": report.app_version,
            "build": report.build,
            "active_terms_count": report.active_terms_count,
            "subscribed_platforms": report.subscribed_platforms,
            "cached_feed_count": report.cached_feed_count,
            "events": [event.model_dump() for event in report.events],
        },
    )
    db.commit()
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
    poll_task = create_poll_task()
    try:
        await asyncio.wait_for(
            asyncio.shield(poll_task),
            timeout=_ADMIN_POLL_TIMEOUT_SECONDS,
        )
        return {"status": "poll completed"}
    except asyncio.TimeoutError:
        _record_poll_request_timeout(_ADMIN_POLL_TIMEOUT_SECONDS)
        return {"status": "poll still running (request timed out)"}
    except Exception as exc:
        trace = traceback.format_exc(limit=8)
        log.exception("Admin poll failed")
        db_sess = SessionLocal()
        try:
            record_backend_event(
                db_sess,
                "poll",
                "failed",
                "Scheduled/backend poll failed",
                {"error": str(exc), "traceback": trace},
            )
            db_sess.commit()
        finally:
            db_sess.close()
        raise HTTPException(
            status_code=500,
            detail={"status": "poll failed", "error": str(exc), "traceback": trace},
        )


@app.get("/api/admin/test-fetch")
async def test_fetch(
    _: None = Depends(require_admin_auth),
    keyword: str = Query("吉沢亮"),
) -> dict:
    from app.ingestion.scheduler import _build_connectors, _fetch_one
    db_sess = SessionLocal()
    try:
        connectors = _build_connectors(db_sess)
        counts: dict[str, int] = {}
        for connector_batch in _connector_batches(connectors):
            results = await asyncio.gather(
                *[_fetch_one(c, keyword, CollectionMode.ALL_INFO) for c in connector_batch]
            )
            counts.update({
                connector.PLATFORM: len(result)
                for connector, result in zip(connector_batch, results)
            })
        return counts
    finally:
        db_sess.close()


@app.post("/api/admin/test-push")
async def test_push(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)) -> dict:
    from app.apns import send_test_push
    return await send_test_push(db)


@app.post("/api/admin/notification-canary")
async def notification_canary(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)) -> dict:
    from app.apns import apns_configured, send_new_match_notifications

    if not apns_configured():
        record_backend_event(
            db,
            "notification_canary",
            "failed",
            "APNs is not configured",
            {},
        )
        db.commit()
        raise HTTPException(503, "APNs is not configured")

    term = _notification_canary_term(db)
    if term is None:
        record_backend_event(
            db,
            "notification_canary",
            "failed",
            "No active notification term has a verified APNs device",
            {},
        )
        db.commit()
        raise HTTPException(503, "No active notification term has a verified APNs device")

    delivered = await send_new_match_notifications(db, term, 1, _notification_canary_preview())
    apns_event = _latest_relevant_apns_event(db)
    payload = {
        "term_id": term.id,
        "keyword": term.keyword,
        "delivered": delivered,
        "apns_event": _jsonable_backend_event_payload(apns_event) if apns_event else None,
    }
    record_backend_event(
        db,
        "notification_canary",
        "passed" if delivered else "failed",
        "Synthetic notification canary completed",
        payload,
    )
    db.commit()
    if not delivered:
        raise HTTPException(503, payload)
    return payload


@app.get("/api/admin/stats")
def get_stats(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)) -> dict:
    from app.apns import apns_configured

    items_total = db.query(func.count(SourceItem.id)).scalar()
    matches_total = db.query(func.count(Match.id)).scalar()
    terms = db.query(WatchTerm).all()
    by_platform = db.query(SourceItem.platform, func.count(SourceItem.id)).group_by(SourceItem.platform).all()
    device_tokens = db.query(APNSDeviceToken.environment, func.count(APNSDeviceToken.token)).group_by(
        APNSDeviceToken.environment
    ).all()
    verified_device_tokens = (
        db.query(APNSDeviceToken.environment, APNSDeviceToken.is_verified, func.count(APNSDeviceToken.token))
        .group_by(APNSDeviceToken.environment, APNSDeviceToken.is_verified)
        .all()
    )
    recent_events = db.query(BackendEvent).order_by(
        BackendEvent.created_at.desc(),
        BackendEvent.id.desc(),
    ).limit(20).all()
    latest_poll = (
        db.query(BackendEvent)
        .filter(BackendEvent.kind == "poll")
        .order_by(BackendEvent.created_at.desc(), BackendEvent.id.desc())
        .first()
    )
    latest_successful_poll = (
        db.query(BackendEvent)
        .filter(
            BackendEvent.kind == "poll",
            BackendEvent.status.in_(["completed", "completed_with_errors"]),
        )
        .order_by(BackendEvent.created_at.desc(), BackendEvent.id.desc())
        .first()
    )
    latest_apns = (
        db.query(BackendEvent)
        .filter(BackendEvent.kind == "apns")
        .order_by(BackendEvent.created_at.desc(), BackendEvent.id.desc())
        .first()
    )
    latest_relevant_apns = _latest_relevant_apns_event(db)
    pending_notifications = (
        db.query(PendingNotification, WatchTerm)
        .join(WatchTerm, PendingNotification.watch_term_id == WatchTerm.id)
        .order_by(PendingNotification.updated_at.desc())
        .limit(20)
        .all()
    )

    def notification_device_counts(term: WatchTerm) -> dict:
        query = db.query(APNSDeviceToken)
        if term.owner_device_secret:
            query = query.filter(APNSDeviceToken.device_secret == term.owner_device_secret)
        total = query.count()
        verified = query.filter(APNSDeviceToken.is_verified == True).count()  # noqa: E712
        return {
            "owner_scoped": bool(term.owner_device_secret),
            "notification_devices": total,
            "notification_verified_devices": verified,
        }

    watch_term_rows: list[dict] = []
    active_silent_orphans: list[dict] = []
    active_notify_without_verified_devices: list[dict] = []
    active_notify_terms = 0
    orphaned_grace_minutes = max(0, settings.orphaned_notification_grace_minutes)
    orphaned_cutoff = datetime.now(timezone.utc) - timedelta(minutes=orphaned_grace_minutes)

    def is_past_owner_grace(term: WatchTerm) -> bool:
        if not term.owner_device_secret:
            return True
        if not term.created_at:
            return True
        created_at = term.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at <= orphaned_cutoff

    for term in terms:
        device_counts = notification_device_counts(term)
        row = {
            "id": term.id,
            "keyword": term.keyword,
            "is_active": term.is_active,
            "notify_on_new": term.notify_on_new,
            **device_counts,
        }
        watch_term_rows.append(row)
        if not term.is_active:
            continue
        if term.notify_on_new:
            active_notify_terms += 1
            if (
                device_counts["notification_verified_devices"] == 0
                and is_past_owner_grace(term)
            ):
                active_notify_without_verified_devices.append(row)
        elif (
            term.owner_device_secret
            and device_counts["notification_devices"] == 0
            and is_past_owner_grace(term)
        ):
            active_silent_orphans.append(row)

    return {
        "items_total": items_total,
        "matches_total": matches_total,
        "watch_terms": watch_term_rows,
        "notification_health": {
            "healthy": not active_silent_orphans and not active_notify_without_verified_devices,
            "active_notify_terms": active_notify_terms,
            "active_silent_orphan_terms": len(active_silent_orphans),
            "active_notify_terms_without_verified_devices": len(active_notify_without_verified_devices),
            "orphaned_notification_grace_minutes": orphaned_grace_minutes,
            "active_silent_orphan_term_ids": [term["id"] for term in active_silent_orphans[:20]],
            "active_notify_term_ids_without_verified_devices": [
                term["id"] for term in active_notify_without_verified_devices[:20]
            ],
        },
        "items_by_platform": {p: c for p, c in by_platform},
        "apns": {
            "configured": apns_configured(),
            "server_environment": "sandbox" if settings.apns_use_sandbox else "production",
            "backend_public_url": settings.backend_public_url,
            "device_tokens_by_environment": {env: c for env, c in device_tokens},
            "device_tokens_by_environment_and_verification": {
                env: {
                    "verified": sum(
                        c for row_env, is_verified, c in verified_device_tokens
                        if row_env == env and is_verified is True
                    ),
                    "unverified": sum(
                        c for row_env, is_verified, c in verified_device_tokens
                        if row_env == env and is_verified is not True
                    ),
                }
                for env, _ in device_tokens
            },
        },
        "latest_poll": _backend_event_payload(latest_poll) if latest_poll else None,
        "latest_successful_poll": (
            _backend_event_payload(latest_successful_poll)
            if latest_successful_poll else None
        ),
        "latest_apns": _backend_event_payload(latest_apns) if latest_apns else None,
        "latest_relevant_apns": (
            _backend_event_payload(latest_relevant_apns)
            if latest_relevant_apns else None
        ),
        "pending_notifications": [
            {
                "watch_term_id": pending.watch_term_id,
                "keyword": term.keyword,
                "new_count": pending.new_count,
                "updated_at": pending.updated_at,
                "notify_on_new": term.notify_on_new,
                "owner_scoped": bool(term.owner_device_secret),
            }
            for pending, term in pending_notifications
        ],
        "recent_events": [
            _backend_event_payload(event)
            for event in recent_events
        ],
    }
