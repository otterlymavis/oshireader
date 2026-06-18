from __future__ import annotations

import json
import logging
import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import BaseConnector, CollectionMode, SourceItemCreate, contains_keyword, parse_feed_date

log = logging.getLogger(__name__)

_YEAR_RE = re.compile(r'^(\d{4})年')
_MONTH_DAY_RE = re.compile(r'(\d+)月(\d+)日')
_TIME_RE = re.compile(r'(\d+):(\d+)')


def _parse_tver_date(content: dict) -> Optional[datetime]:
    # Unix timestamps (seconds) — most reliable
    for key in ("publishedAt", "publish_start", "deliveryStartAt", "broadcastDate", "airDate"):
        val = content.get(key)
        if isinstance(val, (int, float)) and val > 0:
            try:
                return datetime.fromtimestamp(val, tz=timezone.utc)
            except (OSError, OverflowError):
                pass
        if isinstance(val, str) and val:
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except ValueError:
                pass

    # Parse broadcastDateLabel: "6月5日(金)放送分", "5月29日(金) 18:29", "2021年放送"
    label = content.get("broadcastDateLabel") or ""
    if label:
        now = datetime.now(timezone.utc)
        # Year-only: "2021年放送"
        year_m = _YEAR_RE.match(label)
        if year_m:
            return datetime(int(year_m.group(1)), 6, 1, tzinfo=timezone.utc)
        # Month/day with optional time: "6月5日(金)放送分" or "5月29日(金) 18:29"
        md_m = _MONTH_DAY_RE.search(label)
        if md_m:
            month, day = int(md_m.group(1)), int(md_m.group(2))
            hour, minute = 0, 0
            time_m = _TIME_RE.search(label)
            if time_m:
                hour, minute = int(time_m.group(1)), int(time_m.group(2))
            year = now.year
            try:
                dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
                # If date is more than 7 days in the future it's from last year
                if dt > now + timedelta(days=7):
                    dt = dt.replace(year=year - 1)
                return dt
            except ValueError:
                pass

    return None


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class TVERConnector(BaseConnector):
    PLATFORM = "tver"
    SUPPORTS_MEDIA_FILTER = True

    async def _create_platform_token(self, client: httpx.AsyncClient) -> tuple[Optional[str], Optional[str]]:
        try:
            resp = await client.post(
                "https://platform-api.tver.jp/v2/api/platform_users/browser/create",
                headers={
                    "Origin": "https://tver.jp",
                    "Referer": "https://tver.jp/",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                content="device_type=pc"
            )
            if resp.status_code == 200:
                result = resp.json().get("result", {})
                return result.get("platform_uid"), result.get("platform_token")
        except Exception as exc:
            log.warning("TVer create token failed: %s", exc)
        return None, None

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            uid, token = await self._create_platform_token(client)
            if not uid or not token:
                log.warning("Could not obtain TVer platform tokens")
                return []

            params = {
                "platform_uid": uid,
                "platform_token": token,
                "keyword": keyword,
                "detail": "true",
                "platform": "web",
                "require_talent_data": "true",
                "page": "1",
            }
            
            try:
                resp = await client.get(
                    "https://platform-api.tver.jp/service/api/v1/callKeywordSearch",
                    params=params,
                    headers={
                        "x-tver-platform-type": "web",
                        "x-clientplatform": "web",
                        "Origin": "https://tver.jp",
                        "Referer": "https://tver.jp/",
                    }
                )
                if not resp.is_success:
                    log.warning("TVer search API returned status %d", resp.status_code)
                    return []
            except Exception as exc:
                log.warning("TVer search API request failed: %s", exc)
                return []

            try:
                data = resp.json()
                res = data.get("result", {})
                episodes = []
                
                # Check different possible response formats
                if "episodes" in res and "contents" in res["episodes"]:
                    episodes = res["episodes"]["contents"]
                elif "seriesAndEpisode" in res and "episodes" in res["seriesAndEpisode"] and "contents" in res["seriesAndEpisode"]["episodes"]:
                    episodes = res["seriesAndEpisode"]["episodes"]["contents"]
                elif "contents" in res:
                    episodes = res["contents"]
                elif "rows" in res:
                    episodes = res["rows"]
                else:
                    episodes = data.get("contents") or data.get("rows") or []
            except Exception as exc:
                log.warning("TVer response JSON parse failed: %s", exc)
                return []

            items: list[SourceItemCreate] = []
            for ep in episodes[:25]:
                try:
                    content = ep.get("content") or ep.get("episode") or ep
                    ep_id = content.get("id") or content.get("seriesId") or ep.get("id")
                    if not ep_id:
                        continue
                    
                    title = content.get("title") or content.get("episodeTitle") or content.get("seriesTitle")
                    if not title:
                        continue
                    
                    # Construct URL
                    content_type = str(ep.get("type") or content.get("type") or "").lower()
                    if content_type == "series":
                        url = f"https://tver.jp/series/{ep_id}"
                    elif content_type == "special":
                        url = f"https://tver.jp/specials/{ep_id}"
                    else:
                        url = f"https://tver.jp/episodes/{ep_id}"
                        
                    thumb_raw = content.get("thumbnailUrl") or content.get("thumbnailURL") or content.get("thumbnail_path")
                    thumb = None
                    if thumb_raw:
                        if thumb_raw.startswith("http"):
                            thumb = thumb_raw
                        elif thumb_raw.startswith("/"):
                            thumb = f"https://statics.tver.jp{thumb_raw}"
                        else:
                            thumb = thumb_raw

                    author = content.get("broadcasterName") or content.get("productionProviderName")
                    description = content.get("description") or content.get("episodeDescription")
                    if not contains_keyword(keyword, title, description, author, content.get("seriesTitle")):
                        continue

                    # Try API timestamp fields before falling back to now()
                    published_at = _parse_tver_date(content) or datetime.now(timezone.utc)

                    items.append(
                        SourceItemCreate(
                            platform=self.PLATFORM,
                            item_id=str(ep_id),
                            url=url,
                            published_at=published_at,
                            media_type="video",
                            title=str(title),
                            thumbnail_url=thumb,
                            author=author,
                            content_text=description,
                            raw_payload=ep,
                        )
                    )
                except Exception as exc:
                    log.warning("Error parsing TVer episode item: %s", exc)
                    
            if items:
                return items
            return await self._fetch_indexed_history(keyword)

    async def _fetch_indexed_history(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(f"{keyword} site:tver.jp when:10y")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("TVer indexed-history fallback failed: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries[:25]:
            title = (entry.get("title") or "").strip()
            link = entry.get("link", "")
            item_id = entry.get("id") or link
            if not link or not contains_keyword(keyword, title) or item_id in seen:
                continue
            seen.add(item_id)
            items.append(SourceItemCreate(
                platform=self.PLATFORM,
                item_id=item_id,
                url=link,
                published_at=parse_feed_date(entry),
                media_type="video",
                title=title,
                content_text=entry.get("summary") or None,
                raw_payload={"source": "google_news_history", "keyword": keyword},
            ))
        return items
