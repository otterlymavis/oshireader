from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from app.connectors.base import BaseConnector, SourceItemCreate

log = logging.getLogger(__name__)


class NicoNicoConnector(BaseConnector):
    PLATFORM = "niconico"
    SUPPORTS_MEDIA_FILTER = True

    _SEARCH_URL = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        params = {
            "q": keyword,
            "targets": "title,description,tags",
            "fields": "contentId,title,description,userId,channelId,startTime,thumbnailUrl",
            "_sort": "-startTime",
            "_limit": "25",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self._SEARCH_URL, params=params, headers={"Accept": "application/json"})
                if not resp.is_success:
                    log.debug("NicoNico search returned status %d", resp.status_code)
                    return []
                data = resp.json()
        except Exception as exc:
            log.debug("NicoNico fetch error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        for raw in data.get("data", []):
            content_id = raw.get("contentId")
            if not content_id:
                continue

            published = datetime.now(timezone.utc)
            start_time = raw.get("startTime")
            if start_time:
                try:
                    published = datetime.fromisoformat(str(start_time))
                except ValueError:
                    pass

            description = raw.get("description")
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=str(content_id),
                    url=f"https://www.nicovideo.jp/watch/{content_id}",
                    published_at=published,
                    media_type="video",
                    author=str(raw.get("userId") or raw.get("channelId") or "") or None,
                    title=raw.get("title"),
                    content_text=description,
                    thumbnail_url=raw.get("thumbnailUrl"),
                    raw_payload=raw,
                )
            )

        return items
