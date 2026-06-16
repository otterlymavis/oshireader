from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import BaseConnector, CollectionMode, SourceItemCreate, parse_feed_date

log = logging.getLogger(__name__)


class SmartNewsConnector(BaseConnector):
    PLATFORM = "smartnews"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []
        return await self._fetch_gnews(keyword)

    async def _fetch_gnews(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(f"{keyword} site:smartnews.com")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("SmartNews Google News fallback returned %d", resp.status_code)
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("SmartNews Google News error: %s", exc)
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
                    media_type="article",
                    title=title,
                    content_text=entry.get("summary") or None,
                    raw_payload={"keyword": keyword, "source": "google_news"},
                )
            )
        return items
