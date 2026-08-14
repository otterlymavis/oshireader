from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

import feedparser

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    fetch_google_news_direct,
    fetch_search_rss_via_proxy,
    first_nonempty_result,
    is_recent_search_result,
    parse_feed_document,
    SourceUnavailableError,
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

        query = f"{keyword} site:oricon.co.jp"
        google_url = (
            f"https://news.google.com/rss/search?q={quote(query)}"
            "&hl=ja&gl=JP&ceid=JP%3Aja"
        )
        return await first_nonempty_result(
            self._fetch_source(google_url, keyword, "google_news_direct", direct=True),
            self._fetch_source(query, keyword, "bing_news_proxy", direct=False),
        )

    async def _fetch_source(
        self,
        query_or_url: str,
        keyword: str,
        source: str,
        *,
        direct: bool,
    ) -> list[SourceItemCreate]:
        content = (
            await fetch_google_news_direct(query_or_url)
            if direct
            else await fetch_search_rss_via_proxy(query_or_url, target="bing")
        )
        if not content:
            raise SourceUnavailableError(f"{source} unavailable")
        feed = await parse_feed_document(content)
        return await self._items_from_feed(feed, keyword, source)

    async def _items_from_feed(
        self,
        feed,
        keyword: str,
        source: str,
    ) -> list[SourceItemCreate]:
        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in (feed.entries if feed else []):
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
            if published is None:
                continue
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
            if len(items) >= 20:
                break

        return items
