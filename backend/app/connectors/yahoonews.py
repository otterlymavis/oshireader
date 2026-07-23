from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus, urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    GOOGLE_NEWS_HEADERS,
    SourceItemCreate,
    contains_keyword,
    fetch_search_rss_via_proxy,
    parse_feed_date,
    parse_google_news_markdown,
    title_contains_keyword,
)

log = logging.getLogger(__name__)

_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_WHITESPACE_RE = re.compile(r"\s+")
_JINA_ARTICLE_RE = re.compile(
    r"^\s*\d+\.\s+\[(.+?)\]\((https://news\.yahoo\.co\.jp/articles/([A-Za-z0-9]+))\)",
    re.M | re.S,
)
_ARTICLE_ID_RE = re.compile(r"/articles/([A-Za-z0-9]+)")


def _clean_markdown_title(value: str) -> str:
    value = _MD_IMAGE_RE.sub("", value)
    value = value.replace("_", "")
    return _WHITESPACE_RE.sub(" ", value).strip()


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

        # RSS first — it carries real publish dates; Jina only as fallback (no dates available)
        items = await self._fetch_gnews_rss(stripped)
        if not items:
            items = await self._fetch_direct_search(stripped)
        if not items:
            items = await self._fetch_jina(stripped)
        return items

    async def _fetch_direct_search(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(keyword)
        url = f"https://news.yahoo.co.jp/search?p={encoded}"
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers=GOOGLE_NEWS_HEADERS,
            ) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("YahooNews direct search returned status %d", resp.status_code)
                    return []
        except Exception as exc:
            log.warning("YahooNews direct search error: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for anchor in soup.select('a[href*="/articles/"]'):
            href = anchor.get("href") or ""
            article_url = urljoin("https://news.yahoo.co.jp", href)
            id_match = _ARTICLE_ID_RE.search(article_url)
            if not id_match:
                continue
            item_id = id_match.group(1)
            if item_id in seen:
                continue
            title = _clean_markdown_title(anchor.get_text(" ", strip=True))
            if not title or not title_contains_keyword(keyword, title):
                continue
            seen.add(item_id)
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=article_url,
                    published_at=datetime.now(timezone.utc),
                    media_type="article",
                    title=title,
                    raw_payload={"keyword": keyword, "source": "direct_search", "date_parsed": False},
                )
            )
            if len(items) >= 25:
                break
        return items

    async def _fetch_jina(self, keyword: str) -> list[SourceItemCreate]:
        """Fallback: r.jina.ai proxy returns Yahoo News results as markdown (no publish dates)."""
        encoded = quote(keyword)
        url = f"https://r.jina.ai/https://news.yahoo.co.jp/search?p={encoded}"
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("YahooNews jina mirror returned status %d", resp.status_code)
                    return []
        except Exception as exc:
            log.warning("YahooNews jina fetch error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for m in _JINA_ARTICLE_RE.finditer(resp.text):
            title = _clean_markdown_title(m.group(1))
            article_url = m.group(2)
            item_id = m.group(3)
            if not title or item_id in seen:
                continue
            if not contains_keyword(keyword, title):
                continue
            seen.add(item_id)
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=article_url,
                    published_at=datetime.now(timezone.utc),
                    media_type="article",
                    title=title,
                    raw_payload={"keyword": keyword, "source": "jina", "date_parsed": False},
                )
            )
            if len(items) >= 25:
                break
        return items

    async def _fetch_gnews_rss(self, keyword: str) -> list[SourceItemCreate]:
        """Fallback: Google News RSS filtered to news.yahoo.co.jp."""
        encoded = quote(f"{keyword} site:news.yahoo.co.jp")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        feed = None
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("YahooNews Google News fallback returned status %d", resp.status_code)
                else:
                    feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("YahooNews Google News fallback error: %s", exc)

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in (feed.entries if feed else [])[:25]:
            link = entry.get("link", "")
            if not link:
                continue
            item_id = entry.get("id") or link
            if item_id in seen:
                continue
            seen.add(item_id)
            title = (entry.get("title") or "").strip()
            summary = _clean_html_summary(entry.get("summary"))
            if not title:
                continue
            if not title_contains_keyword(keyword, title):
                continue
            published = parse_feed_date(entry)
            if published is None:
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=published,
                    media_type="article",
                    title=title,
                    content_text=summary,
                    raw_payload={"keyword": keyword, "source": "google_news"},
                )
            )
        if items:
            return items
        items = await self._fetch_gnews_jina(keyword, url)
        if items:
            return items
        items = await self._fetch_gnews_proxy(keyword)
        if items:
            return items
        return await self._fetch_bing_news(keyword)

    async def _fetch_gnews_jina(self, keyword: str, google_news_url: str) -> list[SourceItemCreate]:
        proxy_url = "https://r.jina.ai/http://" + google_news_url.replace("https://", "")
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(proxy_url)
                if not resp.is_success:
                    log.warning("YahooNews Google News Jina fallback returned status %d", resp.status_code)
                    return []
        except Exception as exc:
            log.warning("YahooNews Google News Jina fallback error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        for entry in parse_google_news_markdown(resp.text)[:25]:
            title = entry["title"]
            if not title_contains_keyword(keyword, title):
                continue
            published = entry.get("published_at")
            if published is None:
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=entry["url"],
                    url=entry["url"],
                    published_at=published,
                    media_type="article",
                    title=title,
                    content_text=None,
                    raw_payload={"keyword": keyword, "source": "google_news_jina"},
                )
            )
        return items

    async def _fetch_gnews_proxy(self, keyword: str) -> list[SourceItemCreate]:
        content = await fetch_search_rss_via_proxy(f"{keyword} site:news.yahoo.co.jp", target="google")
        if not content:
            return []
        feed = await asyncio.to_thread(feedparser.parse, content)
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
                    raw_payload={"keyword": keyword, "source": "google_news_proxy"},
                )
            )
        return items

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
                    raw_payload={"keyword": keyword, "source": source},
                )
            )
        return items
