from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote, quote_plus

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    GOOGLE_NEWS_HEADERS,
    SourceItemCreate,
    build_google_news_jina_items,
    fetch_search_rss_via_proxy,
    parse_feed_date,
    title_contains_keyword,
)

log = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def _clean_html_summary(value: str | None) -> str | None:
    if not value:
        return None
    text = BeautifulSoup(value, "lxml").get_text(" ", strip=True)
    cleaned = _WHITESPACE_RE.sub(" ", text).strip()
    return cleaned or None


class YahooNewsConnector(BaseConnector):
    PLATFORM = "yahoonews"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        stripped = keyword.strip()
        if not stripped:
            return []

        # Only dated RSS/search-feed results are safe to show. Yahoo search HTML
        # and direct Jina mirrors omit trustworthy publication dates.
        return await self._fetch_gnews_rss(stripped)

    async def _fetch_gnews_rss(self, keyword: str) -> list[SourceItemCreate]:
        """Fallback: Google News RSS filtered to news.yahoo.co.jp."""
        encoded = quote(f"{keyword} site:news.yahoo.co.jp")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        # Google News is unreachable directly from Render's outbound IP (see
        # CLAUDE.md) and the Cloudflare Worker proxy is now also blocked by
        # Google from Cloudflare's IP ranges, so neither is worth the timeout
        # budget: go straight to the jina.ai reader proxy, then Bing.
        items = await self._fetch_gnews_jina(keyword, url)
        if items:
            return items
        return await self._fetch_bing_news(keyword)

    async def _fetch_gnews_jina(self, keyword: str, google_news_url: str) -> list[SourceItemCreate]:
        proxy_url = "https://r.jina.ai/http://" + google_news_url.replace("https://", "")
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(proxy_url)
                if not resp.is_success:
                    log.warning("YahooNews Google News Jina fallback returned status %d", resp.status_code)
                    return []
        except Exception as exc:
            log.warning("YahooNews Google News Jina fallback error: %s", exc)
            return []

        return build_google_news_jina_items(resp.text, keyword, platform=self.PLATFORM)

    async def _fetch_bing_news(self, keyword: str) -> list[SourceItemCreate]:
        query = f"{keyword} site:news.yahoo.co.jp"
        url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&mkt=ja-JP"
        source = "bing_news_proxy"
        content = await fetch_search_rss_via_proxy(query, target="bing")
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
            log.warning("YahooNews Bing News fallback error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries[:25]:
            title = (entry.get("title") or "").strip()
            link = entry.get("link", "")
            item_id = entry.get("id") or link
            if not link or not title or item_id in seen:
                continue
            if not title_contains_keyword(keyword, title):
                continue
            published = parse_feed_date(entry)
            if published is None:
                continue
            seen.add(item_id)
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=published,
                    media_type="article",
                    title=title,
                    content_text=entry.get("summary") or None,
                    raw_payload={"keyword": keyword, "source": source, "date_parsed": True},
                )
            )
        return items
