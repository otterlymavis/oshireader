import logging

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
from app.models import Match, PlatformCredential, SourceItem, WatchTerm

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


async def poll_once() -> None:
    db = SessionLocal()
    try:
        connectors = _build_connectors(db)
        if not connectors:
            log.debug("No connectors configured — skipping poll")
            return

        terms = db.query(WatchTerm).filter(WatchTerm.is_active == True).all()  # noqa: E712
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
                    log.info("term=%r connector=%s fetched=%d new=%d", term.keyword, connector.PLATFORM, len(items), new_count)
                except Exception as exc:
                    log.warning(
                        "poll failed term=%r connector=%s: %s",
                        term.keyword,
                        connector.PLATFORM,
                        exc,
                        exc_info=True,
                    )
                    db.rollback()
    finally:
        db.close()


def start_scheduler() -> None:
    scheduler.add_job(poll_once, "interval", minutes=settings.poll_interval_minutes, id="poll_all")
    scheduler.start()
    log.info("Scheduler started — polling every %d min", settings.poll_interval_minutes)
