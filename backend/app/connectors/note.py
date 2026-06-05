from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import BaseConnector, SourceItemCreate, parse_feed_date

log = logging.getLogger(__name__)

_SEARCH_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; OshiReader/1.0)",
}


class NoteConnector(BaseConnector):
    PLATFORM = "note"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if mode == "media_only":
            return []

        stripped = keyword.strip()
        if not stripped:
            return []

        items = await self._fetch_search_api(stripped)
        if not items:
            items = await self._fetch_hashtag_rss(stripped)
        return items

    async def _fetch_search_api(self, keyword: str) -> list[SourceItemCreate]:
        """Use note.com public search API — finds articles mentioning the keyword in content."""
        params = {"context": "note", "q": keyword, "size": "25", "start": "0"}
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=_SEARCH_HEADERS) as client:
                resp = await client.get("https://note.com/api/v2/searches", params=params)
                if not resp.is_success:
                    log.warning("Note search API returned status=%d", resp.status_code)
                    return []
            data = resp.json()
        except Exception as exc:
            log.warning("Note search API failed: %s", exc)
            return []

        notes = (
            data.get("data", {}).get("notes", {}).get("contents")
            or data.get("data", {}).get("notes")
            or []
        )
        if not isinstance(notes, list):
            return []

        items: list[SourceItemCreate] = []
        for note in notes[:25]:
            note_key = note.get("key") or str(note.get("id", ""))
            if not note_key:
                continue

            user = note.get("user") or {}
            urlname = user.get("urlname", "")
            note_url = note.get("noteUrl") or (
                f"https://note.com/{urlname}/n/{note_key}" if urlname else f"https://note.com/n/{note_key}"
            )

            published = datetime.now(timezone.utc)
            publish_at = note.get("publishAt") or note.get("publish_at") or ""
            if publish_at:
                try:
                    parsed = datetime.fromisoformat(str(publish_at).replace("Z", "+00:00"))
                    published = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pass

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=note_key,
                    url=note_url,
                    published_at=published,
                    media_type="article",
                    author=user.get("name") or urlname or None,
                    title=note.get("name") or note.get("title"),
                    content_text=note.get("body") or None,
                    thumbnail_url=note.get("eyecatch") or None,
                    raw_payload=note,
                )
            )
        return items

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
                    author=entry.get("author"),
                    title=entry.get("title"),
                    content_text=entry.get("summary") or None,
                    thumbnail_url=thumb,
                    raw_payload={"feed_url": url},
                )
            )
        return items
