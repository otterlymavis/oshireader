import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from app.connectors.base import BaseConnector, CollectionMode, SourceItemCreate, parse_feed_date

log = logging.getLogger(__name__)

# Curated public RSS feeds — Japanese entertainment / idol news, no login needed.
FEEDS: list[tuple[str, str, str]] = [
    ("news", "natalie", "https://natalie.mu/music/feed/news"),
    ("news", "natalie", "https://natalie.mu/tv/feed/news"),
    ("news", "sponichi", "https://www.sponichi.co.jp/entertainment/rss/entertainmentAll.rdf"),
    ("news", "hochi", "https://hochi.news/rss/entertainment"),
]


class RSSConnector(BaseConnector):
    PLATFORM = "news"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == "media_only":
            return []

        kw = keyword.lower()
        results: list[SourceItemCreate] = []

        async def _one(platform: str, source: str, url: str) -> None:
            try:
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                    resp = await client.get(url)
                    if not resp.is_success:
                        log.warning("RSS feed returned status=%d source=%s", resp.status_code, source)
                        return
                feed = await asyncio.to_thread(feedparser.parse, resp.content)
                for entry in feed.entries:
                    title: str = entry.get("title", "")
                    summary: str = entry.get("summary", "")
                    link: str = entry.get("link", "")
                    if not link:
                        continue
                    if kw not in title.lower() and kw not in summary.lower():
                        continue
                    published = parse_feed_date(entry)
                    thumb = None
                    for enc in entry.get("enclosures", []):
                        if enc.get("type", "").startswith("image"):
                            thumb = enc.get("href")
                            break
                    results.append(
                        SourceItemCreate(
                            platform=platform,
                            item_id=entry.get("id") or link,
                            url=link,
                            published_at=published,
                            media_type="article",
                            title=title,
                            content_text=summary or None,
                            author=entry.get("author"),
                            thumbnail_url=thumb,
                            raw_payload={"source": source, "feed_url": url},
                        )
                    )
            except Exception as exc:
                log.warning("RSS feed failed source=%s: %s", source, exc)

        await asyncio.gather(*[_one(platform, source, url) for platform, source, url in FEEDS])
        return results
