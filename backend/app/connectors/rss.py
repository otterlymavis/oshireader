import asyncio
import logging
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    GOOGLE_NEWS_HEADERS,
    SourceItemCreate,
    parse_feed_date,
    parse_google_news_markdown,
    title_contains_keyword,
)

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

# Curated public RSS feeds — Japanese entertainment / idol news, no login needed.
# sponichi and hochi removed their RSS feeds; natalie/tv removed their TV section feed.
# natalie/eiga currently returns HTTP 500, so use the healthy public feeds.
FEEDS: list[tuple[str, str, str]] = [
    ("news", "natalie", "https://natalie.mu/music/feed/news"),
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
                        log.debug("RSS feed returned status=%d source=%s", resp.status_code, source)
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
                log.debug("RSS feed failed source=%s feed_url=%s: %s", source, url, exc)

        await asyncio.gather(*[_one(platform, source, url) for platform, source, url in FEEDS])
        if not results:
            log.debug("RSS feeds returned no results for keyword=%r; using Google News history fallback", keyword)
            results = await self._fetch_google_news_history(keyword)
        return results

    async def _fetch_google_news_history(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(f"{keyword} when:10y")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("Google News history fallback failed: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries[:25]:
            title = (entry.get("title") or "").strip()
            link = entry.get("link", "")
            item_id = entry.get("id") or link
            if not link or not title_contains_keyword(keyword, title) or item_id in seen:
                continue
            seen.add(item_id)
            items.append(SourceItemCreate(
                platform=self.PLATFORM,
                item_id=item_id,
                url=link,
                published_at=parse_feed_date(entry),
                media_type="article",
                title=title,
                content_text=entry.get("summary") or None,
                author="Google News",
                raw_payload={"source": "google_news_history", "keyword": keyword},
            ))
        if items:
            return items
        return await self._fetch_google_news_history_jina(keyword, url)

    async def _fetch_google_news_history_jina(
        self,
        keyword: str,
        google_news_url: str,
    ) -> list[SourceItemCreate]:
        proxy_url = "https://r.jina.ai/http://" + google_news_url.replace("https://", "")
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(proxy_url)
                if not resp.is_success:
                    return []
        except Exception as exc:
            log.warning("Google News Jina history fallback failed: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        for entry in parse_google_news_markdown(resp.text)[:25]:
            title = entry["title"]
            if not title_contains_keyword(keyword, title):
                continue
            items.append(SourceItemCreate(
                platform=self.PLATFORM,
                item_id=entry["url"],
                url=entry["url"],
                published_at=entry["published_at"],
                media_type="article",
                title=title,
                content_text=None,
                author="Google News",
                raw_payload={"source": "google_news_jina", "keyword": keyword},
            ))
        return items
