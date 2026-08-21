from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html
import logging
import re
from urllib.parse import quote

import feedparser
import httpx

from app.config import settings
from app.connectors.base import (
    SCRAPE_USER_AGENT,
    BaseConnector,
    CollectionMode,
    SourceItemCreate,
    contains_keyword,
    is_recent_search_result,
    mark_date_provenance,
    parse_feed_document,
    parse_feed_date,
)

log = logging.getLogger(__name__)

_VID_ID_RE = re.compile(r'/watch/([a-zA-Z0-9]+)')
_THUMB_RE = re.compile(r'<img[^>]+src="(https://[^"]+nicovideo\.jp[^"]+)"', re.I)

_HEADERS = {
    "User-Agent": SCRAPE_USER_AGENT,
    "Accept-Language": "ja,en;q=0.9",
}


def _route_budgets() -> tuple[float, float]:
    """Reserve cleanup time while scaling routes to the deployed connector budget."""
    total = max(0.03, float(settings.connector_fetch_timeout_seconds))
    reserve = min(1.0, total / 8.0)
    usable = total - reserve
    snapshot = min(5.0, usable * 3.0 / 7.0)
    legacy = usable - snapshot
    return snapshot, legacy


class NicoNicoConnector(BaseConnector):
    PLATFORM = "niconico"
    SUPPORTS_MEDIA_FILTER = True

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        # Snapshot Search is NicoNico's current keyless API. A successful empty
        # response is authoritative; use the legacy feeds only when the API is
        # unavailable or malformed.
        snapshot_budget, legacy_budget = _route_budgets()
        try:
            snapshot_items = await asyncio.wait_for(
                self._fetch_snapshot_search(keyword),
                timeout=snapshot_budget,
            )
        except asyncio.TimeoutError:
            log.debug("NicoNico snapshot search exceeded its route budget")
            snapshot_items = None
        if snapshot_items is not None:
            return snapshot_items

        # Keep the two legacy feeds concurrent and preserve any route that
        # finishes inside the remaining connector budget. Waiting for a hung
        # sibling with gather() would discard a usable fast result when the
        # scheduler's outer deadline expires.
        legacy_tasks = {
            asyncio.create_task(self._fetch_rss(keyword)),
            asyncio.create_task(self._fetch_tag_rss(keyword)),
        }
        done: set[asyncio.Task] = set()
        try:
            done, _ = await asyncio.wait(
                legacy_tasks,
                timeout=legacy_budget,
            )
        finally:
            for task in legacy_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*legacy_tasks, return_exceptions=True)
        feed_results: list[list[SourceItemCreate]] = []
        for task in done:
            try:
                result = task.result()
            except Exception:
                continue
            if result is not None:
                feed_results.append(result)
        merged: list[SourceItemCreate] = []
        seen = set()
        for result in feed_results:
            for item in result:
                if item.item_id not in seen:
                    seen.add(item.item_id)
                    merged.append(item)
        if merged:
            return merged[:25]

        if not feed_results:
            # Every independent NicoNico path failed. Raise so scheduler health
            # records an outage instead of a misleading successful empty check.
            raise RuntimeError(f"NicoNico sources unavailable for keyword {keyword!r}")
        return []

    async def _fetch_snapshot_search(self, keyword: str) -> list[SourceItemCreate] | None:
        url = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"
        params = {
            "q": keyword,
            # Ingestion uses the title as primary text, so constrain the API
            # before its 25-row limit rather than discarding title matches that
            # were crowded out by description/tag-only results.
            "targets": "title",
            "fields": "contentId,title,description,userId,channelId,thumbnailUrl,startTime",
            "_sort": "-startTime",
            "_limit": "25",
            "_context": "OshiReader",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=_HEADERS) as client:
                resp = await client.get(url, params=params)
                if not resp.is_success:
                    log.debug("NicoNico snapshot search returned status %d", resp.status_code)
                    return None
                payload = resp.json()
        except Exception as exc:
            log.debug("NicoNico snapshot search error: %s", exc)
            return None

        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return None

        items: list[SourceItemCreate] = []
        for raw in payload["data"]:
            if not isinstance(raw, dict):
                continue
            video_id = str(raw.get("contentId") or "").strip()
            title = str(raw.get("title") or "").strip()
            description = html.unescape(re.sub(r"<[^>]+>", " ", str(raw.get("description") or "")))
            # Ingestion treats the title as the primary searchable text when a
            # title exists. Apply the same rule here so a keyword mentioned only
            # in a long description does not become a result that ingestion will
            # immediately reject as a mismatch.
            if not video_id or not title or not contains_keyword(keyword, title):
                continue
            try:
                published = datetime.fromisoformat(str(raw.get("startTime") or "").replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                published = mark_date_provenance(published.astimezone(timezone.utc), date_parsed=True)
            except ValueError:
                continue
            if not is_recent_search_result(published):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=video_id,
                    url=f"https://www.nicovideo.jp/watch/{video_id}",
                    published_at=published,
                    media_type="video",
                    title=title,
                    content_text=" ".join(description.split()) or None,
                    thumbnail_url=raw.get("thumbnailUrl") or None,
                    raw_payload={
                        "source": "snapshot_search",
                        "keyword": keyword,
                        "user_id": raw.get("userId"),
                        "channel_id": raw.get("channelId"),
                    },
                )
            )
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
            feed = await parse_feed_document(resp.content)
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
            feed = await parse_feed_document(resp.content)
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
