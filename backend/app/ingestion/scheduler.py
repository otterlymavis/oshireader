from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text as sa_text

from app.apns import revalidate_unverified_devices, send_new_match_notifications
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
    CinemaCafeConnector,
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
from app.diagnostics import prune_backend_events, record_backend_event
from app.models import (
    APNSDeviceToken,
    BackendEvent,
    CollectionMode,
    Match,
    PendingNotification,
    PlatformCredential,
    SourceItem,
    WatchTerm,
)
from app.relevance import primary_text_matches, prune_irrelevant_matches

log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
_poll_lock = asyncio.Lock()
_queued_task: asyncio.Task | None = None

# Platforms where published_at should reflect "last reply/activity" rather than original
# post date. We update published_at whenever we see a more recent date from the connector.
_DISCUSSION_PLATFORMS: frozenset[str] = frozenset({"5ch", "girlschannel", "togetter"})
_NOTIFICATION_FRESHNESS_WINDOW = timedelta(hours=2)
_WATCH_TERM_CLOCK_SKEW = timedelta(minutes=5)
_ESTIMATED_DATE_NOTIFICATION_WARMUP = timedelta(hours=2)


def _build_connectors(db) -> list[BaseConnector]:
    connectors: list[BaseConnector] = [
        RSSConnector(),
        AERAConnector(),
        AmebloConnector(),
        BARKSConnector(),
        CinemaCafeConnector(),
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


def _observe_poll_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        log.info("Background poll task was cancelled")
    except Exception:
        log.exception("Background poll task failed")


def create_poll_task() -> asyncio.Task:
    task = asyncio.create_task(poll_once())
    task.add_done_callback(_observe_poll_task_result)
    return task


def queue_poll() -> bool:
    global _queued_task
    if _poll_lock.locked() or (_queued_task and not _queued_task.done()):
        return False
    _queued_task = create_poll_task()
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
        items = await asyncio.wait_for(
            connector.fetch(search_term, mode),
            timeout=settings.connector_fetch_timeout_seconds,
        )
    except asyncio.TimeoutError:
        log.warning(
            "fetch timeout connector=%s term=%r timeout=%ss",
            connector.PLATFORM,
            search_term,
            settings.connector_fetch_timeout_seconds,
        )
        return []
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


def _connector_batches(connectors: list[BaseConnector]) -> list[list[BaseConnector]]:
    """Split connector work so one poll cannot retain every response in memory at once."""
    batch_size = max(1, settings.connector_concurrency)
    return [
        connectors[index:index + batch_size]
        for index in range(0, len(connectors), batch_size)
    ]


def _poll_term_window(db, terms: list[WatchTerm]) -> tuple[list[WatchTerm], int, int]:
    total = len(terms)
    limit = settings.poll_terms_per_run
    if total == 0 or limit <= 0 or limit >= total:
        return terms, 0, 0

    latest = (
        db.query(BackendEvent)
        .filter(
            BackendEvent.kind == "poll",
            BackendEvent.status.in_(["completed", "completed_with_errors"]),
        )
        .order_by(BackendEvent.created_at.desc(), BackendEvent.id.desc())
        .first()
    )
    try:
        offset = int((latest.payload or {}).get("next_term_offset", 0)) if latest else 0
    except (TypeError, ValueError):
        offset = 0
    offset %= total
    next_offset = (offset + limit) % total

    if offset + limit <= total:
        return terms[offset:offset + limit], offset, next_offset
    return terms[offset:] + terms[:next_offset], offset, next_offset


def _disable_orphaned_notification_terms(db) -> int:
    grace_minutes = max(0, settings.orphaned_notification_grace_minutes)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)
    terms = (
        db.query(WatchTerm)
        .filter(WatchTerm.is_active == True)  # noqa: E712
        .filter(WatchTerm.notify_on_new == True)  # noqa: E712
        .filter(WatchTerm.owner_device_secret.isnot(None))
        .filter(WatchTerm.created_at <= cutoff)
        .all()
    )
    disabled = 0
    disabled_keywords: list[str] = []
    for term in terms:
        has_device = (
            db.query(APNSDeviceToken.token)
            .filter(APNSDeviceToken.device_secret == term.owner_device_secret)
            .first()
            is not None
        )
        if has_device:
            continue
        term.notify_on_new = False
        disabled += 1
        if len(disabled_keywords) < 10:
            disabled_keywords.append(term.keyword)

    if disabled:
        record_backend_event(
            db,
            "notification_maintenance",
            "disabled_orphaned_terms",
            "Disabled push alerts for owner-scoped terms with no APNs device",
            {
                "disabled_count": disabled,
                "grace_minutes": grace_minutes,
                "keywords": disabled_keywords,
            },
        )
        db.commit()
    return disabled


def _term_has_verified_device(db, term: WatchTerm) -> bool:
    query = db.query(APNSDeviceToken.token).filter(APNSDeviceToken.is_verified == True)  # noqa: E712
    if term.owner_device_secret:
        query = query.filter(APNSDeviceToken.device_secret == term.owner_device_secret)
    return query.first() is not None


def _deactivate_orphaned_duplicate_terms(db) -> int:
    grace_minutes = max(0, settings.orphaned_notification_grace_minutes)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)
    candidates = (
        db.query(WatchTerm)
        .filter(WatchTerm.is_active == True)  # noqa: E712
        .filter(WatchTerm.notify_on_new == False)  # noqa: E712
        .filter(WatchTerm.owner_device_secret.isnot(None))
        .filter(WatchTerm.created_at <= cutoff)
        .all()
    )
    deactivated = 0
    deactivated_keywords: list[str] = []
    for term in candidates:
        has_any_owner_device = (
            db.query(APNSDeviceToken.token)
            .filter(APNSDeviceToken.device_secret == term.owner_device_secret)
            .first()
            is not None
        )
        if has_any_owner_device:
            continue

        replacements = (
            db.query(WatchTerm)
            .filter(WatchTerm.id != term.id)
            .filter(WatchTerm.keyword == term.keyword)
            .filter(WatchTerm.is_active == True)  # noqa: E712
            .filter(WatchTerm.notify_on_new == True)  # noqa: E712
            .all()
        )
        if not any(_term_has_verified_device(db, replacement) for replacement in replacements):
            continue

        term.is_active = False
        deactivated += 1
        if len(deactivated_keywords) < 10:
            deactivated_keywords.append(term.keyword)

    if deactivated:
        record_backend_event(
            db,
            "notification_maintenance",
            "deactivated_orphaned_duplicates",
            "Deactivated stale duplicate owner-scoped terms with no APNs device",
            {
                "deactivated_count": deactivated,
                "grace_minutes": grace_minutes,
                "keywords": deactivated_keywords,
            },
        )
        db.commit()
    return deactivated


def _deactivate_orphaned_silent_terms(db) -> int:
    grace_minutes = max(0, settings.orphaned_notification_grace_minutes)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)
    candidates = (
        db.query(WatchTerm)
        .filter(WatchTerm.is_active == True)  # noqa: E712
        .filter(WatchTerm.notify_on_new == False)  # noqa: E712
        .filter(WatchTerm.owner_device_secret.isnot(None))
        .filter(WatchTerm.created_at <= cutoff)
        .all()
    )
    deactivated = 0
    deactivated_keywords: list[str] = []
    for term in candidates:
        has_any_owner_device = (
            db.query(APNSDeviceToken.token)
            .filter(APNSDeviceToken.device_secret == term.owner_device_secret)
            .first()
            is not None
        )
        if has_any_owner_device:
            continue

        term.is_active = False
        deactivated += 1
        if len(deactivated_keywords) < 10:
            deactivated_keywords.append(term.keyword)

    if deactivated:
        record_backend_event(
            db,
            "notification_maintenance",
            "deactivated_orphaned_silent_terms",
            "Deactivated stale non-notifying owner-scoped terms with no APNs device",
            {
                "deactivated_count": deactivated,
                "grace_minutes": grace_minutes,
                "keywords": deactivated_keywords,
            },
        )
        db.commit()
    return deactivated


def _published_at_is_estimated(raw, observed_at: datetime) -> bool:
    marker = (raw.raw_payload or {}).get("date_parsed")
    if marker is not None:
        return not bool(marker)
    return False


def _is_notification_eligible(
    *,
    term: WatchTerm,
    source_item: SourceItem,
    observed_at: datetime,
    term_had_existing_matches: bool = True,
) -> bool:
    """Keep historical matches in the feed without notifying as if they were new."""
    term_created_at = term.created_at or observed_at
    if term_created_at.tzinfo is None:
        term_created_at = term_created_at.replace(tzinfo=timezone.utc)

    if _published_at_is_estimated(source_item, observed_at):
        # Some sources only expose discovery time. Suppress those during the
        # first/backfill pass, then allow future discoveries to behave like
        # Twitter-style new-post alerts for established follows.
        return term_had_existing_matches or (
            observed_at - term_created_at >= _ESTIMATED_DATE_NOTIFICATION_WARMUP
        )

    published_at = source_item.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    cutoff = max(
        observed_at - _NOTIFICATION_FRESHNESS_WINDOW,
        term_created_at - _WATCH_TERM_CLOCK_SKEW,
    )
    return published_at >= cutoff


def _candidate_is_newer(
    *,
    candidate_is_estimated: bool,
    candidate_published_at: datetime,
    current_is_estimated: bool,
    current_published_at: datetime | None,
) -> bool:
    if current_published_at is None:
        return True
    if current_published_at.tzinfo is None:
        current_published_at = current_published_at.replace(tzinfo=timezone.utc)
    if current_is_estimated != candidate_is_estimated:
        return current_is_estimated and not candidate_is_estimated
    if candidate_published_at != current_published_at:
        return candidate_published_at > current_published_at
    return False


def _queue_pending_notification(
    db,
    term: WatchTerm,
    new_count: int,
    newest_candidate: tuple[bool, datetime, dict] | None,
) -> None:
    if new_count <= 0:
        return

    pending = db.get(PendingNotification, term.id)
    if pending is None:
        pending = PendingNotification(watch_term_id=term.id, new_count=0)
        db.add(pending)
    pending.new_count += new_count
    pending.updated_at = datetime.now(timezone.utc)

    if newest_candidate is None:
        return
    candidate_is_estimated, candidate_published_at, candidate_preview = newest_candidate
    if _candidate_is_newer(
        candidate_is_estimated=candidate_is_estimated,
        candidate_published_at=candidate_published_at,
        current_is_estimated=bool(pending.preview_is_estimated),
        current_published_at=pending.preview_published_at,
    ):
        pending.preview_item = candidate_preview
        pending.preview_published_at = candidate_published_at
        pending.preview_is_estimated = candidate_is_estimated


def _duplicate_notification_terms(db, term: WatchTerm) -> list[WatchTerm]:
    return (
        db.query(WatchTerm)
        .filter(WatchTerm.id != term.id)
        .filter(WatchTerm.keyword == term.keyword)
        .filter(WatchTerm.is_active == True)  # noqa: E712
        .filter(WatchTerm.notify_on_new == True)  # noqa: E712
        .order_by(WatchTerm.id)
        .all()
    )


def _preview_for_match(match: Match, source_item: SourceItem) -> dict:
    public_base_url = settings.backend_public_url.rstrip("/")
    return {
        "id": source_item.id,
        "match_id": match.id,
        "platform": source_item.platform,
        "url": source_item.url,
        "redirect_url": f"{public_base_url}/api/feed/matches/{match.id}/redirect",
        "title": source_item.title,
        "content_text": source_item.content_text,
        "author": source_item.author,
        "thumbnail_url": source_item.thumbnail_url,
        "media_type": source_item.media_type,
        "published_at": source_item.published_at.isoformat(),
    }


def _queue_duplicate_term_notifications(
    db,
    term: WatchTerm,
    raw_items: list[SourceItemCreate],
    observed_at: datetime,
) -> None:
    duplicate_terms = _duplicate_notification_terms(db, term)
    if not duplicate_terms or not raw_items:
        return

    source_item_ids = list(dict.fromkeys(raw.composite_id for raw in raw_items))

    for duplicate_term in duplicate_terms:
        existing_match_ids = {
            r[0]
            for r in db.query(Match.source_item_id)
            .filter(
                Match.watch_term_id == duplicate_term.id,
                Match.source_item_id.in_(source_item_ids),
            )
            .all()
        }
        term_had_existing_matches = (
            db.query(Match.id)
            .filter(Match.watch_term_id == duplicate_term.id)
            .first()
            is not None
        )

        new_matches: list[tuple[Match, SourceItem]] = []
        for source_item_id in source_item_ids:
            if source_item_id in existing_match_ids:
                continue
            source_item = db.get(SourceItem, source_item_id)
            if source_item is None:
                continue
            match = Match(watch_term_id=duplicate_term.id, source_item_id=source_item_id)
            db.add(match)
            existing_match_ids.add(source_item_id)
            new_matches.append((match, source_item))

        if not new_matches:
            continue

        db.flush()
        notification_count = 0
        newest_candidate: tuple[bool, datetime, dict] | None = None
        for match, source_item in new_matches:
            if not _is_notification_eligible(
                term=duplicate_term,
                source_item=source_item,
                observed_at=observed_at,
                term_had_existing_matches=term_had_existing_matches,
            ):
                continue
            notification_count += 1
            published_at = source_item.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            published_at_is_estimated = _published_at_is_estimated(source_item, observed_at)
            if (
                newest_candidate is None
                or _candidate_is_newer(
                    candidate_is_estimated=published_at_is_estimated,
                    candidate_published_at=published_at,
                    current_is_estimated=newest_candidate[0],
                    current_published_at=newest_candidate[1],
                )
            ):
                newest_candidate = (
                    published_at_is_estimated,
                    published_at,
                    _preview_for_match(match, source_item),
                )

        _queue_pending_notification(
            db,
            duplicate_term,
            notification_count,
            newest_candidate,
        )


async def _deliver_pending_notification(db, term: WatchTerm) -> bool:
    pending = db.get(PendingNotification, term.id)
    if pending is None:
        return True
    try:
        should_clear = await send_new_match_notifications(
            db,
            term,
            pending.new_count,
            pending.preview_item,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        db.rollback()
        log.warning(
            "notification delivery failed term=%r count=%d: %s",
            term.keyword,
            pending.new_count,
            exc,
            exc_info=True,
        )
        return False

    if should_clear is False:
        return False
    db.delete(pending)
    db.commit()
    return True


async def _flush_pending_notifications(db, exclude_term_ids: set[int] | None = None) -> None:
    exclude_term_ids = exclude_term_ids or set()
    for pending in db.query(PendingNotification).order_by(PendingNotification.updated_at).all():
        if pending.watch_term_id in exclude_term_ids:
            continue
        term = db.get(WatchTerm, pending.watch_term_id)
        if term is None:
            db.delete(pending)
            db.commit()
            continue
        await _deliver_pending_notification(db, term)


async def _poll_once_unlocked() -> None:
    db = SessionLocal()
    try:
        await revalidate_unverified_devices(db)
        _disable_orphaned_notification_terms(db)
        _deactivate_orphaned_duplicate_terms(db)
        _deactivate_orphaned_silent_terms(db)
        connectors = _build_connectors(db)
        all_terms = (
            db.query(WatchTerm)
            .filter(WatchTerm.is_active == True)  # noqa: E712
            .order_by(WatchTerm.id)
            .all()
        )
        terms, term_offset, next_term_offset = _poll_term_window(db, all_terms)
        processed_term_ids = {term.id for term in terms}
        total_new = 0
        failed_connectors = 0
        record_backend_event(
            db,
            "poll",
            "started",
            "Scheduled/backend poll started",
            {
                "terms": len(terms),
                "total_terms": len(all_terms),
                "connectors": len(connectors),
                "term_offset": term_offset,
                "next_term_offset": next_term_offset,
            },
        )

        for term in terms:
            for search_term in _search_terms_for(term):
                # Keep I/O parallel without retaining every connector's response,
                # parser tree, and result list until the slowest request finishes.
                # Small batches materially reduce peak RSS on memory-limited hosts.
                for connector_batch in _connector_batches(connectors):
                    batch_results = await asyncio.gather(
                        *[
                            _fetch_one(
                                connector,
                                search_term,
                                CollectionMode(term.collection_mode or "all_info"),
                            )
                            for connector in connector_batch
                        ]
                    )
                    for connector, items in zip(connector_batch, batch_results):
                        if not items:
                            continue
                        try:
                            new_count = 0
                            notification_count = 0
                            newest_candidate: tuple[bool, datetime, dict] | None = None
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
                            term_had_existing_matches = (
                                db.query(Match.id)
                                .filter(Match.watch_term_id == term.id)
                                .first()
                                is not None
                            )

                            for raw in items:
                                published_at = raw.published_at
                                if published_at.tzinfo is None:
                                    published_at = published_at.replace(tzinfo=timezone.utc)
                                if raw.composite_id not in existing_source_ids:
                                    db.add(
                                        SourceItem(
                                            id=raw.composite_id,
                                            platform=raw.platform,
                                            item_id=raw.item_id,
                                            url=raw.url,
                                            published_at=published_at,
                                            media_type=raw.media_type,
                                            author=raw.author,
                                            title=raw.title,
                                            content_text=raw.content_text,
                                            thumbnail_url=raw.thumbnail_url,
                                            raw_payload=raw.raw_payload,
                                        )
                                    )
                                    existing_source_ids.add(raw.composite_id)
                                    existing_items[raw.composite_id] = published_at
                                else:
                                    # Update published_at when the connector returns a better date.
                                    # Discussion platforms: always update toward newer dates so
                                    # threads with recent replies sort above stale ones.
                                    # Other platforms: only heal when dates differ significantly
                                    # (avoids spurious updates from fetch-time placeholders).
                                    stored = existing_items.get(raw.composite_id)
                                    if stored is not None:
                                        stored_aware = stored if stored.tzinfo else stored.replace(tzinfo=timezone.utc)
                                        if raw.platform in _DISCUSSION_PLATFORMS:
                                            # Only heal toward a newer date when the connector
                                            # actually parsed a real timestamp. A fetch-time
                                            # placeholder (date_parsed=False) is always ~now and
                                            # would otherwise re-pin the thread to the top every poll.
                                            date_parsed = (raw.raw_payload or {}).get("date_parsed", True)
                                            should_update = date_parsed and published_at > stored_aware
                                        else:
                                            new_age = (now - published_at).total_seconds()
                                            diff = abs((published_at - stored_aware).total_seconds())
                                            should_update = new_age > 300 and diff > 300
                                        if should_update:
                                            db.query(SourceItem).filter(
                                                SourceItem.id == raw.composite_id
                                            ).update(
                                                {"published_at": published_at},
                                                synchronize_session=False,
                                            )
                                            existing_items[raw.composite_id] = published_at
                                            log.info(
                                                "healed published_at for %s: %s → %s",
                                                raw.composite_id,
                                                stored_aware.isoformat(),
                                                published_at.isoformat(),
                                            )

                            # Flush source_items before inserting matches so that
                            # SQLite's FOREIGN KEY enforcement (PRAGMA foreign_keys=ON)
                            # can verify the source_item_id reference exists.
                            db.flush()

                            new_matches: list[tuple[Match, SourceItemCreate]] = []
                            for raw in items:
                                if raw.composite_id not in existing_match_ids:
                                    match = Match(watch_term_id=term.id, source_item_id=raw.composite_id)
                                    db.add(match)
                                    existing_match_ids.add(raw.composite_id)
                                    new_count += 1
                                    new_matches.append((match, raw))

                            # Single flush assigns autoincrement IDs to all new matches at once.
                            if new_matches:
                                db.flush()

                            for match, raw in new_matches:
                                source_item = db.get(SourceItem, raw.composite_id)
                                if source_item is None:
                                    raise RuntimeError(
                                        f"source item disappeared before notification preview: {raw.composite_id}"
                                    )
                                if not _is_notification_eligible(
                                    term=term,
                                    source_item=source_item,
                                    observed_at=now,
                                    term_had_existing_matches=term_had_existing_matches,
                                ):
                                    continue
                                notification_count += 1
                                published_at = source_item.published_at
                                if published_at.tzinfo is None:
                                    published_at = published_at.replace(tzinfo=timezone.utc)
                                published_at_is_estimated = _published_at_is_estimated(source_item, now)
                                if (
                                    newest_candidate is None
                                    or _candidate_is_newer(
                                        candidate_is_estimated=published_at_is_estimated,
                                        candidate_published_at=published_at,
                                        current_is_estimated=newest_candidate[0],
                                        current_published_at=newest_candidate[1],
                                    )
                                ):
                                    newest_candidate = (
                                        published_at_is_estimated,
                                        published_at,
                                        _preview_for_match(match, source_item),
                                    )

                            _queue_pending_notification(
                                db,
                                term,
                                notification_count,
                                newest_candidate,
                            )
                            _queue_duplicate_term_notifications(db, term, items, now)
                            db.flush()
                            db.commit()
                            if new_count:
                                total_new += new_count
                            log.info(
                                "term=%r search=%r connector=%s fetched=%d new=%d",
                                term.keyword,
                                search_term,
                                connector.PLATFORM,
                                len(items),
                                new_count,
                            )
                        except Exception as exc:
                            failed_connectors += 1
                            log.warning(
                                "poll failed term=%r search=%r connector=%s: %s",
                                term.keyword,
                                search_term,
                                connector.PLATFORM,
                                exc,
                                exc_info=True,
                            )
                            db.rollback()

            # Deliver once after every connector and alias has contributed to the
            # database-backed outbox. If the poll is canceled or APNs raises, the
            # pending row survives and is retried at the start of the next poll.
            await _deliver_pending_notification(db, term)

        await _flush_pending_notifications(db, exclude_term_ids=processed_term_ids)

        _prune_irrelevant_matches(db, terms)

        # Prune: keep at most 200 items per (platform, watch_term) to
        # prevent unbounded DB growth.  Community platforms (5ch, girlschannel)
        # are excluded — their threads are rare and long-lived.
        _prune_old_items(db)
        record_backend_event(
            db,
            "poll",
            "completed" if failed_connectors == 0 else "completed_with_errors",
            "Scheduled/backend poll completed",
            {
                "terms": len(terms),
                "total_terms": len(all_terms),
                "connectors": len(connectors),
                "new_matches": total_new,
                "failed_connectors": failed_connectors,
                "term_offset": term_offset,
                "next_term_offset": next_term_offset,
            },
        )
        prune_backend_events(db)
        for term in terms:
            try:
                db.expunge(term)
            except Exception:
                pass
        db.commit()

    finally:
        db.close()


def _prune_irrelevant_matches(db, terms: list[WatchTerm]) -> None:
    """Remove legacy matches whose visible title/post text no longer matches the term."""
    try:
        removed = prune_irrelevant_matches(db, terms)
        if removed:
            db.commit()
            log.info("Pruned %d irrelevant legacy match records", removed)
    except Exception as exc:
        log.warning("Irrelevant-match prune failed: %s", exc)
        db.rollback()


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
