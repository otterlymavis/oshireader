from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote, quote_plus

import feedparser
import httpx

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    GOOGLE_NEWS_HEADERS,
    SourceUnavailableError,
    SourceItemCreate,
    build_google_news_jina_items,
    build_google_news_public_proxy_items,
    clean_html_text,
    fetch_google_news_via_public_proxy,
    fetch_search_rss_via_proxy,
    is_usable_jina_reader_response,
    jina_reader_headers,
    parse_feed_document,
    parse_feed_date,
    race_jina_and_public_proxy,
    title_contains_keyword,
)
from app.source_health import record_jina_result

log = logging.getLogger(__name__)


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
        # budget: go straight to the jina.ai reader proxy and a second,
        # independent public proxy (see race_jina_and_public_proxy), then Bing.
        google_chain_succeeded = True
        try:
            items = await race_jina_and_public_proxy(
                self._fetch_gnews_jina(keyword, url),
                self._fetch_gnews_public_proxy(keyword, url),
            )
        except SourceUnavailableError:
            google_chain_succeeded = False
            items = []
        if items:
            return items
        try:
            return await self._fetch_bing_news(keyword)
        except SourceUnavailableError:
            if google_chain_succeeded:
                return []
            raise

    async def _fetch_gnews_jina(self, keyword: str, google_news_url: str) -> tuple[list[SourceItemCreate], bool]:
        proxy_url = "https://r.jina.ai/http://" + google_news_url.replace("https://", "")
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=jina_reader_headers()) as client:
                resp = await client.get(proxy_url)
                if not resp.is_success:
                    log.warning("YahooNews Google News Jina fallback returned status %d", resp.status_code)
                    record_jina_result(self.PLATFORM, succeeded=False, error=f"http_{resp.status_code}")
                    return [], False
        except Exception as exc:
            log.warning("YahooNews Google News Jina fallback error: %s", exc)
            record_jina_result(self.PLATFORM, succeeded=False, error=type(exc).__name__)
            return [], False

        if not is_usable_jina_reader_response(resp.text):
            record_jina_result(self.PLATFORM, succeeded=False, error="invalid_response")
            return [], False
        record_jina_result(self.PLATFORM, succeeded=True)
        items = build_google_news_jina_items(resp.text, keyword, platform=self.PLATFORM)
        return items, True

    async def _fetch_gnews_public_proxy(self, keyword: str, google_news_url: str) -> list[SourceItemCreate]:
        content = await fetch_google_news_via_public_proxy(google_news_url)
        if not content:
            raise SourceUnavailableError("public Google News proxy unavailable")
        return await build_google_news_public_proxy_items(content, keyword, platform=self.PLATFORM)

    async def _fetch_bing_news(self, keyword: str) -> list[SourceItemCreate]:
        query = f"{keyword} site:news.yahoo.co.jp"
        url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&mkt=ja-JP"
        source = "bing_news_proxy"
        content = await fetch_search_rss_via_proxy(query, target="bing")
        try:
            if content:
                try:
                    feed = await parse_feed_document(content)
                except SourceUnavailableError:
                    log.warning("YahooNews Bing News proxy returned a non-feed response")
                    content = None
            if not content:
                source = "bing_news"
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                    resp = await client.get(url)
                    if not resp.is_success:
                        raise SourceUnavailableError("YahooNews Bing News unavailable")
                content = resp.content
                feed = await parse_feed_document(content)
        except Exception as exc:
            log.warning("YahooNews Bing News fallback error: %s", exc)
            raise SourceUnavailableError("YahooNews Bing News unavailable") from exc

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
                    content_text=clean_html_text(entry.get("summary")),
                    raw_payload={"keyword": keyword, "source": source, "date_parsed": True},
                )
            )
        return items
