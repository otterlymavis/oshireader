from __future__ import annotations

import asyncio
import logging
import re

import feedparser

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    fetch_search_rss_via_proxy,
    SourceItemCreate,
    parse_feed_date,
    title_contains_keyword,
)

log = logging.getLogger(__name__)

_SUFFIX_RE = re.compile(r"\s*[-|]\s*モデルプレス\s*$", re.I)


def _clean_title(value: str) -> str:
    return _SUFFIX_RE.sub("", value).strip()


class ModelPressConnector(BaseConnector):
    PLATFORM = "mdpr"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        # Google News is unreachable directly from Render's outbound IP (see
        # CLAUDE.md) and the Cloudflare Worker proxy is now also blocked by
        # Google from Cloudflare's IP ranges, so go straight to Bing, the
        # only fallback here that actually works.
        query = f"{keyword} site:mdpr.jp"
        content = await fetch_search_rss_via_proxy(query, target="bing")
        if not content:
            return []
        feed = await asyncio.to_thread(feedparser.parse, content)
        return await self._items_from_feed(feed, keyword, "bing_news_proxy")

    async def _items_from_feed(
        self,
        feed,
        keyword: str,
        source: str,
    ) -> list[SourceItemCreate]:
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
            title = _clean_title(entry.get("title", ""))
            summary = entry.get("summary") or ""
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
                    content_text=summary or None,
                    thumbnail_url=None,
                    raw_payload={"source": source, "keyword": keyword},
                )
            )

        return items
