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
    SourceItemCreate,
    contains_keyword,
    is_recent_search_result,
    parse_feed_date,
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
    # Render's outbound IP gets a permanent 403 from NicoNico (see CLAUDE.md);
    # real matches for this platform arrive via a separate client-side scrape,
    # so this connector's backend poll result would just show as permanently
    # "failed" without reflecting whether the source actually works.
    REPORTS_STATUS_TO_CLIENT = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        # Both feeds are unconditionally unreachable from Render's IP (see
        # CLAUDE.md), so run them concurrently rather than sequentially —
        # otherwise every poll pays up to the sum of both feeds' timeouts
        # before failing instead of just the slower of the two.
        items, tag_items = await asyncio.gather(
            self._fetch_rss(keyword),
            self._fetch_tag_rss(keyword),
        )
        if items is None:
            items = tag_items
        if items is None:
            # Both of NicoNico's own feeds failed to fetch (e.g. the 403 Render's
            # outbound IP gets — see CLAUDE.md). Raise rather than returning []
            # so the scheduler's existing failure path records this as a real
            # failure instead of a misleading "success, zero results" — actual
            # matches for this source arrive via the separate client-side scrape.
            raise RuntimeError(f"NicoNico feeds unavailable for keyword {keyword!r}")
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

            title = (entry.get("title") or "").strip()
            if not title:
                continue
            summary = entry.get("summary") or ""
            if not contains_keyword(keyword, title, summary, entry.get("author")):
                continue
            published = parse_feed_date(entry)
            if published is None:
                continue
            if not is_recent_search_result(published):
                continue
            seen.add(vid_id)

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
                    published_at=published,
                    media_type="video",
                    author=author,
                    title=title,
                    content_text=None,
                    thumbnail_url=thumb,
                    raw_payload={"source": source, "keyword": keyword},
                )
            )
        return items
