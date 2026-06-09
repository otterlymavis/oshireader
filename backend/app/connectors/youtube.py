import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import BaseConnector, CollectionMode, SourceItemCreate, parse_feed_date

log = logging.getLogger(__name__)


def _parse_youtube_relative(text: str) -> Optional[datetime]:
    """Convert YouTube relative timestamps ('2 days ago', '3ヶ月前') to UTC datetimes."""
    if not text:
        return None
    now = datetime.now(timezone.utc)
    m = re.search(r'(\d+)[\s 　]*(second|minute|hour|day|week|month|year|秒|分|時間|日|週間?|週|ヶ月|か月|年)', text.lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit in ('second', '秒'):      return now - timedelta(seconds=n)
    if unit in ('minute', '分'):      return now - timedelta(minutes=n)
    if unit in ('hour', '時間'):      return now - timedelta(hours=n)
    if unit in ('day', '日'):         return now - timedelta(days=n)
    if unit in ('week', '週', '週間'): return now - timedelta(weeks=n)
    if unit in ('month', 'ヶ月', 'か月'): return now - timedelta(days=n * 30)
    return now - timedelta(days=n * 365)  # unit must be 'year' or '年' — exhaustive


class YouTubeConnector(BaseConnector):
    PLATFORM = "youtube"
    SUPPORTS_MEDIA_FILTER = True

    _SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def _fetch_api(self, keyword: str) -> list[SourceItemCreate]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "order": "date",
            "publishedAfter": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "maxResults": 25,
            "key": self.api_key,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(self._SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        items: list[SourceItemCreate] = []
        for raw in data.get("items", []):
            vid_id = raw["id"].get("videoId")
            if not vid_id:
                continue
            snippet = raw["snippet"]
            published = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
            thumb = snippet.get("thumbnails", {}).get("medium", {}).get("url")
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=vid_id,
                    url=f"https://www.youtube.com/watch?v={vid_id}",
                    published_at=published,
                    media_type="video",
                    author=snippet.get("channelTitle"),
                    title=snippet.get("title"),
                    content_text=snippet.get("description"),
                    thumbnail_url=thumb,
                    raw_payload=raw,
                )
            )

        return items

    async def _fetch_scrape(self, keyword: str) -> list[SourceItemCreate]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ja,ja-JP;q=0.9,en;q=0.8",
        }
        url = f"https://www.youtube.com/results?search_query={keyword}"

        async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
            resp = await client.get(url)
            if not resp.is_success:
                log.warning("YouTube search scrape failed with status code %d", resp.status_code)
                return []

        # Find ytInitialData JSON inside HTML
        m = re.search(r"ytInitialData\s*=\s*({.+?});", resp.text, re.S)
        if not m:
            log.warning("ytInitialData not found in YouTube search scrape response")
            return []

        try:
            data = json.loads(m.group(1))
            contents = (
                data.get("contents", {})
                .get("twoColumnSearchResultsRenderer", {})
                .get("primaryContents", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
        except Exception as e:
            log.warning("Failed to parse ytInitialData JSON: %s", e)
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        items: list[SourceItemCreate] = []
        for content in contents:
            item_section = content.get("itemSectionRenderer", {})
            for item in item_section.get("contents", []):
                if "videoRenderer" in item:
                    vr = item["videoRenderer"]
                    vid_id = vr.get("videoId")
                    if not vid_id:
                        continue

                    title = vr.get("title", {}).get("runs", [{}])[0].get("text")
                    channel = vr.get("ownerText", {}).get("runs", [{}])[0].get("text")

                    desc = ""
                    snippets = vr.get("detailedMetadataSnippets", [])
                    if snippets:
                        desc = snippets[0].get("snippetText", {}).get("runs", [{}])[0].get("text", "")

                    thumb = None
                    thumbnails = vr.get("thumbnail", {}).get("thumbnails", [])
                    if thumbnails:
                        thumb = thumbnails[0].get("url")

                    rel_text = vr.get("publishedTimeText", {}).get("simpleText", "")
                    published_at = _parse_youtube_relative(rel_text) or datetime.now(timezone.utc)

                    if published_at < cutoff:
                        continue

                    items.append(
                        SourceItemCreate(
                            platform=self.PLATFORM,
                            item_id=str(vid_id),
                            url=f"https://www.youtube.com/watch?v={vid_id}",
                            published_at=published_at,
                            media_type="video",
                            author=str(channel) if channel else None,
                            title=str(title) if title else None,
                            content_text=str(desc) if desc else None,
                            thumbnail_url=thumb,
                            raw_payload=item,
                        )
                    )

        return items

    async def _fetch_gnews(self, keyword: str) -> list[SourceItemCreate]:
        """Last-resort fallback: Google News RSS filtered to youtube.com.
        Works from cloud IPs where direct YouTube scraping is blocked."""
        encoded = quote(f"{keyword} site:youtube.com")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("YouTube Google News fallback returned status %d", resp.status_code)
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("YouTube Google News fallback error: %s", exc)
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
                    media_type="video",
                    title=title,
                    content_text=entry.get("summary") or None,
                    thumbnail_url=None,
                    raw_payload={"source": "google_news", "keyword": keyword},
                )
            )
        return items

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        # Always try API first if credential is provided
        if self.api_key:
            try:
                return await self._fetch_api(keyword)
            except Exception as e:
                log.warning("YouTube API fetch failed, falling back to scrape. Error: %s", e)

        # Scrape fallback
        try:
            items = await self._fetch_scrape(keyword)
            if items:
                return items
        except Exception as e:
            log.warning("YouTube scrape fetch failed. Error: %s", e)

        # Google News fallback — works from cloud IPs when direct scraping is blocked
        return await self._fetch_gnews(keyword)
