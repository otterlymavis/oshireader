import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, quote_plus

import feedparser
import httpx

from app.connectors.base import (
    GOOGLE_NEWS_HEADERS,
    SCRAPE_USER_AGENT,
    BaseConnector,
    CollectionMode,
    SourceItemCreate,
    build_google_news_jina_items,
    fetch_search_rss_via_proxy,
    is_recent_search_result,
    parse_feed_date,
    title_contains_keyword,
)

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": SCRAPE_USER_AGENT,
}

# Curated public RSS feeds for the generic News source.
#
# Natalie has its own dedicated connector/platform, so keep its feeds out of
# this generic source to avoid storing and notifying duplicate copies as both
# `news:*` and `natalie:*`.
FEEDS: list[tuple[str, str, str]] = []


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
                    if published is None:
                        continue
                    if not is_recent_search_result(published):
                        continue
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
        # Google News is unreachable directly from Render's outbound IP (see
        # CLAUDE.md) and the Cloudflare Worker proxy is now also blocked by
        # Google from Cloudflare's IP ranges, so neither is worth the timeout
        # budget: go straight to the jina.ai reader proxy, then Bing.
        items = await self._fetch_google_news_history_jina(keyword, url)
        if items:
            return items
        return await self._fetch_bing_news(keyword)

    async def _fetch_google_news_history_jina(
        self,
        keyword: str,
        google_news_url: str,
    ) -> list[SourceItemCreate]:
        proxy_url = "https://r.jina.ai/http://" + google_news_url.replace("https://", "")
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(proxy_url)
                if not resp.is_success:
                    return []
        except Exception as exc:
            log.warning("Google News Jina history fallback failed: %s", exc)
            return []

        return build_google_news_jina_items(
            resp.text,
            keyword,
            platform=self.PLATFORM,
            author="Google News",
        )

    async def _fetch_bing_news(self, keyword: str) -> list[SourceItemCreate]:
        url = f"https://www.bing.com/news/search?q={quote_plus(keyword)}&format=rss&mkt=ja-JP"
        source = "bing_news_proxy"
        content = await fetch_search_rss_via_proxy(keyword, target="bing")
        try:
            if not content:
                source = "bing_news"
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                    resp = await client.get(url)
                    if not resp.is_success:
                        return []
                content = resp.content
            feed = await asyncio.to_thread(feedparser.parse, content)
        except Exception as exc:
            log.warning("Bing News fallback failed: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries[:25]:
            title = (entry.get("title") or "").strip()
            link = entry.get("link", "")
            item_id = entry.get("id") or link
            if not link or not title_contains_keyword(keyword, title) or item_id in seen:
                continue
            published = parse_feed_date(entry)
            if published is None:
                continue
            if not is_recent_search_result(published):
                continue
            seen.add(item_id)
            items.append(SourceItemCreate(
                platform=self.PLATFORM,
                item_id=item_id,
                url=link,
                published_at=published,
                media_type="article",
                title=title,
                content_text=entry.get("summary") or None,
                author="Bing News",
                raw_payload={"source": source, "keyword": keyword},
            ))
        return items
