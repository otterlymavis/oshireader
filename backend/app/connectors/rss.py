import asyncio
import logging

import feedparser
import httpx

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    SourceItemCreate,
    parse_feed_date,
    title_contains_keyword,
)

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

# Curated public RSS feeds — Japanese entertainment / idol news, no login needed.
# sponichi and hochi removed their RSS feeds; natalie/tv removed their TV section feed.
FEEDS: list[tuple[str, str, str]] = [
    ("news", "natalie", "https://natalie.mu/music/feed/news"),
    ("news", "natalie", "https://natalie.mu/eiga/feed/news"),
    ("news", "natalie", "https://natalie.mu/stage/feed/news"),
]


class RSSConnector(BaseConnector):
    PLATFORM = "news"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        results: list[SourceItemCreate] = []

        async def _one(platform: str, source: str, url: str) -> None:
            try:
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=_HEADERS) as client:
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
                    if not title_contains_keyword(keyword, title):
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
