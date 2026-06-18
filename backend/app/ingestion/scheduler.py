from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text as sa_text

from app.apns import send_new_match_notifications
from app.config import settings
from app.connectors.base import BaseConnector
from app.connectors.fivech import FiveChConnector
from app.connectors.girlschannel import GirlsChannelConnector
from app.connectors.mdpr import ModelPressConnector
from app.connectors.niconico import NicoNicoConnector
from app.connectors.note import NoteConnector
from app.connectors.oricon import OriconConnector
from app.connectors.news_sites import (
    AmebloConnector,
    AERAConnector,
    BARKSConnector,
    HochiConnector,
    LivedoorConnector,
    MantanWebConnector,
    RealSoundConnector,
    SponichiConnector,
)
from app.connectors.rss import RSSConnector
from app.connectors.smartnews import SmartNewsConnector
from app.connectors.togetter import TogetterConnector
from app.connectors.twitter import TwitterConnector
from app.connectors.tver import TVERConnector
from app.connectors.yahoonews import YahooNewsConnector
from app.connectors.youtube import YouTubeConnector
from app.database import SessionLocal
from app.models import CollectionMode, Match, PlatformCredential, SourceItem, WatchTerm
from app.relevance import primary_text_matches, prune_irrelevant_matches

log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
_poll_lock = asyncio.Lock()
_queued_task: asyncio.Task | None = None

# Platforms where published_at should reflect "last reply/activity" rather than original
# post date. We update published_at whenever we see a more recent date from the connector.
_DISCUSSION_PLATFORMS: frozenset[str] = frozenset({"5ch", "girlschannel", "togetter"})


def _build_connectors(db) -> list[BaseConnector]:
    connectors: list[BaseConnector] = [
        RSSConnector(),
        AERAConnector(),
        AmebloConnector(),
        BARKSConnector(),
        FiveChConnector(),
        GirlsChannelConnector(),
        HochiConnector(),
        LivedoorConnector(),
        MantanWebConnector(),
        ModelPressConnector(),
        NicoNicoConnector(),
        NoteConnector(),
        OriconConnector(),
        RealSoundConnector(),
        SmartNewsConnector(),
        SponichiConnector(),
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

    twitter_bearer = settings.twitter_bearer_token
    if not twitter_bearer:
        cred = db.get(PlatformCredential, "twitter")
        if cred:
            twitter_bearer = cred.bearer_token or ""
    connectors.append(TwitterConnector(bearer_token=twitter_bearer))

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


async def _fetch_one(connector: BaseConnector, search_term: str, mode: CollectionMode) -> list:
    """Run a single connector fetch; return [] on any error."""
    try:
        items = await connector.fetch(search_term, mode)
    except Exception as exc:
        log.warning("fetch error connector=%s term=%r: %s", connector.PLATFORM, search_term, exc)
        return []
    filtered = [item for item in items if primary_text_matches(search_term, item)]
    dropped = len(items) - len(filtered)
    if dropped:
        log.info(
            "filtered non-matching items connector=%s term=%r dropped=%d returned=%d",
            connector.PLATFORM,
            search_term,
            dropped,
            len(items),
        )
    return filtered


async def _poll_once_unlocked() -> None:
    db = SessionLocal()
    try:
        connectors = _build_connectors(db)
        terms = db.query(WatchTerm).filter(WatchTerm.is_active == True).all()  # noqa: E712

        for term in terms:
            for search_term in _search_terms_for(term):
                # Fetch all connectors in parallel — pure I/O, no DB contention.
                all_results = await asyncio.gather(
                    *[_fetch_one(c, search_term, CollectionMode(term.collection_mode or "all_info")) for c in connectors]
                )
                for connector, items in zip(connectors, all_results):
                    if not items:
                        continue
                    try:
                        new_count = 0
                        preview_item: dict | None = None
                        ids = [raw.composite_id for raw in items]
                        now = datetime.now(timezone.utc)

                        # Fetch existing ids AND their stored published_at so we can
                        # fix bad dates (fetch-time placeholders) when the connector
                        # now returns a real publication date.
                        existing_items: dict[str, datetime] = {
                            r[0]: r[1]
                            for r in db.query(SourceItem.id, SourceItem.published_at)
                            .filter(SourceItem.id.in_(ids))
                            .all()
                        }
                        existing_source_ids = set(existing_items.keys())
                        existing_match_ids = {
                            r[0]
                            for r in db.query(Match.source_item_id)
                            .filter(
                                Match.watch_term_id == term.id,
                                Match.source_item_id.in_(ids),
                            )
                            .all()
                        }

                        for raw in items:
                            if raw.composite_id not in existing_source_ids:
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
                                existing_source_ids.add(raw.composite_id)
                            else:
                                # Update published_at when the connector returns a better date.
                                # Discussion platforms: always update toward newer dates so
                                # threads with recent replies sort above stale ones.
                                # Other platforms: only heal when dates differ significantly
                                # (avoids spurious updates from fetch-time placeholders).
                                stored = existing_items.get(raw.composite_id)
                                new_pub = raw.published_at
                                if stored is not None and new_pub is not None:
                                    new_aware = new_pub if new_pub.tzinfo else new_pub.replace(tzinfo=timezone.utc)
                                    stored_aware = stored if stored.tzinfo else stored.replace(tzinfo=timezone.utc)
                                    if raw.platform in _DISCUSSION_PLATFORMS:
                                        # Only heal toward a newer date when the connector
                                        # actually parsed a real timestamp. A fetch-time
                                        # placeholder (date_parsed=False) is always ~now and
                                        # would otherwise re-pin the thread to the top every poll.
                                        date_parsed = (raw.raw_payload or {}).get("date_parsed", True)
                                        should_update = date_parsed and new_aware > stored_aware
                                    else:
                                        new_age = (now - new_aware).total_seconds()
                                        diff = abs((new_aware - stored_aware).total_seconds())
                                        should_update = new_age > 300 and diff > 300
                                    if should_update:
                                        db.query(SourceItem).filter(
                                            SourceItem.id == raw.composite_id
                                        ).update(
                                            {"published_at": new_aware},
                                            synchronize_session=False,
                                        )
                                        log.info(
                                            "healed published_at for %s: %s → %s",
                                            raw.composite_id,
                                            stored_aware.isoformat(),
                                            new_aware.isoformat(),
                                        )

                        # Flush source_items before inserting matches so that
                        # SQLite's FOREIGN KEY enforcement (PRAGMA foreign_keys=ON)
                        # can verify the source_item_id reference exists.
                        db.flush()

                        for raw in items:
                            if raw.composite_id not in existing_match_ids:
                                match = Match(watch_term_id=term.id, source_item_id=raw.composite_id)
                                db.add(match)
                                db.flush()
                                existing_match_ids.add(raw.composite_id)
                                new_count += 1
                                if preview_item is None:
                                    public_base_url = settings.backend_public_url.rstrip("/")
                                    preview_item = {
                                        "id": raw.composite_id,
                                        "match_id": match.id,
                                        "platform": raw.platform,
                                        "url": raw.url,
                                        "redirect_url": f"{public_base_url}/api/feed/matches/{match.id}/redirect",
                                        "title": raw.title,
                                        "content_text": raw.content_text,
                                        "author": raw.author,
                                        "thumbnail_url": raw.thumbnail_url,
                                    }

                        db.flush()
                        db.commit()
                        if new_count:
                            await send_new_match_notifications(db, term, new_count, preview_item)
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

        removed = prune_irrelevant_matches(db, terms)
        if removed:
            db.commit()
            log.info("Pruned %d irrelevant legacy match records", removed)

        # Prune: keep at most 200 items per (platform, watch_term) to
        # prevent unbounded DB growth.  Community platforms (5ch, girlschannel)
        # are excluded — their threads are rare and long-lived.
        _prune_old_items(db)

    finally:
        db.close()


def _prune_old_items(db) -> None:
    """Delete the oldest matches beyond 200 per (platform, watch_term) pair.

    Uses a single window-function query (ROW_NUMBER) instead of one query per
    pair — O(1) round-trips regardless of how many (platform, term) combos exist.
    Requires SQLite ≥ 3.25 / PostgreSQL ≥ 8.4 (both satisfied in production).
    """
    try:
        result = db.execute(sa_text("""
            DELETE FROM matches WHERE id IN (
                SELECT id FROM (
                    SELECT m.id,
                           ROW_NUMBER() OVER (
                               PARTITION BY si.platform, m.watch_term_id
                               ORDER BY si.published_at DESC
                           ) AS rn
                    FROM matches m
                    JOIN source_items si ON si.id = m.source_item_id
                    WHERE si.platform NOT IN ('5ch', 'girlschannel', 'togetter')
                ) ranked
                WHERE rn > 200
            )
        """))
        pruned = result.rowcount
        if pruned:
            db.execute(sa_text(
                "DELETE FROM source_items WHERE id NOT IN (SELECT source_item_id FROM matches)"
            ))
            db.commit()
            log.info("Pruned %d old match records", pruned)
    except Exception as exc:
        log.warning("Prune failed: %s", exc)
        db.rollback()


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
