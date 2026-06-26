from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    SourceItemCreate,
    parse_feed_date,
)

log = logging.getLogger(__name__)


class NoteConnector(BaseConnector):
    PLATFORM = "note"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        stripped = keyword.strip()
        if not stripped:
            return []

        return await self._fetch_hashtag_rss(stripped)

    async def _fetch_hashtag_rss(self, keyword: str) -> list[SourceItemCreate]:
        """Fallback: hashtag RSS — only finds notes tagged with exactly this keyword."""
        encoded = quote(keyword)
        url = f"https://note.com/hashtag/{encoded}/rss"
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("Note hashtag RSS returned status=%d", resp.status_code)
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("Note hashtag RSS failed: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        for entry in feed.entries[:25]:
            link = entry.get("link", "")
            if not link:
                continue
            title = entry.get("title")
            summary = entry.get("summary") or ""
            author = entry.get("author")
            item_id = entry.get("id") or link.rstrip("/").split("/")[-1] or link
            thumb = None
            for enc in entry.get("enclosures", []):
                if enc.get("type", "").startswith("image"):
                    thumb = enc.get("href")
                    break
            media = entry.get("media_thumbnail") or entry.get("media_content") or []
            if not thumb and media:
                thumb = media[0].get("url")
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=str(item_id),
                    url=link,
                    published_at=parse_feed_date(entry),
                    media_type="article",
                    author=author,
                    title=title,
                    content_text=summary or None,
                    thumbnail_url=thumb,
                    raw_payload={"feed_url": url, "matched_hashtag": keyword},
                )
            )
        return items
