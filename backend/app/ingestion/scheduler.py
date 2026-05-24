import logging

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.connectors.base import BaseConnector
from app.connectors.fivech import FiveChConnector
from app.connectors.girlschannel import GirlsChannelConnector
from app.connectors.rss import RSSConnector
from app.connectors.togetter import TogetterConnector
from app.connectors.tver import TVERConnector
from app.connectors.twitter import TwitterConnector
from app.connectors.youtube import YouTubeConnector
from app.database import SessionLocal
from app.models import Match, PlatformCredential, PushToken, SourceItem, WatchTerm

log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


def _build_connectors(db) -> list[BaseConnector]:
    connectors: list[BaseConnector] = [
        RSSConnector(),
        FiveChConnector(),
        GirlsChannelConnector(),
        TogetterConnector(),
        TVERConnector(),
    ]

    youtube_key = settings.youtube_api_key
    if not youtube_key:
        cred = db.get(PlatformCredential, "youtube")
        if cred:
            youtube_key = cred.api_key or ""
    if youtube_key:
        connectors.append(YouTubeConnector(api_key=youtube_key))

    twitter_cred = db.get(PlatformCredential, "twitter")
    if twitter_cred and twitter_cred.bearer_token:
        connectors.append(TwitterConnector(bearer_token=twitter_cred.bearer_token))

    return connectors


async def _send_push_notifications(db, new_by_term: dict[str, int]) -> None:
    """Send Expo push notifications for terms that have new content."""
    tokens = [row.token for row in db.query(PushToken).all()]
    if not tokens:
        return

    messages = []
    for keyword, count in new_by_term.items():
        body = f"{count} new item{'s' if count > 1 else ''} found"
        for token in tokens:
            messages.append({
                "to": token,
                "title": f'New: "{keyword}"',
                "body": body,
                "sound": "default",
            })

    if not messages:
        return

    # Expo push API accepts up to 100 messages per request
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for i in range(0, len(messages), 100):
                chunk = messages[i:i + 100]
                resp = await client.post(
                    "https://exp.host/--/api/v2/push/send",
                    json=chunk,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                log.info("Push sent: %d messages, status=%d", len(chunk), resp.status_code)
    except Exception as exc:
        log.warning("Push notification failed: %s", exc)


async def poll_once() -> None:
    db = SessionLocal()
    try:
        connectors = _build_connectors(db)
        terms = db.query(WatchTerm).filter(WatchTerm.is_active == True).all()  # noqa: E712

        # Track new matches per keyword for push notifications
        new_by_term: dict[str, int] = {}

        for term in terms:
            for connector in connectors:
                try:
                    items = await connector.fetch(term.keyword, term.collection_mode)
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
                        new_by_term[term.keyword] = new_by_term.get(term.keyword, 0) + new_count
                    log.info("term=%r connector=%s fetched=%d new=%d", term.keyword, connector.PLATFORM, len(items), new_count)
                except Exception as exc:
                    log.warning(
                        "poll failed term=%r connector=%s: %s",
                        term.keyword, connector.PLATFORM, exc, exc_info=True,
                    )
                    db.rollback()

        # Send push notifications for terms that got new content
        if new_by_term:
            await _send_push_notifications(db, new_by_term)

    finally:
        db.close()


def start_scheduler() -> None:
    scheduler.add_job(poll_once, "interval", minutes=settings.poll_interval_minutes, id="poll_all")
    scheduler.start()
    log.info("Scheduler started — polling every %d min", settings.poll_interval_minutes)
