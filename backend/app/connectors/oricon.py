from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    fetch_search_rss_via_proxy,
    is_recent_search_result,
    SourceItemCreate,
    parse_feed_date,
    title_contains_keyword,
)

log = logging.getLogger(__name__)

_SUFFIX_RE = re.compile(r"\s*[-|]\s*(ORICON NEWS|オリコンニュース|オリコン)\s*$", re.I)


def _clean_title(value: str) -> str:
    return _SUFFIX_RE.sub("", value).strip()


class OriconConnector(BaseConnector):
    PLATFORM = "oricon"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        encoded = quote(f"{keyword} site:oricon.co.jp")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"

        feed = None
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("Oricon via Google News returned status %d", resp.status_code)
                else:
                    feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("Oricon Google News fetch error: %s", exc)

        items = await self._items_from_feed(feed, keyword, "google_news")
        if items:
            return items

        query = f"{keyword} site:oricon.co.jp"
        for target, source in (("google", "google_news_proxy"), ("bing", "bing_news_proxy")):
            content = await fetch_search_rss_via_proxy(query, target=target)
            if not content:
                continue
            feed = await asyncio.to_thread(feedparser.parse, content)
            items = await self._items_from_feed(feed, keyword, source)
            if items:
                return items
        return []

    async def _items_from_feed(
        self,
        feed,
        keyword: str,
        source: str,
    ) -> list[SourceItemCreate]:
        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in (feed.entries if feed else [])[:20]:
            link = entry.get("link", "")
            if not link:
                continue
            item_id = entry.get("id") or link
            if item_id in seen:
                continue
            title = _clean_title(entry.get("title", ""))
            summary = entry.get("summary") or ""
            if not title:
                continue
            if not title_contains_keyword(keyword, title):
                continue
            published = parse_feed_date(entry)
            if not is_recent_search_result(published):
                continue
            seen.add(item_id)
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=published,
                    media_type="article",
                    author="ORICON NEWS",
                    title=title,
                    content_text=summary or None,
                    thumbnail_url=None,
                    raw_payload={"source": source, "keyword": keyword},
                )
            )

        return items
