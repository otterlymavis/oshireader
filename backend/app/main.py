from __future__ import annotations

import asyncio
import feedparser
import httpx
import logging
import os
import struct
import traceback
import zlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import AsyncGenerator, Optional
from urllib.parse import quote, quote_plus

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
from app.connectors.base import (
    GOOGLE_NEWS_HEADERS,
    fetch_search_rss_via_proxy,
    parse_google_news_markdown,
    title_contains_keyword,
)
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

_GNEWS_PROBE_QUERIES = {
    "news": "{keyword} when:10y",
    "aera": "{keyword} site:dot.asahi.com",
    "barks": "{keyword} site:barks.jp",
    "cinemacafe": "{keyword} site:cinemacafe.net",
    "5ch": "{keyword} site:5ch.net OR site:2ch.sc",
    "hochi": "{keyword} site:hochi.news",
    "livedoor": "{keyword} site:news.livedoor.com",
    "mantanweb": "{keyword} site:mantan-web.jp",
    "mdpr": "{keyword} site:mdpr.jp",
    "niconico": "{keyword} site:nicovideo.jp",
    "oricon": "{keyword} site:oricon.co.jp",
    "smartnews": "{keyword} site:smartnews.com",
    "sponichi": "{keyword} site:sponichi.co.jp",
    "twitter": "{keyword} site:x.com OR site:twitter.com",
    "yahoonews": "{keyword} site:news.yahoo.co.jp",
}


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


def _notification_canary_terms(db: Session, *, all_terms: bool = False) -> list[WatchTerm]:
    candidates: list[WatchTerm] = (
        db.query(WatchTerm)
        .filter(WatchTerm.is_active == True)  # noqa: E712
        .filter(WatchTerm.notify_on_new == True)  # noqa: E712
        .order_by(WatchTerm.owner_device_secret.isnot(None), WatchTerm.id)
        .all()
    )
    terms = [term for term in candidates if _term_verified_device_count(db, term) > 0]
    if all_terms:
        return terms
    return terms[:1]


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
    platform: Optional[str] = Query(None),
    samples: int = Query(0, ge=0, le=10),
    timeout_seconds: Optional[float] = Query(None, ge=1, le=60),
) -> dict:
    from app.ingestion.scheduler import _build_connectors, _fetch_one
    from app.relevance import primary_text_matches
    db_sess = SessionLocal()
    try:
        connectors = _build_connectors(db_sess)
        if platform:
            requested = platform.casefold()
            connectors = [c for c in connectors if c.PLATFORM.casefold() == requested]
        counts: dict[str, int] = {}
        details: dict[str, dict] = {}
        for connector_batch in _connector_batches(connectors):
            if timeout_seconds is None:
                results = await asyncio.gather(
                    *[_fetch_one(c, keyword, CollectionMode.ALL_INFO) for c in connector_batch]
                )
            else:
                async def _fetch_with_timeout(connector):
                    try:
                        items = await asyncio.wait_for(
                            connector.fetch(keyword, CollectionMode.ALL_INFO),
                            timeout=timeout_seconds,
                        )
                    except Exception as exc:
                        log.warning(
                            "admin test-fetch error connector=%s term=%r timeout=%ss: %s",
                            connector.PLATFORM,
                            keyword,
                            timeout_seconds,
                            exc,
                        )
                        return []
                    return [item for item in items if primary_text_matches(keyword, item)]

                results = await asyncio.gather(
                    *[_fetch_with_timeout(c) for c in connector_batch]
                )
            for connector, result in zip(connector_batch, results):
                counts[connector.PLATFORM] = len(result)
                if samples:
                    details[connector.PLATFORM] = {
                        "count": len(result),
                        "items": [
                            {
                                "item_id": item.item_id,
                                "url": item.url,
                                "title": item.title,
                                "media_type": item.media_type,
                                "published_at": item.published_at.isoformat(),
                                "raw_payload": item.raw_payload,
                            }
                            for item in result[:samples]
                        ],
                    }
        if samples:
            return details
        return counts
    finally:
        db_sess.close()


@app.get("/api/admin/source-probe")
async def source_probe(
    _: None = Depends(require_admin_auth),
    platform: str = Query(...),
    keyword: str = Query("吉沢亮"),
) -> dict:
    template = _GNEWS_PROBE_QUERIES.get(platform.casefold())
    if not template:
        raise HTTPException(status_code=404, detail="Unsupported source probe platform")

    query = template.format(keyword=keyword)
    encoded = quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
    proxy_url = "https://r.jina.ai/http://" + url.replace("https://", "")
    bing_url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&mkt=ja-JP"

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
        direct_status = None
        direct_len = 0
        direct_entries = 0
        direct_matches = 0
        direct_error = None
        try:
            direct_resp = await client.get(url)
            direct_status = direct_resp.status_code
            direct_len = len(direct_resp.content)
            direct_feed = await asyncio.to_thread(feedparser.parse, direct_resp.content)
            direct_entries = len(direct_feed.entries)
            direct_matches = sum(
                1 for entry in direct_feed.entries
                if title_contains_keyword(keyword, entry.get("title") or "")
            )
        except Exception as exc:
            direct_error = f"{type(exc).__name__}: {exc}"

        jina_status = None
        jina_len = 0
        jina_entries = 0
        jina_matches = 0
        jina_error = None
        try:
            jina_resp = await client.get(proxy_url)
            jina_status = jina_resp.status_code
            jina_len = len(jina_resp.text)
            jina_items = parse_google_news_markdown(jina_resp.text)
            jina_entries = len(jina_items)
            jina_matches = sum(
                1 for item in jina_items
                if title_contains_keyword(keyword, item.get("title") or "")
            )
        except Exception as exc:
            jina_error = f"{type(exc).__name__}: {exc}"

        bing_status = None
        bing_len = 0
        bing_entries = 0
        bing_matches = 0
        bing_error = None
        try:
            bing_resp = await client.get(bing_url)
            bing_status = bing_resp.status_code
            bing_len = len(bing_resp.content)
            bing_feed = await asyncio.to_thread(feedparser.parse, bing_resp.content)
            bing_entries = len(bing_feed.entries)
            bing_matches = sum(
                1 for entry in bing_feed.entries
                if title_contains_keyword(keyword, entry.get("title") or "")
            )
        except Exception as exc:
            bing_error = f"{type(exc).__name__}: {exc}"

        async def probe_worker_proxy(target: str) -> dict:
            result = {
                "bytes": 0,
                "entries": 0,
                "keyword_title_matches": 0,
                "error": None,
            }
            try:
                proxy_content = await fetch_search_rss_via_proxy(query, target=target)
                if proxy_content:
                    result["bytes"] = len(proxy_content)
                    proxy_feed = await asyncio.to_thread(feedparser.parse, proxy_content)
                    result["entries"] = len(proxy_feed.entries)
                    result["keyword_title_matches"] = sum(
                        1 for entry in proxy_feed.entries
                        if title_contains_keyword(keyword, entry.get("title") or "")
                    )
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"
            return result

        worker_google = await probe_worker_proxy("google")
        worker_bing = await probe_worker_proxy("bing")

    return {
        "platform": platform,
        "keyword": keyword,
        "query": query,
        "direct": {
            "status": direct_status,
            "bytes": direct_len,
            "entries": direct_entries,
            "keyword_title_matches": direct_matches,
            "error": direct_error,
        },
        "jina": {
            "status": jina_status,
            "bytes": jina_len,
            "entries": jina_entries,
            "keyword_title_matches": jina_matches,
            "error": jina_error,
        },
        "bing": {
            "status": bing_status,
            "bytes": bing_len,
            "entries": bing_entries,
            "keyword_title_matches": bing_matches,
            "error": bing_error,
        },
        "worker_proxy": worker_google,
        "worker_proxy_bing": worker_bing,
    }


@app.post("/api/admin/test-push")
async def test_push(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)) -> dict:
    from app.apns import send_test_push
    return await send_test_push(db)


@app.post("/api/admin/notification-canary")
async def notification_canary(
    all_terms: bool = Query(False),
    _: None = Depends(require_admin_auth),
    db: Session = Depends(get_db),
) -> dict:
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

    terms = _notification_canary_terms(db, all_terms=all_terms)
    if not terms:
        record_backend_event(
            db,
            "notification_canary",
            "failed",
            "No active notification term has a verified APNs device",
            {},
        )
        db.commit()
        raise HTTPException(503, "No active notification term has a verified APNs device")

    term_results = []
    all_delivered = True
    for term in terms:
        delivered = await send_new_match_notifications(db, term, 1, _notification_canary_preview())
        apns_event = _latest_relevant_apns_event(db)
        all_delivered = all_delivered and delivered
        term_results.append({
            "term_id": term.id,
            "keyword": term.keyword,
            "owner_scoped": bool(term.owner_device_secret),
            "delivered": delivered,
            "apns_event": _jsonable_backend_event_payload(apns_event) if apns_event else None,
        })

    first_result = term_results[0]
    payload = {
        **first_result,
        "all_terms": all_terms,
        "delivered": all_delivered,
        "terms": term_results,
    }
    record_backend_event(
        db,
        "notification_canary",
        "passed" if all_delivered else "failed",
        "Synthetic notification canary completed",
        payload,
    )
    db.commit()
    if not all_delivered:
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
