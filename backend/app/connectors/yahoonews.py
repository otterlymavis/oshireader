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


def _clean_markdown_title(value: str) -> str:
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
    value = value.replace("_", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


class YahooNewsConnector(BaseConnector):
    PLATFORM = "yahoonews"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if mode == "media_only":
            return []

        stripped = keyword.strip()
        if not stripped:
            return []

        # RSS first — it carries real publish dates; Jina only as fallback (no dates available)
        items = await self._fetch_gnews_rss(stripped)
        if not items:
            items = await self._fetch_jina(stripped)
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
        pattern = re.compile(
            r"^\s*\d+\.\s+\[(.+?)\]\((https://news\.yahoo\.co\.jp/articles/([A-Za-z0-9]+))\)",
            re.M | re.S,
        )
        for m in pattern.finditer(resp.text):
            title = _clean_markdown_title(m.group(1))
            article_url = m.group(2)
            item_id = m.group(3)
            if not title or item_id in seen:
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
                    raw_payload={"keyword": keyword, "source": "jina"},
                )
            )
            if len(items) >= 25:
                break
        return items

    async def _fetch_gnews_rss(self, keyword: str) -> list[SourceItemCreate]:
        """Fallback: Google News RSS filtered to news.yahoo.co.jp."""
        encoded = quote(f"{keyword} site:news.yahoo.co.jp")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("YahooNews Google News fallback returned status %d", resp.status_code)
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("YahooNews Google News fallback error: %s", exc)
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
