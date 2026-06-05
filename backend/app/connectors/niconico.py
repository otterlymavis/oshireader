from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import BaseConnector, SourceItemCreate, parse_feed_date

log = logging.getLogger(__name__)


class NicoNicoConnector(BaseConnector):
    PLATFORM = "niconico"
    SUPPORTS_MEDIA_FILTER = True

    _SEARCH_URL = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        items = await self._fetch_api(keyword)
        if not items:
            items = await self._fetch_gnews(keyword)
        return items

    async def _fetch_api(self, keyword: str) -> list[SourceItemCreate]:
        params = {
            "q": keyword,
            "targets": "title,description,tags",
            "fields": "contentId,title,description,userId,channelId,startTime,thumbnailUrl",
            "_sort": "-startTime",
            "_limit": "25",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self._SEARCH_URL, params=params, headers={"Accept": "application/json"})
                if not resp.is_success:
                    log.debug("NicoNico snapshot API returned status %d", resp.status_code)
                    return []
                data = resp.json()
        except Exception as exc:
            log.debug("NicoNico API fetch error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        for raw in data.get("data", []):
            content_id = raw.get("contentId")
            if not content_id:
                continue
            published = datetime.now(timezone.utc)
            start_time = raw.get("startTime")
            if start_time:
                try:
                    published = datetime.fromisoformat(str(start_time))
                except ValueError:
                    pass
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=str(content_id),
                    url=f"https://www.nicovideo.jp/watch/{content_id}",
                    published_at=published,
                    media_type="video",
                    author=str(raw.get("userId") or raw.get("channelId") or "") or None,
                    title=raw.get("title"),
                    content_text=raw.get("description"),
                    thumbnail_url=raw.get("thumbnailUrl"),
                    raw_payload=raw,
                )
            )
        return items

    async def _fetch_gnews(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(f"{keyword} site:nicovideo.jp")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.debug("NicoNico Google News fallback error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries[:25]:
            link = entry.get("link", "")
            if not link:
                continue
            item_id = entry.get("id") or link
            if item_id in seen:
                continue
            seen.add(item_id)
            title = (entry.get("title") or "").strip()
            if not title:
                continue
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
                    raw_payload={"source": "google_news", "keyword": keyword},
                )
            )
        return items
