from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.connectors.base import BaseConnector, SourceItemCreate

log = logging.getLogger(__name__)

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
            log.debug("TVer create token failed: %s", exc)
        return None, None

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            uid, token = await self._create_platform_token(client)
            if not uid or not token:
                log.debug("Could not obtain TVer platform tokens")
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
                    log.debug("TVer search API returned status %d", resp.status_code)
                    return []
            except Exception as exc:
                log.debug("TVer search API request failed: %s", exc)
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
                log.debug("TVer response JSON parse failed: %s", exc)
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
                    log.debug("Error parsing TVer episode item: %s", exc)
                    
            return items
