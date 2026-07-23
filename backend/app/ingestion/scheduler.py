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
    AllkpopConnector,
    BARKSConnector,
    BillboardJapanConnector,
    CinemaCafeConnector,
    HochiConnector,
    KpopOfficialConnector,
    LivedoorConnector,
    MantanWebConnector,
    NatalieConnector,
    RealSoundConnector,
    SoompiConnector,
    SponichiConnector,
    TheTVConnector,
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
    MutedFeedItem,
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
_WATCH_TERM_CLOCK_SKEW = timedelta(minutes=5)
_FIVECH_FETCH_TIMEOUT_SECONDS = 35.0
_MUTED_FEED_ITEMS_PER_TERM_LIMIT = 2000
_CATCHUP_NOTIFICATION_MIN_MATCH_AGE = timedelta(minutes=10)
_PREVIEW_SOURCE_NEW_MATCH = "new_match"
_PREVIEW_SOURCE_DISCUSSION_REPLY_UPDATE = "discussion_reply_update"
_PENDING_NOTIFICATION_COUNT_KEY = "_notification_count"
_NotificationCandidate = tuple[bool, datetime, dict]


def _build_connectors(db) -> list[BaseConnector]:
    connectors: list[BaseConnector] = [
        RSSConnector(),
        AERAConnector(),
        AmebloConnector(),
        AllkpopConnector(),
        BARKSConnector(),
        BillboardJapanConnector(),
        CinemaCafeConnector(),
        FiveChConnector(),
        GirlsChannelConnector(),
        HochiConnector(),
        KpopOfficialConnector(),
        LivedoorConnector(),
        MantanWebConnector(),
        ModelPressConnector(),
        NatalieConnector(),
        NicoNicoConnector(),
        NoteConnector(),
        OriconConnector(),
        RealSoundConnector(),
        SmartNewsConnector(),
        SoompiConnector(),
        SponichiConnector(),
        TheTVConnector(),
        TogetterConnector(),
        TVERConnector(),
        YahooNewsConnector(),
    ]

    youtube_key = settings.youtube_api_key.strip()
    if not youtube_key:
        cred = db.get(PlatformCredential, "youtube")
        if cred:
            youtube_key = (cred.api_key or "").strip()

    # Always register YouTubeConnector so it can fetch using scrape fallback if no key is set
    connectors.append(YouTubeConnector(api_key=youtube_key))

    twitter_bearer = settings.twitter_bearer_token.strip()
    if not twitter_bearer:
        cred = db.get(PlatformCredential, "twitter")
        if cred:
            twitter_bearer = (cred.bearer_token or "").strip()
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


async def _fetch_one_result(
    connector: BaseConnector,
    search_term: str,
    mode: CollectionMode,
) -> tuple[list, bool]:
    """Run a connector fetch; return (items, fetch_succeeded)."""
    timeout_seconds = connector_fetch_timeout_seconds(connector)
    try:
        items = await asyncio.wait_for(
            connector.fetch(search_term, mode),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        log.warning(
            "fetch timeout connector=%s term=%r timeout=%ss",
            connector.PLATFORM,
            search_term,
            timeout_seconds,
        )
        return [], False
    except Exception as exc:
        log.warning("fetch error connector=%s term=%r: %s", connector.PLATFORM, search_term, exc)
        return [], False
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
    return filtered, True


async def _fetch_one(connector: BaseConnector, search_term: str, mode: CollectionMode) -> list:
    """Run a single connector fetch; return [] on any error."""
    items, _ = await _fetch_one_result(connector, search_term, mode)
    return items


def _cache_successful_fetch(
    fetch_cache: dict[tuple[str, str, str], list],
    cache_key: tuple[str, str, str],
    items: list,
    *,
    fetch_succeeded: bool,
) -> None:
    """Cache successful fetches, including legitimate empty result sets."""
    if fetch_succeeded:
        fetch_cache[cache_key] = items


def connector_fetch_timeout_seconds(connector: BaseConnector) -> float:
    timeout = settings.connector_fetch_timeout_seconds
    if connector.PLATFORM == "5ch":
        return max(timeout, _FIVECH_FETCH_TIMEOUT_SECONDS)
    return timeout


def _notification_freshness_window() -> timedelta:
    minutes = max(1, settings.notification_freshness_window_minutes)
    return timedelta(minutes=minutes)


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
    # Older connector payloads predate the marker. Preserve their existing
    # notification behavior; explicit fetch-time placeholders remain blocked.
    return False


def _is_notification_eligible(
    *,
    term: WatchTerm,
    source_item: SourceItem,
    observed_at: datetime,
) -> bool:
    """Keep historical matches in the feed without notifying as if they were new."""
    term_created_at = term.created_at or observed_at
    if term_created_at.tzinfo is None:
        term_created_at = term_created_at.replace(tzinfo=timezone.utc)

    if _published_at_is_estimated(source_item, observed_at):
        # A fetch-time placeholder cannot establish that the post is new. It
        # is safe to retain the match in the feed, but never use it for a push
        # or an established-follow catch-up notification.
        return False

    published_at = source_item.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    cutoff = max(
        observed_at - _notification_freshness_window(),
        term_created_at - _WATCH_TERM_CLOCK_SKEW,
    )
    return published_at >= cutoff


def _candidate_is_newer(
    *,
    candidate_rank: int = 1,
    candidate_is_estimated: bool,
    candidate_published_at: datetime,
    current_rank: int = 1,
    current_is_estimated: bool,
    current_published_at: datetime | None,
) -> bool:
    if current_published_at is None:
        return True
    if candidate_rank != current_rank:
        return candidate_rank > current_rank
    if current_published_at.tzinfo is None:
        current_published_at = current_published_at.replace(tzinfo=timezone.utc)
    if current_is_estimated != candidate_is_estimated:
        return current_is_estimated and not candidate_is_estimated
    if candidate_published_at != current_published_at:
        return candidate_published_at > current_published_at
    return False


def _notification_preview_rank(preview_item: dict | None) -> int:
    if not preview_item:
        return 1
    if preview_item.get("notification_preview_source") == _PREVIEW_SOURCE_DISCUSSION_REPLY_UPDATE:
        return 0
    return 1


def _queue_pending_notification(
    db,
    term: WatchTerm,
    candidates: list[_NotificationCandidate],
) -> None:
    if not term.notify_on_new:
        pending = db.get(PendingNotification, term.id)
        if pending is not None:
            db.delete(pending)
        return
    if not candidates:
        return

    pending = db.get(PendingNotification, term.id)
    if pending is None:
        pending = PendingNotification(watch_term_id=term.id, new_count=0)
        db.add(pending)
    previous_count = pending.new_count
    pending.new_count += len(candidates)
    pending.updated_at = datetime.now(timezone.utc)
    queued_items = _pending_notification_items(pending)
    if (
        not queued_items
        and isinstance(pending.preview_item, dict)
        and pending.preview_item
        and previous_count > 0
    ):
        legacy_item = dict(pending.preview_item)
        legacy_item[_PENDING_NOTIFICATION_COUNT_KEY] = previous_count
        queued_items.append(legacy_item)
    current_preview = queued_items[-1] if queued_items else None
    queued_items.extend(candidate[2] for candidate in candidates)
    pending.preview_item = {"items": queued_items}

    for candidate_is_estimated, candidate_published_at, candidate_preview in candidates:
        if _candidate_is_newer(
            candidate_rank=_notification_preview_rank(candidate_preview),
            candidate_is_estimated=candidate_is_estimated,
            candidate_published_at=candidate_published_at,
            current_rank=_notification_preview_rank(current_preview),
            current_is_estimated=bool(pending.preview_is_estimated),
            current_published_at=pending.preview_published_at,
        ):
            pending.preview_published_at = candidate_published_at
            pending.preview_is_estimated = candidate_is_estimated
            current_preview = candidate_preview


def _pending_notification_item_ids(pending: PendingNotification | None) -> set[str]:
    if pending is None:
        return set()
    ids = {
        item_id
        for item in _pending_notification_items(pending)
        if isinstance((item_id := item.get("id")), str) and item_id
    }
    if (
        isinstance(pending.preview_item, dict)
        and isinstance((preview_id := pending.preview_item.get("id")), str)
        and preview_id
    ):
        ids.add(preview_id)
    return ids


def _delivered_notification_item_ids(
    db,
    term_id: int,
    source_item_ids: list[str],
    *,
    since: datetime | None = None,
) -> set[str]:
    if not source_item_ids:
        return set()
    source_item_id_set = set(source_item_ids)
    query = (
        db.query(BackendEvent)
        .filter(BackendEvent.kind == "apns")
        .filter(BackendEvent.status == "attempted")
        .filter(BackendEvent.payload["term_id"].as_integer() == term_id)
    )
    if since is not None:
        query = query.filter(BackendEvent.created_at >= since)
    events = query.order_by(BackendEvent.id.desc()).all()
    delivered: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        try:
            delivered_count = int(payload.get("delivered_count") or 0)
        except (TypeError, ValueError):
            delivered_count = 0
        if delivered_count > 0:
            item_ids = payload.get("notification_item_ids")
            if not isinstance(item_ids, list):
                item_ids = []
            event_item_ids = {
                item_id
                for item_id in item_ids
                if isinstance(item_id, str) and item_id in source_item_id_set
            }
            preview_item_id = payload.get("preview_item_id")
            if isinstance(preview_item_id, str) and preview_item_id in source_item_id_set:
                event_item_ids.add(preview_item_id)
            delivered.update(event_item_ids)
    return delivered


def _catchup_candidate_sort_key(row: tuple[Match, SourceItem]) -> tuple[bool, float, int]:
    match, source_item = row
    published_at = source_item.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return (
        source_item.platform in _DISCUSSION_PLATFORMS,
        -published_at.timestamp(),
        match.id or 0,
    )


def _queue_recent_unnotified_match_notifications(
    db,
    term: WatchTerm,
    observed_at: datetime,
    *,
    limit: int = 200,
) -> int:
    """Catch up fresh matches that were saved before notification rules changed."""
    if not term.notify_on_new:
        return 0

    cutoff = observed_at - _notification_freshness_window()
    catchup_created_before = observed_at - _CATCHUP_NOTIFICATION_MIN_MATCH_AGE
    rows = (
        db.query(Match, SourceItem)
        .join(SourceItem, Match.source_item_id == SourceItem.id)
        .filter(Match.watch_term_id == term.id)
        .filter(Match.created_at >= cutoff)
        .filter(Match.created_at <= catchup_created_before)
        .filter(SourceItem.published_at >= cutoff)
        .order_by(Match.created_at.asc(), Match.id.asc())
        .limit(limit)
        .all()
    )
    if not rows:
        return 0

    source_item_ids = [source_item.id for _, source_item in rows]
    already_delivered_ids = _delivered_notification_item_ids(db, term.id, source_item_ids, since=cutoff)
    pending_ids = _pending_notification_item_ids(db.get(PendingNotification, term.id))
    muted_ids = {
        source_item_id
        for (source_item_id,) in (
            db.query(MutedFeedItem.source_item_id)
            .filter(MutedFeedItem.watch_term_id == term.id)
            .filter(MutedFeedItem.source_item_id.in_(source_item_ids))
            .all()
        )
    }

    candidates: list[_NotificationCandidate] = []
    for match, source_item in sorted(rows, key=_catchup_candidate_sort_key):
        if source_item.id in already_delivered_ids or source_item.id in pending_ids:
            continue
        if source_item.id in muted_ids:
            continue
        if _published_at_is_estimated(source_item, observed_at):
            continue
        if not _is_notification_eligible(
            term=term,
            source_item=source_item,
            observed_at=observed_at,
        ):
            continue
        candidates.append(_newest_notification_candidate(
            None,
            source_item=source_item,
            match=match,
            observed_at=observed_at,
        ))

    _queue_pending_notification(db, term, candidates)
    return len(candidates)


def _pending_notification_items(pending: PendingNotification) -> list[dict]:
    preview_item = pending.preview_item
    if isinstance(preview_item, dict) and isinstance(preview_item.get("items"), list):
        return [item for item in preview_item["items"] if isinstance(item, dict)]
    return []


def _pending_notification_item_count(preview_item: dict) -> int:
    try:
        count = int(preview_item.get(_PENDING_NOTIFICATION_COUNT_KEY, 1))
    except (TypeError, ValueError):
        return 1
    return max(1, count)


def _sendable_pending_preview(preview_item: dict) -> dict:
    if _PENDING_NOTIFICATION_COUNT_KEY not in preview_item:
        return preview_item
    return {
        key: value
        for key, value in preview_item.items()
        if key != _PENDING_NOTIFICATION_COUNT_KEY
    }


def _pending_preview_is_estimated(db, preview_item: dict, observed_at: datetime) -> bool:
    source_item_id = preview_item.get("id")
    if not isinstance(source_item_id, str) or not source_item_id:
        return False
    source_item = db.get(SourceItem, source_item_id)
    if source_item is None:
        return False
    return _published_at_is_estimated(source_item, observed_at)


def _newer_pending_preview(
    db,
    current_preview: dict | None,
    candidate_preview: dict,
    observed_at: datetime,
) -> dict:
    candidate_published_at = _parse_pending_preview_published_at(candidate_preview)
    current_published_at = (
        _parse_pending_preview_published_at(current_preview)
        if current_preview
        else None
    )
    if candidate_published_at is None:
        return current_preview or candidate_preview
    if current_preview is None or _candidate_is_newer(
        candidate_rank=_notification_preview_rank(candidate_preview),
        candidate_is_estimated=_pending_preview_is_estimated(db, candidate_preview, observed_at),
        candidate_published_at=candidate_published_at,
        current_rank=_notification_preview_rank(current_preview),
        current_is_estimated=_pending_preview_is_estimated(db, current_preview, observed_at),
        current_published_at=current_published_at,
    ):
        return candidate_preview
    return current_preview


def _parse_pending_preview_published_at(preview_item: dict) -> datetime | None:
    value = preview_item.get("published_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fallback_pending_preview_is_fresh(
    term: WatchTerm,
    preview_item: dict,
    observed_at: datetime,
) -> bool:
    published_at = _parse_pending_preview_published_at(preview_item)
    if published_at is None:
        return False

    term_created_at = term.created_at or observed_at
    if term_created_at.tzinfo is None:
        term_created_at = term_created_at.replace(tzinfo=timezone.utc)

    cutoff = max(
        observed_at - _notification_freshness_window(),
        term_created_at - _WATCH_TERM_CLOCK_SKEW,
    )
    return published_at >= cutoff


def _pending_preview_is_notification_eligible(
    db,
    term: WatchTerm,
    preview_item: dict,
    observed_at: datetime,
) -> bool:
    is_reply_update = (
        preview_item.get("notification_preview_source")
        == _PREVIEW_SOURCE_DISCUSSION_REPLY_UPDATE
    )
    source_item_id = preview_item.get("id")
    if not isinstance(source_item_id, str) or not source_item_id:
        return False

    source_item = db.get(SourceItem, source_item_id)
    if source_item is None:
        return False

    if is_reply_update:
        if _published_at_is_estimated(source_item, observed_at):
            return False
        return _is_notification_eligible(
            term=term,
            source_item=source_item,
            observed_at=observed_at,
        )

    return _is_notification_eligible(
        term=term,
        source_item=source_item,
        observed_at=observed_at,
    )


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


def _preview_for_match(
    match: Match,
    source_item: SourceItem,
    preview_source: str = _PREVIEW_SOURCE_NEW_MATCH,
) -> dict:
    public_base_url = settings.backend_public_url.rstrip("/")
    return {
        "id": source_item.id,
        "match_id": match.id,
        "notification_preview_source": preview_source,
        "platform": source_item.platform,
        "url": source_item.url,
        "redirect_url": f"{public_base_url}/api/feed/matches/{match.id}/redirect",
        "title": source_item.title,
        "content_text": source_item.content_text,
        "author": source_item.author,
        "thumbnail_url": source_item.thumbnail_url,
        "media_type": source_item.media_type,
        "published_at": source_item.published_at.isoformat(),
        "source": source_item.source,
    }


def _newest_notification_candidate(
    newest_candidate: tuple[bool, datetime, dict] | None,
    *,
    source_item: SourceItem,
    match: Match,
    observed_at: datetime,
    preview_source: str = _PREVIEW_SOURCE_NEW_MATCH,
) -> tuple[bool, datetime, dict]:
    published_at = source_item.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    published_at_is_estimated = _published_at_is_estimated(source_item, observed_at)
    if (
        newest_candidate is None
        or _candidate_is_newer(
            candidate_rank=_notification_preview_rank(
                {"notification_preview_source": preview_source}
            ),
            candidate_is_estimated=published_at_is_estimated,
            candidate_published_at=published_at,
            current_rank=_notification_preview_rank(newest_candidate[2]),
            current_is_estimated=newest_candidate[0],
            current_published_at=newest_candidate[1],
        )
    ):
        return (
            published_at_is_estimated,
            published_at,
            _preview_for_match(match, source_item, preview_source),
        )
    return newest_candidate


def _queue_duplicate_term_notifications(
    db,
    term: WatchTerm,
    raw_items: list[SourceItemCreate],
    observed_at: datetime,
    discussion_reply_source_ids: set[str] | None = None,
) -> None:
    duplicate_terms = _duplicate_notification_terms(db, term)
    if not duplicate_terms or not raw_items:
        return

    discussion_reply_source_ids = discussion_reply_source_ids or set()
    source_item_ids = list(dict.fromkeys(raw.composite_id for raw in raw_items))

    for duplicate_term in duplicate_terms:
        existing_match_ids = {
            r[0]: r[1]
            for r in db.query(Match.source_item_id, Match.id)
            .filter(
                Match.watch_term_id == duplicate_term.id,
                Match.source_item_id.in_(source_item_ids),
            )
            .all()
        }
        muted_source_ids = {
            r[0]
            for r in db.query(MutedFeedItem.source_item_id)
            .filter(
                MutedFeedItem.watch_term_id == duplicate_term.id,
                MutedFeedItem.source_item_id.in_(source_item_ids),
            )
            .all()
        }
        new_matches: list[tuple[Match, SourceItem]] = []
        for source_item_id in source_item_ids:
            if source_item_id in muted_source_ids:
                continue
            if source_item_id in existing_match_ids:
                continue
            source_item = db.get(SourceItem, source_item_id)
            if source_item is None:
                continue
            match = Match(watch_term_id=duplicate_term.id, source_item_id=source_item_id)
            db.add(match)
            existing_match_ids[source_item_id] = match.id
            new_matches.append((match, source_item))

        reply_update_matches = [
            (source_item_id, match_id)
            for source_item_id, match_id in existing_match_ids.items()
            if source_item_id in discussion_reply_source_ids
            and source_item_id not in muted_source_ids
            and match_id is not None
        ]

        if not new_matches and not reply_update_matches:
            continue

        db.flush()
        notification_candidates: list[_NotificationCandidate] = []
        for source_item_id, match_id in reply_update_matches:
            source_item = db.get(SourceItem, source_item_id)
            match = db.get(Match, match_id)
            if source_item is None or match is None:
                continue
            notification_candidates.append(_newest_notification_candidate(
                None,
                source_item=source_item,
                match=match,
                observed_at=observed_at,
                preview_source=_PREVIEW_SOURCE_DISCUSSION_REPLY_UPDATE,
            ))

        for match, source_item in new_matches:
            if not _is_notification_eligible(
                term=duplicate_term,
                source_item=source_item,
                observed_at=observed_at,
            ):
                continue
            notification_candidates.append(_newest_notification_candidate(
                None,
                source_item=source_item,
                match=match,
                observed_at=observed_at,
            ))

        _queue_pending_notification(
            db,
            duplicate_term,
            notification_candidates,
        )


def _fresh_sendable_notification_term(db, term_id: int) -> WatchTerm | None:
    fresh_term = (
        db.query(WatchTerm)
        .populate_existing()
        .filter(WatchTerm.id == term_id)
        .first()
    )
    if fresh_term is None or not fresh_term.is_active or not fresh_term.notify_on_new:
        return None
    return fresh_term


async def _deliver_pending_notification(db, term: WatchTerm) -> bool:
    pending = db.get(PendingNotification, term.id)
    if pending is None:
        return True
    term = _fresh_sendable_notification_term(db, term.id)
    if term is None:
        db.delete(pending)
        db.commit()
        return True
    observed_at = datetime.now(timezone.utc)
    try:
        pending_items = _pending_notification_items(pending)
        if not pending_items:
            if not isinstance(pending.preview_item, dict) or not _pending_preview_is_notification_eligible(
                db,
                term,
                pending.preview_item,
                observed_at,
            ):
                db.delete(pending)
                db.commit()
                return True
            should_clear = await send_new_match_notifications(
                db,
                term,
                pending.new_count,
                pending.preview_item,
            )
            if should_clear is False:
                return False
            db.delete(pending)
            db.commit()
            return True

        send_count = 0
        send_preview: dict | None = None
        send_item_ids: list[str] = []
        for preview_item in pending_items:
            if not _pending_preview_is_notification_eligible(db, term, preview_item, observed_at):
                continue
            send_count += _pending_notification_item_count(preview_item)
            item_id = preview_item.get("id")
            if isinstance(item_id, str) and item_id not in send_item_ids:
                send_item_ids.append(item_id)
            send_preview = _newer_pending_preview(db, send_preview, preview_item, observed_at)

        if send_count <= 0 or send_preview is None:
            db.delete(pending)
            db.commit()
            return True

        fresh_term = _fresh_sendable_notification_term(db, term.id)
        if fresh_term is None:
            pending = db.get(PendingNotification, term.id)
            if pending is not None:
                db.delete(pending)
                db.commit()
            return True
        term = fresh_term
        should_clear = await send_new_match_notifications(
            db,
            term,
            send_count,
            _sendable_pending_preview(send_preview),
            notification_item_ids=send_item_ids,
        )
        if should_clear is False:
            return False

        pending = db.get(PendingNotification, term.id)
        if pending is not None:
            db.delete(pending)
            db.commit()
        return True
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

    return True


async def _flush_pending_notifications(
    db,
    exclude_term_ids: set[int] | None = None,
) -> set[int]:
    exclude_term_ids = exclude_term_ids or set()
    flushed_term_ids: set[int] = set()
    for pending in db.query(PendingNotification).order_by(PendingNotification.updated_at).all():
        if pending.watch_term_id in exclude_term_ids:
            continue
        term = db.get(WatchTerm, pending.watch_term_id)
        if term is None:
            db.delete(pending)
            db.commit()
            continue
        await _deliver_pending_notification(db, term)
        # Exclude only rows that remain queued after this attempt. If delivery
        # succeeded or an unverifiable legacy row was discarded, new items
        # queued later in this poll still need to be delivered.
        if db.get(PendingNotification, term.id) is not None:
            flushed_term_ids.add(term.id)
    return flushed_term_ids


async def _poll_once_unlocked() -> None:
    db = SessionLocal()
    try:
        await revalidate_unverified_devices(db)
        _disable_orphaned_notification_terms(db)
        _deactivate_orphaned_duplicate_terms(db)
        _deactivate_orphaned_silent_terms(db)
        all_terms = (
            db.query(WatchTerm)
            .filter(WatchTerm.is_active == True)  # noqa: E712
            .order_by(WatchTerm.id)
            .all()
        )
        if not all_terms:
            flushed_pending_term_ids = await _flush_pending_notifications(db)
            record_backend_event(
                db,
                "poll",
                "skipped",
                "Scheduled/backend poll skipped because there are no active watch terms",
                {
                    "terms": 0,
                    "total_terms": 0,
                    "connectors": 0,
                    "flushed_pending_terms": len(flushed_pending_term_ids),
                    "term_offset": 0,
                    "next_term_offset": 0,
                },
            )
            prune_backend_events(db)
            db.commit()
            return

        connectors = _build_connectors(db)
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

        flushed_pending_term_ids = await _flush_pending_notifications(db)
        fetch_cache: dict[tuple[str, str, str], list] = {}

        for term in terms:
            for search_term in _search_terms_for(term):
                # Keep I/O parallel without retaining every connector's response,
                # parser tree, and result list until the slowest request finishes.
                # Small batches materially reduce peak RSS on memory-limited hosts.
                for connector_batch in _connector_batches(connectors):
                    mode = CollectionMode(term.collection_mode or "all_info")
                    batch_results: list[list | None] = [None] * len(connector_batch)
                    uncached: list[tuple[int, BaseConnector, tuple[str, str, str]]] = []
                    for idx, connector in enumerate(connector_batch):
                        cache_key = (connector.PLATFORM, search_term, mode.value)
                        if cache_key in fetch_cache:
                            batch_results[idx] = fetch_cache[cache_key]
                        else:
                            uncached.append((idx, connector, cache_key))

                    if uncached:
                        fetched = await asyncio.gather(
                            *[
                                _fetch_one_result(connector, search_term, mode)
                                for _, connector, _ in uncached
                            ]
                        )
                        for (idx, _, cache_key), (items, fetch_succeeded) in zip(uncached, fetched):
                            _cache_successful_fetch(
                                fetch_cache,
                                cache_key,
                                items,
                                fetch_succeeded=fetch_succeeded,
                            )
                            batch_results[idx] = items

                    for connector, items in zip(connector_batch, batch_results):
                        if not items:
                            continue
                        try:
                            new_count = 0
                            notification_candidates: list[_NotificationCandidate] = []
                            discussion_reply_source_ids: set[str] = set()
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
                                r[0]: r[1]
                                for r in db.query(Match.source_item_id, Match.id)
                                .filter(
                                    Match.watch_term_id == term.id,
                                    Match.source_item_id.in_(ids),
                                )
                                .all()
                            }
                            muted_source_ids = {
                                r[0]
                                for r in db.query(MutedFeedItem.source_item_id)
                                .filter(
                                    MutedFeedItem.watch_term_id == term.id,
                                    MutedFeedItem.source_item_id.in_(ids),
                                )
                                .all()
                            }
                            for raw in items:
                                if raw.composite_id in muted_source_ids:
                                    continue
                                published_at = raw.published_at
                                if published_at.tzinfo is None:
                                    published_at = published_at.replace(tzinfo=timezone.utc)
                                discussion_reply_match_id: int | None = None
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
                                            source_item = db.get(SourceItem, raw.composite_id)
                                            if source_item is not None:
                                                source_item.published_at = published_at
                                                if raw.platform in _DISCUSSION_PLATFORMS:
                                                    source_item.url = raw.url or source_item.url
                                                    source_item.media_type = raw.media_type or source_item.media_type
                                                    source_item.author = raw.author or source_item.author
                                                    source_item.title = raw.title or source_item.title
                                                    source_item.content_text = raw.content_text or source_item.content_text
                                                    source_item.thumbnail_url = raw.thumbnail_url or source_item.thumbnail_url
                                                healed_payload = {
                                                    **(source_item.raw_payload or {}),
                                                    **(raw.raw_payload or {}),
                                                }
                                                if raw.platform in _DISCUSSION_PLATFORMS:
                                                    healed_payload["date_parsed"] = True
                                                source_item.raw_payload = healed_payload
                                            existing_items[raw.composite_id] = published_at
                                            log.info(
                                                "healed published_at for %s: %s → %s",
                                                raw.composite_id,
                                                stored_aware.isoformat(),
                                                published_at.isoformat(),
                                            )
                                            if (
                                                raw.platform in _DISCUSSION_PLATFORMS
                                                and raw.composite_id in existing_match_ids
                                            ):
                                                discussion_reply_match_id = existing_match_ids[raw.composite_id]
                                                discussion_reply_source_ids.add(raw.composite_id)

                                if discussion_reply_match_id is not None:
                                    db.flush()
                                    source_item = db.get(SourceItem, raw.composite_id)
                                    match = db.get(Match, discussion_reply_match_id)
                                    if source_item is not None and match is not None:
                                        notification_candidates.append(_newest_notification_candidate(
                                            None,
                                            source_item=source_item,
                                            match=match,
                                            observed_at=now,
                                            preview_source=_PREVIEW_SOURCE_DISCUSSION_REPLY_UPDATE,
                                        ))

                            # Flush source_items before inserting matches so that
                            # SQLite's FOREIGN KEY enforcement (PRAGMA foreign_keys=ON)
                            # can verify the source_item_id reference exists.
                            db.flush()

                            new_matches: list[tuple[Match, SourceItemCreate]] = []
                            for raw in items:
                                if raw.composite_id in muted_source_ids:
                                    continue
                                if raw.composite_id not in existing_match_ids:
                                    match = Match(watch_term_id=term.id, source_item_id=raw.composite_id)
                                    db.add(match)
                                    existing_match_ids[raw.composite_id] = match.id
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
                                ):
                                    continue
                                notification_candidates.append(_newest_notification_candidate(
                                    None,
                                    source_item=source_item,
                                    match=match,
                                    observed_at=now,
                                ))

                            _queue_pending_notification(
                                db,
                                term,
                                notification_candidates,
                            )
                            _queue_duplicate_term_notifications(
                                db,
                                term,
                                items,
                                now,
                                discussion_reply_source_ids=discussion_reply_source_ids,
                            )
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
            catchup_count = _queue_recent_unnotified_match_notifications(
                db,
                term,
                datetime.now(timezone.utc),
            )
            if catchup_count:
                db.flush()
                db.commit()
                log.info(
                    "queued catch-up notifications term=%r count=%d",
                    term.keyword,
                    catchup_count,
                )
            if term.id not in flushed_pending_term_ids:
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
    _prune_old_items_with_limit(db, muted_per_term_limit=_MUTED_FEED_ITEMS_PER_TERM_LIMIT)


def _delete_orphan_source_items(db) -> int:
    result = db.execute(sa_text(
        "DELETE FROM source_items "
        "WHERE id NOT IN (SELECT source_item_id FROM matches) "
        "AND id NOT IN (SELECT source_item_id FROM muted_feed_items)"
    ))
    return result.rowcount or 0


def _prune_old_muted_feed_items(db, per_term_limit: int) -> int:
    if per_term_limit <= 0:
        return 0

    result = db.execute(sa_text("""
        DELETE FROM muted_feed_items WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY watch_term_id
                           ORDER BY created_at IS NULL ASC, created_at DESC, id DESC
                       ) AS rn
                FROM muted_feed_items
            ) ranked
            WHERE rn > :per_term_limit
        )
    """), {"per_term_limit": per_term_limit})
    return result.rowcount or 0


def _prune_old_items_with_limit(
    db,
    muted_per_term_limit: int,
    match_per_term_platform_limit: int = 200,
    include_discussion_platforms: bool = False,
    raise_on_error: bool = False,
) -> dict[str, int]:
    try:
        discussion_filter = "" if include_discussion_platforms else (
            "WHERE si.platform NOT IN ('5ch', 'girlschannel', 'togetter')"
        )
        result = db.execute(sa_text(f"""
            DELETE FROM matches WHERE id IN (
                SELECT id FROM (
                    SELECT m.id,
                           ROW_NUMBER() OVER (
                               PARTITION BY si.platform, m.watch_term_id
                               ORDER BY si.published_at DESC
                           ) AS rn
                    FROM matches m
                    JOIN source_items si ON si.id = m.source_item_id
                    {discussion_filter}
                ) ranked
                WHERE rn > :match_per_term_platform_limit
            )
        """), {"match_per_term_platform_limit": match_per_term_platform_limit})
        pruned = result.rowcount or 0
        muted_pruned = _prune_old_muted_feed_items(db, muted_per_term_limit)
        orphan_source_items_pruned = _delete_orphan_source_items(db)
        if pruned or muted_pruned or orphan_source_items_pruned:
            db.commit()
            log.info(
                "Pruned %d old match records, %d muted feed items, and %d orphan source items",
                pruned,
                muted_pruned,
                orphan_source_items_pruned,
            )
        return {
            "matches_pruned": pruned,
            "muted_feed_items_pruned": muted_pruned,
            "orphan_source_items_pruned": orphan_source_items_pruned,
        }
    except Exception as exc:
        log.warning("Prune failed: %s", exc)
        db.rollback()
        if raise_on_error:
            raise
        return {
            "matches_pruned": 0,
            "muted_feed_items_pruned": 0,
            "orphan_source_items_pruned": 0,
        }


def prune_storage(
    db,
    match_per_term_platform_limit: int = 100,
    muted_per_term_limit: int = 500,
    include_discussion_platforms: bool = True,
    backend_event_keep: int = 200,
) -> dict[str, int | bool]:
    """Run quota-oriented storage pruning for manual maintenance."""
    match_per_term_platform_limit = max(1, match_per_term_platform_limit)
    muted_per_term_limit = max(0, muted_per_term_limit)
    backend_event_keep = max(1, backend_event_keep)

    event_count_before = db.query(BackendEvent).count()
    summary = _prune_old_items_with_limit(
        db,
        muted_per_term_limit=muted_per_term_limit,
        match_per_term_platform_limit=match_per_term_platform_limit,
        include_discussion_platforms=include_discussion_platforms,
        raise_on_error=True,
    )
    prune_backend_events(db, keep=backend_event_keep, raise_on_error=True)
    db.commit()
    event_count_after = db.query(BackendEvent).count()
    summary.update({
        "backend_events_pruned": max(0, event_count_before - event_count_after),
        "match_per_term_platform_limit": match_per_term_platform_limit,
        "muted_per_term_limit": muted_per_term_limit,
        "backend_event_keep": backend_event_keep,
        "included_discussion_platforms": include_discussion_platforms,
    })
    return summary


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
