from datetime import datetime

import httpx

from app.connectors.base import BaseConnector, SourceItemCreate


class YouTubeConnector(BaseConnector):
    PLATFORM = "youtube"
    SUPPORTS_MEDIA_FILTER = True

    _SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if not self.api_key:
            return []

        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "order": "date",
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
