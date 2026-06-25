from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import httpx

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

_VID_ID_RE = re.compile(r'/watch/([a-zA-Z0-9]+)')
_THUMB_RE = re.compile(r'<img[^>]+src="(https://[^"]+nicovideo\.jp[^"]+)"', re.I)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
}


class NicoNicoConnector(BaseConnector):
    PLATFORM = "niconico"
    SUPPORTS_MEDIA_FILTER = True

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        items = await self._fetch_rss(keyword)
        if items is None:
            items = await self._fetch_tag_rss(keyword)
        if not items:
            items = await self._fetch_gnews(keyword)
        return items

    async def _fetch_rss(self, keyword: str) -> list[SourceItemCreate] | None:
        """NicoNico keyword search RSS feed (newest first)."""
        encoded = quote(keyword)
        url = f"https://www.nicovideo.jp/search/{encoded}?sort=f&order=d&rss=2.0&lang=ja-jp"
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=_HEADERS) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.debug("NicoNico search RSS returned status %d", resp.status_code)
                    return None
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.debug("NicoNico search RSS fetch error: %s", exc)
            return None

        return self._parse_feed(feed, keyword, "rss_search")

    async def _fetch_tag_rss(self, keyword: str) -> list[SourceItemCreate] | None:
        """NicoNico tag RSS — finds videos where keyword is an exact tag."""
        encoded = quote(keyword)
        url = f"https://www.nicovideo.jp/tag/{encoded}?sort=f&order=d&rss=2.0&lang=ja-jp"
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=_HEADERS) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.debug("NicoNico tag RSS returned status %d", resp.status_code)
                    return None
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.debug("NicoNico tag RSS fetch error: %s", exc)
            return None

        return self._parse_feed(feed, keyword, "rss_tag")

    def _parse_feed(self, feed: feedparser.FeedParserDict, keyword: str, source: str) -> list[SourceItemCreate]:
        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries[:25]:
            link = entry.get("link", "")
            if not link:
                continue

            vid_m = _VID_ID_RE.search(link)
            vid_id = vid_m.group(1) if vid_m else (entry.get("id") or link)

            if vid_id in seen:
                continue
            seen.add(vid_id)

            title = (entry.get("title") or "").strip()
            if not title:
                continue
            summary = entry.get("summary") or ""
            if not contains_keyword(keyword, title, summary, entry.get("author")):
                continue

            # Thumbnail: from media:thumbnail, media:content, or description HTML
            thumb: str | None = None
            for attr in ("media_thumbnail", "media_content"):
                media = entry.get(attr) or []
                if media and isinstance(media, list):
                    thumb = media[0].get("url")
                    break
            if not thumb:
                m = _THUMB_RE.search(summary)
                if m:
                    thumb = m.group(1)

            author: str | None = None
            if entry.get("authors"):
                author = entry.authors[0].get("name")
            elif entry.get("author"):
                author = entry.get("author")

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=str(vid_id),
                    url=f"https://www.nicovideo.jp/watch/{vid_id}" if vid_m else link,
                    published_at=parse_feed_date(entry),
                    media_type="video",
                    author=author,
                    title=title,
                    content_text=None,
                    thumbnail_url=thumb,
                    raw_payload={"source": source, "keyword": keyword},
                )
            )
        return items

    async def _fetch_gnews(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(f"{keyword} site:nicovideo.jp")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        feed = None
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("NicoNico Google News fallback returned status %d", resp.status_code)
                else:
                    feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("NicoNico Google News fallback error: %s", exc)

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
            summary = entry.get("summary") or ""
            if not title:
                continue
            if not title_contains_keyword(keyword, title):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=parse_feed_date(entry),
                    media_type="video",
                    title=title,
                    content_text=summary or None,
                    thumbnail_url=None,
                    raw_payload={"source": "google_news", "keyword": keyword},
                )
            )
        if items:
            return items
        items = await self._fetch_gnews_jina(keyword, url)
        if items:
            return items
        return await self._fetch_gnews_proxy(keyword)

    async def _fetch_gnews_jina(self, keyword: str, google_news_url: str) -> list[SourceItemCreate]:
        proxy_url = "https://r.jina.ai/http://" + google_news_url.replace("https://", "")
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(proxy_url)
                if not resp.is_success:
                    return []
        except Exception as exc:
            log.warning("NicoNico Google News Jina fallback error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        for entry in parse_google_news_markdown(resp.text)[:25]:
            title = entry["title"]
            if not title_contains_keyword(keyword, title):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=entry["url"],
                    url=entry["url"],
                    published_at=entry["published_at"],
                    media_type="video",
                    title=title,
                    content_text=None,
                    thumbnail_url=None,
                    raw_payload={"source": "google_news_jina", "keyword": keyword},
                )
            )
        return items

    async def _fetch_gnews_proxy(self, keyword: str) -> list[SourceItemCreate]:
        query = f"{keyword} site:nicovideo.jp"
        for target, source in (("google", "google_news_proxy"), ("bing", "bing_news_proxy")):
            content = await fetch_search_rss_via_proxy(query, target=target)
            if not content:
                continue
            feed = await asyncio.to_thread(feedparser.parse, content)
            items: list[SourceItemCreate] = []
            seen: set[str] = set()
            for entry in feed.entries[:25]:
                link = entry.get("link", "")
                item_id = entry.get("id") or link
                title = (entry.get("title") or "").strip()
                if not link or not title or item_id in seen:
                    continue
                if not title_contains_keyword(keyword, title):
                    continue
                seen.add(item_id)
                items.append(
                    SourceItemCreate(
                        platform=self.PLATFORM,
                        item_id=item_id,
                        url=link,
                        published_at=parse_feed_date(entry),
                        media_type="video",
                        title=title,
                        content_text=entry.get("summary") or None,
                        thumbnail_url=None,
                        raw_payload={"source": source, "keyword": keyword},
                    )
                )
            if items:
                return items
        return []
