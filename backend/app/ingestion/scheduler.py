from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import or_, text as sa_text

from app.apns import revalidate_unverified_devices, send_new_match_notifications
from app.config import settings
from app.entitlements import push_delivery_allowed
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
from app.connectors.twitter import TwitterConnector
from app.connectors.tver import TVERConnector
from app.connectors.yahoonews import YahooNewsConnector
from app.connectors.youtube import YouTubeConnector
from app.database import SessionLocal
from app.diagnostics import prune_backend_events, record_backend_event
from app.source_health import begin_poll_cycle, record_fetch_result, record_filtered
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
_DISCUSSION_PLATFORMS: frozenset[str] = frozenset({"5ch", "girlschannel"})
_WATCH_TERM_CLOCK_SKEW = timedelta(minutes=5)
_DEVICE_OWNED_REFRESH_INTERVAL = timedelta(minutes=180)
_MUTED_FEED_ITEMS_PER_TERM_LIMIT = 500
_CATCHUP_NOTIFICATION_MIN_MATCH_AGE = timedelta(minutes=10)
_PREVIEW_SOURCE_NEW_MATCH = "new_match"
_PREVIEW_SOURCE_DISCUSSION_REPLY_UPDATE = "discussion_reply_update"
_PENDING_NOTIFICATION_COUNT_KEY = "_notification_count"
_NotificationCandidate = tuple[bool, datetime, dict]


def _past_orphaned_owner_grace_filter(cutoff: datetime):
    return or_(WatchTerm.created_at.is_(None), WatchTerm.created_at <= cutoff)


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
    # Connectors that do not support media filtering already return no items
    # for MEDIA_ONLY terms. Avoid opening the network request at all while
    # preserving the successful-empty-result semantics used by the poller.
    if mode == CollectionMode.MEDIA_ONLY and not connector.SUPPORTS_MEDIA_FILTER:
        if connector.REPORTS_STATUS_TO_CLIENT:
            record_filtered(connector.PLATFORM)
        return [], True
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
        if connector.REPORTS_STATUS_TO_CLIENT:
            record_fetch_result(connector.PLATFORM, succeeded=False, error=f"timeout after {timeout_seconds}s")
        return [], False
    except Exception as exc:
        log.warning("fetch error connector=%s term=%r: %s", connector.PLATFORM, search_term, exc)
        if connector.REPORTS_STATUS_TO_CLIENT:
            # Only the exception type is exposed via /api/source-health (a public,
            # cross-user diagnostic). str(exc) often embeds the search term itself
            # (e.g. in a request URL or a connector's own error message), which
            # would otherwise leak other users' watched keywords.
            record_fetch_result(connector.PLATFORM, succeeded=False, error=type(exc).__name__)
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
    if connector.REPORTS_STATUS_TO_CLIENT:
        record_fetch_result(connector.PLATFORM, succeeded=True, item_count=len(filtered))
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
    if connector.MIN_FETCH_TIMEOUT_SECONDS is not None:
        return max(timeout, connector.MIN_FETCH_TIMEOUT_SECONDS)
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


def _connectors_for_term(term: WatchTerm, connectors: list[BaseConnector]) -> list[BaseConnector]:
    if (term.source_mode or "all").casefold() != "selected":
        return connectors
    selected = _selected_platforms(term)
    return [connector for connector in connectors if connector.PLATFORM.casefold() in selected]


_MEDIA_TYPES = {"video", "image"}


def _selected_platforms(term: WatchTerm) -> set[str]:
    return {
        p.strip().casefold()
        for p in (term.selected_platforms or [])
        if isinstance(p, str) and p.strip()
    }


def _term_allows_source_item(
    term: WatchTerm,
    source_item: SourceItem,
    *,
    selected_platforms: set[str] | None = None,
) -> bool:
    """Whether `term`'s own platform/collection-mode filters admit this item.

    Used when fanning a match found under one term's filters out to other
    terms sharing the same keyword: each recipient term must still pass its
    own filters, since it may be scoped to different platforms/media.
    `selected_platforms` lets a caller iterating many items for the same
    term precompute the set once instead of rebuilding it per item.
    """
    if (term.source_mode or "all").casefold() == "selected":
        selected = selected_platforms if selected_platforms is not None else _selected_platforms(term)
        if source_item.platform.casefold() not in selected:
            return False
    if (term.collection_mode or "all_info") == CollectionMode.MEDIA_ONLY.value:
        if source_item.media_type not in _MEDIA_TYPES:
            return False
    return True


def _poll_term_window(db, terms: list[WatchTerm]) -> tuple[list[WatchTerm], int, int]:
    total = len(terms)
    limit = settings.poll_terms_per_run
    if total == 0 or limit <= 0 or limit >= total:
        return terms, 0, 0

    def sort_key(term: WatchTerm) -> tuple[datetime, int]:
        last_polled = term.last_polled_at
        if last_polled is None:
            last_polled = datetime.min.replace(tzinfo=timezone.utc)
        elif last_polled.tzinfo is None:
            last_polled = last_polled.replace(tzinfo=timezone.utc)
        else:
            last_polled = last_polled.astimezone(timezone.utc)
        return last_polled, term.id or 0

    # Poll the stalest due terms first. Reusing an offset from the previous
    # due-term list can skip terms after the list shrinks between runs.
    selected = sorted(terms, key=sort_key)[:limit]
    return selected, 0, 0


def _notify_lane_terms(
    due_terms: list[WatchTerm],
    orphaned_term_ids: set[int],
) -> list[WatchTerm]:
    """Owner-scoped notify-enabled due terms that are not orphaned.

    These are polled first so that a large backlog of silent/feed-only terms
    cannot delay APNs checks. Global terms (no owner) are excluded because they
    have no device to deliver to.
    """
    return [
        term for term in due_terms
        if term.notify_on_new
        and term.owner_device_secret is not None
        and term.id not in orphaned_term_ids
    ]


def _term_refresh_interval(term: WatchTerm) -> timedelta:
    minutes = settings.refresh_intervals_minutes.get(
        (term.refresh_tier or "free").casefold(),
        settings.refresh_intervals_minutes["free"],
    )
    configured = timedelta(minutes=minutes)
    # iOS asks the backend for a poll roughly every 170 minutes. App-created
    # terms are owner-scoped, so cap only those terms at three hours instead of
    # collapsing the free/standard/premium cadence for every API client.
    if term.owner_device_secret:
        return min(configured, _DEVICE_OWNED_REFRESH_INTERVAL)
    return configured


def _term_is_due(term: WatchTerm, now: datetime) -> bool:
    return term.last_polled_at is None or (
        (term.last_polled_at if term.last_polled_at.tzinfo else term.last_polled_at.replace(tzinfo=timezone.utc))
        + _term_refresh_interval(term)
        <= now
    )


def _inactive_owner_term_ids(db, terms: list[WatchTerm]) -> set[int]:
    days = max(0, settings.inactive_poll_after_days)
    if days <= 0:
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    owner_secrets = {term.owner_device_secret for term in terms if term.owner_device_secret}
    if not owner_secrets:
        return set()
    active_secrets = {
        device_secret
        for (device_secret,) in db.query(APNSDeviceToken.device_secret)
        .filter(APNSDeviceToken.device_secret.in_(owner_secrets))
        .filter(APNSDeviceToken.is_verified == True)  # noqa: E712
        .filter(APNSDeviceToken.last_seen_at >= cutoff)
        .all()
        if device_secret
    }
    return {
        term.id for term in terms
        if term.owner_device_secret and term.owner_device_secret not in active_secrets
    }


def _report_orphaned_notification_terms(db) -> set[int]:
    """Report owner-scoped terms without devices without changing preferences.

    APNs registration can be temporarily absent or unverified while an app is
    launching, reinstalling, or switching APNs environments. Disabling
    ``notify_on_new`` here permanently overwrote the user's setting and made a
    transient registration problem look like an intentional opt-out.
    """
    grace_minutes = max(0, settings.orphaned_notification_grace_minutes)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)
    terms = (
        db.query(WatchTerm)
        .filter(WatchTerm.is_active == True)  # noqa: E712
        .filter(WatchTerm.notify_on_new == True)  # noqa: E712
        .filter(WatchTerm.owner_device_secret.isnot(None))
        .filter(_past_orphaned_owner_grace_filter(cutoff))
        .all()
    )
    orphaned_ids: set[int] = set()
    orphaned_keywords: list[str] = []
    for term in terms:
        if _term_has_verified_device(db, term):
            continue
        orphaned_ids.add(term.id)
        if len(orphaned_keywords) < 10:
            orphaned_keywords.append(term.keyword)

    if orphaned_ids:
        record_backend_event(
            db,
            "notification_maintenance",
            "orphaned_terms_detected",
            "Detected owner-scoped terms without a verified APNs device; preserved preferences for retry",
            {
                "orphaned_count": len(orphaned_ids),
                "grace_minutes": grace_minutes,
                "keywords": orphaned_keywords,
            },
        )
        db.commit()
    return orphaned_ids


def _prune_stale_orphaned_pending_notifications(db, orphaned_notify_term_ids: set[int]) -> None:
    """Clear pending counts that have sat behind a missing device too long.

    _report_orphaned_notification_terms intentionally never disables
    notify_on_new, so a transient registration gap can't look like an
    intentional opt-out. But if the owner never reconnects a device, the
    associated PendingNotification would otherwise grow without bound
    forever. Filtered on created_at rather than updated_at: a term sharing
    a keyword with an actively-polled term keeps getting its pending row
    refreshed via duplicate-term fan-out (_queue_duplicate_term_notifications)
    even while orphaned, which would keep updated_at fresh indefinitely and
    defeat this entirely. created_at is a fixed anchor set once when the row
    is first created, so a term still orphaned pending_notification_stale_
    after_days after that point gets pruned regardless of how many times it
    was subsequently touched. The preference stays on, and a fresh row is
    queued from scratch as soon as new content next arrives.
    """
    if not orphaned_notify_term_ids:
        return
    days = max(0, settings.pending_notification_stale_after_days)
    if days <= 0:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stale = (
        db.query(PendingNotification)
        .filter(PendingNotification.watch_term_id.in_(orphaned_notify_term_ids))
        .filter(PendingNotification.created_at <= cutoff)
        .all()
    )
    if not stale:
        return
    pruned_term_ids = [pending.watch_term_id for pending in stale]
    for pending in stale:
        db.delete(pending)
    # Commit the deletes before recording the diagnostic event: record_backend_event
    # swallows its own failures with an internal rollback, which would otherwise
    # silently undo these deletes too if it ran first in the same transaction.
    db.commit()
    record_backend_event(
        db,
        "notification_maintenance",
        "stale_pending_pruned",
        "Cleared pending notification counts stuck behind a missing device",
        {"term_ids": pruned_term_ids, "stale_after_days": days},
    )
    db.commit()


def _term_has_verified_device(db, term: WatchTerm) -> bool:
    query = db.query(APNSDeviceToken.token).filter(APNSDeviceToken.is_verified == True)  # noqa: E712
    if term.owner_device_secret:
        query = query.filter(APNSDeviceToken.device_secret == term.owner_device_secret)
    return query.first() is not None


def _report_orphaned_duplicate_terms(db) -> set[int]:
    """Report duplicate owner terms without deactivating the user's keyword."""
    grace_minutes = max(0, settings.orphaned_notification_grace_minutes)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)
    candidates = (
        db.query(WatchTerm)
        .filter(WatchTerm.is_active == True)  # noqa: E712
        .filter(WatchTerm.notify_on_new == False)  # noqa: E712
        .filter(WatchTerm.owner_device_secret.isnot(None))
        .filter(_past_orphaned_owner_grace_filter(cutoff))
        .all()
    )
    orphaned_ids: set[int] = set()
    orphaned_keywords: list[str] = []
    for term in candidates:
        if _term_has_verified_device(db, term):
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

        orphaned_ids.add(term.id)
        if len(orphaned_keywords) < 10:
            orphaned_keywords.append(term.keyword)

    if orphaned_ids:
        record_backend_event(
            db,
            "notification_maintenance",
            "orphaned_duplicates_detected",
            "Detected stale duplicate owner-scoped terms; preserved keyword state",
            {
                "detected_count": len(orphaned_ids),
                "grace_minutes": grace_minutes,
                "keywords": orphaned_keywords,
            },
        )
        db.commit()
    return orphaned_ids


def _report_orphaned_silent_terms(db) -> set[int]:
    """Report silent owner terms without deactivating the user's keyword."""
    grace_minutes = max(0, settings.orphaned_notification_grace_minutes)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)
    candidates = (
        db.query(WatchTerm)
        .filter(WatchTerm.is_active == True)  # noqa: E712
        .filter(WatchTerm.notify_on_new == False)  # noqa: E712
        .filter(WatchTerm.owner_device_secret.isnot(None))
        .filter(_past_orphaned_owner_grace_filter(cutoff))
        .all()
    )
    orphaned_ids: set[int] = set()
    orphaned_keywords: list[str] = []
    for term in candidates:
        if _term_has_verified_device(db, term):
            continue

        orphaned_ids.add(term.id)
        if len(orphaned_keywords) < 10:
            orphaned_keywords.append(term.keyword)

    if orphaned_ids:
        record_backend_event(
            db,
            "notification_maintenance",
            "orphaned_silent_terms_detected",
            "Detected stale non-notifying owner-scoped terms; preserved keyword state",
            {
                "detected_count": len(orphaned_ids),
                "grace_minutes": grace_minutes,
                "keywords": orphaned_keywords,
            },
        )
        db.commit()
    return orphaned_ids


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
    return cutoff <= published_at <= observed_at + _WATCH_TERM_CLOCK_SKEW


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
    if not term.notify_on_new or not push_delivery_allowed(db, term):
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
    if not term.notify_on_new or not push_delivery_allowed(db, term):
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


def resolve_sendable_pending_preview(db, pending: PendingNotification) -> dict | None:
    """Unwrap a PendingNotification's preview_item into a flat, sendable preview dict.

    preview_item is stored as {"items": [...]} once any candidate has been queued
    (see _queue_pending_notification); callers must not pass it to APNs payload
    building unwrapped, or every field lookup silently misses.
    """
    items = _pending_notification_items(pending)
    if not items:
        preview_item = pending.preview_item
        return preview_item if isinstance(preview_item, dict) and preview_item else None
    observed_at = pending.updated_at or pending.created_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    best: dict | None = None
    for item in items:
        best = _newer_pending_preview(db, best, item, observed_at)
    return _sendable_pending_preview(best) if best else None


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


def _parse_pending_preview_queued_at(preview_item: dict) -> datetime | None:
    value = preview_item.get("queued_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _pending_preview_is_notification_eligible(
    db,
    term: WatchTerm,
    preview_item: dict,
    observed_at: datetime,
) -> bool:
    # Prefer the timestamp this specific item was queued at over the pending
    # row's shared anchor. A pending row can accumulate items queued at
    # different times (e.g. a discussion reply arrives after an earlier item
    # on the same term was deferred); using the row-wide anchor for all of
    # them would judge a fresh item's own published_at against a stale
    # reference point and reject it as implausibly "too new". Older rows
    # queued before this field existed fall back to the row-level anchor.
    item_observed_at = _parse_pending_preview_queued_at(preview_item) or observed_at

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
        if _published_at_is_estimated(source_item, item_observed_at):
            return False
        return _is_notification_eligible(
            term=term,
            source_item=source_item,
            observed_at=item_observed_at,
        )

    return _is_notification_eligible(
        term=term,
        source_item=source_item,
        observed_at=item_observed_at,
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
    *,
    queued_at: datetime,
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
        # Frozen at queue time so a later delivery retry can't make this item
        # look stale (matches the intent of anchoring eligibility checks to
        # when an item was queued rather than to "now" at delivery time).
        # Read back per-item in _pending_preview_is_notification_eligible
        # instead of the whole pending row's created_at, so a fresh item
        # appended to an already-queued row isn't judged against a stale
        # anchor left over from an earlier, unrelated item on that same row.
        "queued_at": queued_at.isoformat(),
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
            _preview_for_match(match, source_item, preview_source, queued_at=observed_at),
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
        duplicate_term_selected_platforms = _selected_platforms(duplicate_term)
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
            if not _term_allows_source_item(
                duplicate_term, source_item, selected_platforms=duplicate_term_selected_platforms
            ):
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
    if not push_delivery_allowed(db, fresh_term):
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
    # Anchor freshness to when the notification was first queued so that
    # APNs retry delays do not shift the eligibility window forward.
    # created_at is immutable (set once on row creation); updated_at would
    # advance on every preview refresh and defeat this purpose.
    _created_at = pending.created_at or pending.updated_at
    if _created_at is None:
        observed_at = datetime.now(timezone.utc)
    elif _created_at.tzinfo is None:
        observed_at = _created_at.replace(tzinfo=timezone.utc)
    else:
        observed_at = _created_at
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

        eligible_items = [
            preview_item
            for preview_item in pending_items
            if _pending_preview_is_notification_eligible(db, term, preview_item, observed_at)
        ]
        # Oldest first, so a batch that accumulated across a missed delivery
        # arrives in publish order instead of newest-first.
        eligible_items.sort(
            key=lambda item: _parse_pending_preview_published_at(item) or observed_at
        )
        # A source item can be queued more than once (e.g. a discussion thread
        # re-queued as a reply update before the prior queue entry was
        # delivered). Collapse to one send per id so a queuing duplicate
        # doesn't become a duplicate push now that each item sends on its own.
        seen_item_ids: set[str] = set()
        deduped_items: list[dict] = []
        for preview_item in eligible_items:
            item_id = preview_item.get("id")
            if isinstance(item_id, str):
                if item_id in seen_item_ids:
                    continue
                seen_item_ids.add(item_id)
            deduped_items.append(preview_item)
        eligible_items = deduped_items

        if not eligible_items:
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

        # One push per item: no aggregation into a single "keyword ほかN件"
        # notification. Items that fail to send stay queued for retry.
        # Persist progress after every send (not just once at the end) so
        # that if a later item raises, already-delivered items are already
        # off the pending row and don't get resent on the next retry.
        remaining_items: list[dict] = list(eligible_items)
        for preview_item in eligible_items:
            item_id = preview_item.get("id")
            item_ids = [item_id] if isinstance(item_id, str) else []
            should_clear = await send_new_match_notifications(
                db,
                term,
                _pending_notification_item_count(preview_item),
                _sendable_pending_preview(preview_item),
                notification_item_ids=item_ids,
            )
            if should_clear is not False:
                remaining_items.remove(preview_item)

            pending = db.get(PendingNotification, term.id)
            if pending is None:
                return not remaining_items
            if remaining_items:
                pending.preview_item = {"items": remaining_items}
                pending.new_count = sum(_pending_notification_item_count(item) for item in remaining_items)
                pending.updated_at = datetime.now(timezone.utc)
                db.commit()
            else:
                db.delete(pending)
                db.commit()

        return not remaining_items
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


async def _poll_terms_batch(
    db,
    terms: list[WatchTerm],
    connectors: list[BaseConnector],
    fetch_cache: dict[tuple[str, str, str], list],
    flushed_pending_term_ids: set[int],
) -> tuple[int, int]:
    """Poll a list of terms using the shared connector pool and fetch cache.

    Returns (total_new_matches, failed_connector_count). The caller is
    responsible for stamping last_polled_at and committing afterwards.
    """
    total_new = 0
    failed_connectors = 0
    for term in terms:
        term_connectors = _connectors_for_term(term, connectors)
        for search_term in _search_terms_for(term):
            # Keep I/O parallel without retaining every connector's response,
            # parser tree, and result list until the slowest request finishes.
            # Small batches materially reduce peak RSS on memory-limited hosts.
            for connector_batch in _connector_batches(term_connectors):
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

    return total_new, failed_connectors


async def _poll_once_unlocked() -> None:
    begin_poll_cycle()
    db = SessionLocal()
    try:
        await revalidate_unverified_devices(db)
        notify_orphaned_term_ids = _report_orphaned_notification_terms(db)
        orphaned_term_ids = (
            notify_orphaned_term_ids
            | _report_orphaned_duplicate_terms(db)
            | _report_orphaned_silent_terms(db)
        )
        try:
            _prune_stale_orphaned_pending_notifications(db, notify_orphaned_term_ids)
        except Exception:
            # Isolated so a transient DB error while pruning doesn't abort the
            # rest of this poll cycle — connector fetches and notification
            # delivery below matter far more than this maintenance step.
            db.rollback()
            log.warning("stale pending notification pruning failed", exc_info=True)
        active_terms = (
            db.query(WatchTerm)
            .filter(WatchTerm.is_active == True)  # noqa: E712
            .order_by(WatchTerm.id)
            .all()
        )
        # Preserve the user's active setting, but do not fetch an owner-scoped
        # term while its device is absent. Device registration makes the term
        # eligible again on the next poll.
        inactive_term_ids = _inactive_owner_term_ids(db, active_terms)
        paused_push_term_ids = {
            term.id
            for term in active_terms
            if term.notify_on_new and not push_delivery_allowed(db, term)
        }
        excluded_term_ids = orphaned_term_ids | inactive_term_ids | paused_push_term_ids
        eligible_terms = [
            term for term in active_terms
            if term.id not in excluded_term_ids
        ]
        due_terms = [term for term in eligible_terms if _term_is_due(term, datetime.now(timezone.utc))]
        if not due_terms:
            flushed_pending_term_ids = await _flush_pending_notifications(
                db,
                exclude_term_ids=excluded_term_ids,
            )
            record_backend_event(
                db,
                "poll",
                "skipped",
                "Scheduled/backend poll skipped because no watch terms are due",
                {
                    "terms": 0,
                    "total_terms": len(active_terms),
                    "due_terms": 0,
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

        # Notification lane: notify-enabled owner terms processed first so that
        # a large backlog of silent/feed-only terms cannot delay APNs checks.
        notify_terms = _notify_lane_terms(due_terms, orphaned_term_ids)
        notify_term_ids = {t.id for t in notify_terms}

        # Regular feed lane: remaining due terms through the LRU window.
        remaining_due = [t for t in due_terms if t.id not in notify_term_ids]
        regular_terms, term_offset, next_term_offset = _poll_term_window(db, remaining_due)

        all_terms = notify_terms + regular_terms
        total_new = 0
        failed_connectors = 0

        record_backend_event(
            db,
            "poll",
            "started",
            "Scheduled/backend poll started",
            {
                "terms": len(all_terms),
                "total_terms": len(active_terms),
                "due_terms": len(due_terms),
                "notification_lane_terms": len(notify_terms),
                "regular_lane_terms": len(regular_terms),
                "connectors": len(connectors),
                "term_offset": term_offset,
                "next_term_offset": next_term_offset,
            },
        )

        flushed_pending_term_ids = await _flush_pending_notifications(
            db,
            exclude_term_ids=excluded_term_ids,
        )
        fetch_cache: dict[tuple[str, str, str], list] = {}

        if notify_terms:
            lane_new, lane_failed = await _poll_terms_batch(
                db, notify_terms, connectors, fetch_cache, flushed_pending_term_ids,
            )
            total_new += lane_new
            failed_connectors += lane_failed
            log.info(
                "notification lane: polled %d term(s), %d new match(es)",
                len(notify_terms),
                lane_new,
            )

        if regular_terms:
            lane_new, lane_failed = await _poll_terms_batch(
                db, regular_terms, connectors, fetch_cache, flushed_pending_term_ids,
            )
            total_new += lane_new
            failed_connectors += lane_failed

        await _flush_pending_notifications(
            db,
            exclude_term_ids={t.id for t in all_terms} | excluded_term_ids,
        )

        _prune_irrelevant_matches(db, all_terms)

        # Prune: keep at most 100 items per (platform, watch_term) to
        # prevent unbounded DB growth.  Community platforms (5ch, girlschannel)
        # are excluded — their threads are rare and long-lived.
        _prune_old_items(db)
        record_backend_event(
            db,
            "poll",
            "completed" if failed_connectors == 0 else "completed_with_errors",
            "Scheduled/backend poll completed",
            {
                "terms": len(all_terms),
                "total_terms": len(active_terms),
                "due_terms": len(due_terms),
                "notification_lane_terms": len(notify_terms),
                "regular_lane_terms": len(regular_terms),
                "connectors": len(connectors),
                "new_matches": total_new,
                "failed_connectors": failed_connectors,
                "term_offset": term_offset,
                "next_term_offset": next_term_offset,
            },
        )
        prune_backend_events(db)
        polled_at = datetime.now(timezone.utc)
        for term in all_terms:
            term.last_polled_at = polled_at
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
    """Delete the oldest matches beyond 100 per (platform, watch_term) pair.

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
    match_per_term_platform_limit: int = 100,
    include_discussion_platforms: bool = False,
    raise_on_error: bool = False,
) -> dict[str, int]:
    try:
        params = {"match_per_term_platform_limit": match_per_term_platform_limit}
        discussion_filter = ""
        if not include_discussion_platforms:
            platform_params = {
                f"discussion_platform_{i}": platform
                for i, platform in enumerate(_DISCUSSION_PLATFORMS)
            }
            placeholders = ", ".join(f":{key}" for key in platform_params)
            discussion_filter = f"WHERE si.platform NOT IN ({placeholders})"
            params.update(platform_params)
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
        """), params)
        pruned = result.rowcount or 0
        muted_pruned = _prune_old_muted_feed_items(db, muted_per_term_limit)
        orphan_source_items_pruned = _delete_orphan_source_items(db)
        retention_days = max(0, settings.retention_days)
        retention_matches_pruned = 0
        retention_muted_pruned = 0
        retention_source_items_pruned = 0
        if retention_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            retention_matches_pruned = db.execute(
                sa_text("DELETE FROM matches WHERE created_at < :cutoff"),
                {"cutoff": cutoff},
            ).rowcount or 0
            retention_muted_pruned = db.execute(
                sa_text("DELETE FROM muted_feed_items WHERE created_at < :cutoff"),
                {"cutoff": cutoff},
            ).rowcount or 0
            retention_source_items_pruned = db.execute(
                sa_text(
                    "DELETE FROM source_items "
                    "WHERE published_at < :cutoff "
                    "AND id NOT IN (SELECT source_item_id FROM matches) "
                    "AND id NOT IN (SELECT source_item_id FROM muted_feed_items)"
                ),
                {"cutoff": cutoff},
            ).rowcount or 0
        if (
            pruned
            or muted_pruned
            or orphan_source_items_pruned
            or retention_matches_pruned
            or retention_muted_pruned
            or retention_source_items_pruned
        ):
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
            "retention_matches_pruned": retention_matches_pruned,
            "retention_muted_feed_items_pruned": retention_muted_pruned,
            "retention_source_items_pruned": retention_source_items_pruned,
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
            "retention_matches_pruned": 0,
            "retention_muted_feed_items_pruned": 0,
            "retention_source_items_pruned": 0,
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
