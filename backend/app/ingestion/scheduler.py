from __future__ import annotations

import logging
import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.apns import send_new_match_notifications
from app.config import settings
from app.connectors.base import BaseConnector
from app.connectors.fivech import FiveChConnector
from app.connectors.girlschannel import GirlsChannelConnector
from app.connectors.mdpr import ModelPressConnector
from app.connectors.niconico import NicoNicoConnector
from app.connectors.note import NoteConnector
from app.connectors.rss import RSSConnector
from app.connectors.togetter import TogetterConnector
from app.connectors.tver import TVERConnector
from app.connectors.yahoonews import YahooNewsConnector
from app.connectors.youtube import YouTubeConnector
from app.database import SessionLocal
from app.models import Match, PlatformCredential, SourceItem, WatchTerm

log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
_poll_lock = asyncio.Lock()
_queued_task: asyncio.Task | None = None


def _build_connectors(db) -> list[BaseConnector]:
    connectors: list[BaseConnector] = [
        RSSConnector(),
        FiveChConnector(),
        GirlsChannelConnector(),
        ModelPressConnector(),
        NicoNicoConnector(),
        NoteConnector(),
        TogetterConnector(),
        TVERConnector(),
        YahooNewsConnector(),
    ]

    youtube_key = settings.youtube_api_key
    if not youtube_key:
        cred = db.get(PlatformCredential, "youtube")
        if cred:
            youtube_key = cred.api_key or ""
    
    # Always register YouTubeConnector so it can fetch using scrape fallback if no key is set
    connectors.append(YouTubeConnector(api_key=youtube_key))

    return connectors


async def poll_once() -> None:
    if _poll_lock.locked():
        log.info("Poll skipped because another poll is already running")
        return

    async with _poll_lock:
        await _poll_once_unlocked()


def queue_poll() -> bool:
    global _queued_task
    if _poll_lock.locked() or (_queued_task and not _queued_task.done()):
        return False
    _queued_task = asyncio.create_task(poll_once())
    return True


def _search_terms_for(term: WatchTerm) -> list[str]:
    seen: set[str] = set()
    searches: list[str] = []
    for value in [term.keyword, *(term.aliases or [])]:
        normalized = value.strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            searches.append(normalized)
    return searches


async def _poll_once_unlocked() -> None:
    db = SessionLocal()
    try:
        connectors = _build_connectors(db)
        terms = db.query(WatchTerm).filter(WatchTerm.is_active == True).all()  # noqa: E712

        for term in terms:
            for search_term in _search_terms_for(term):
                for connector in connectors:
                    try:
                        items = await connector.fetch(search_term, term.collection_mode)
                        new_count = 0
                        for raw in items:
                            if not db.get(SourceItem, raw.composite_id):
                                db.add(
                                    SourceItem(
                                        id=raw.composite_id,
                                        platform=raw.platform,
                                        item_id=raw.item_id,
                                        url=raw.url,
                                        published_at=raw.published_at,
                                        media_type=raw.media_type,
                                        author=raw.author,
                                        title=raw.title,
                                        content_text=raw.content_text,
                                        thumbnail_url=raw.thumbnail_url,
                                        raw_payload=raw.raw_payload,
                                    )
                                )
                            match_exists = (
                                db.query(Match)
                                .filter_by(watch_term_id=term.id, source_item_id=raw.composite_id)
                                .first()
                            )
                            if not match_exists:
                                db.add(Match(watch_term_id=term.id, source_item_id=raw.composite_id))
                                new_count += 1

                        db.commit()
                        if new_count:
                            await send_new_match_notifications(db, term, new_count)
                        log.info(
                            "term=%r search=%r connector=%s fetched=%d new=%d",
                            term.keyword,
                            search_term,
                            connector.PLATFORM,
                            len(items),
                            new_count,
                        )
                    except Exception as exc:
                        log.warning(
                            "poll failed term=%r search=%r connector=%s: %s",
                            term.keyword,
                            search_term,
                            connector.PLATFORM,
                            exc,
                            exc_info=True,
                        )
                        db.rollback()

    finally:
        db.close()


def start_scheduler() -> None:
    if not scheduler.get_job("poll_all"):
        scheduler.add_job(
            poll_once,
            "interval",
            minutes=settings.poll_interval_minutes,
            id="poll_all",
            max_instances=1,
            coalesce=True,
        )
    if not scheduler.running:
        scheduler.start()
    log.info("Scheduler started — polling every %d min", settings.poll_interval_minutes)
