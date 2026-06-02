import json
import logging
import re
from datetime import datetime, timezone

import httpx

from app.connectors.base import BaseConnector, SourceItemCreate

log = logging.getLogger(__name__)


class YouTubeConnector(BaseConnector):
    PLATFORM = "youtube"
    SUPPORTS_MEDIA_FILTER = True

    _SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def _fetch_api(self, keyword: str) -> list[SourceItemCreate]:
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
        m = re.search(r"ytInitialData\s*=\s*({.+?});", resp.text)
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

                    items.append(
                        SourceItemCreate(
                            platform=self.PLATFORM,
                            item_id=str(vid_id),
                            url=f"https://www.youtube.com/watch?v={vid_id}",
                            published_at=datetime.now(timezone.utc),  # Scrape date fallback
                            media_type="video",
                            author=str(channel) if channel else None,
                            title=str(title) if title else None,
                            content_text=str(desc) if desc else None,
                            thumbnail_url=thumb,
                            raw_payload=item,
                        )
                    )

        return items

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        # Always try API first if credential is provided
        if self.api_key:
            try:
                return await self._fetch_api(keyword)
            except Exception as e:
                log.warning("YouTube API fetch failed, falling back to scrape. Error: %s", e)

        # Scrape fallback
        try:
            return await self._fetch_scrape(keyword)
        except Exception as e:
            log.warning("YouTube scrape fetch failed. Error: %s", e)
            return []
