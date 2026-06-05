from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import BaseConnector, SourceItemCreate, parse_feed_date

log = logging.getLogger(__name__)


def _clean_title(value: str) -> str:
    value = re.sub(r"\s*[-|]\s*モデルプレス\s*$", "", value, flags=re.I)
    return value.strip()


class ModelPressConnector(BaseConnector):
    PLATFORM = "mdpr"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if mode == "media_only":
            return []

        encoded = quote(f"{keyword} site:mdpr.jp")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"

        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("MDPR via Google News returned status %d", resp.status_code)
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("MDPR Google News fetch error: %s", exc)
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
            title = _clean_title(entry.get("title", ""))
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
                    thumbnail_url=None,
                    raw_payload={"source": "google_news", "keyword": keyword},
                )
            )

        return items
