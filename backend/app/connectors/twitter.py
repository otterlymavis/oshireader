import logging
from datetime import datetime

import httpx

from app.connectors.base import BaseConnector, SourceItemCreate, contains_keyword
from app.models import CollectionMode

log = logging.getLogger(__name__)


class TwitterConnector(BaseConnector):
    PLATFORM = "twitter"
    SUPPORTS_MEDIA_FILTER = True

    _SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

    def __init__(self, bearer_token: str) -> None:
        self.bearer_token = bearer_token

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if not self.bearer_token:
            return []

        query = f"{keyword} has:media" if mode == CollectionMode.MEDIA_ONLY else keyword
        params = {
            "query": query,
            "max_results": 25,
            "tweet.fields": "created_at,author_id,text",
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "name,username",
            "media.fields": "preview_image_url,url,type",
        }
        headers = {"Authorization": f"Bearer {self.bearer_token}"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self._SEARCH_URL, params=params, headers=headers)
                if not resp.is_success:
                    log.warning("Twitter API returned status %d", resp.status_code)
                    return []
                data = resp.json()
        except Exception as exc:
            log.warning("Twitter fetch error: %s", exc)
            return []

        users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
        media_map = {m["media_key"]: m for m in data.get("includes", {}).get("media", [])}

        items: list[SourceItemCreate] = []
        for tweet in data.get("data", []):
            user = users.get(tweet.get("author_id", ""), {})
            if not contains_keyword(keyword, tweet.get("text"), user.get("name"), user.get("username")):
                continue
            tweet_id = tweet["id"]
            username = user.get("username", "")
            created = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))

            thumb = None
            media_type = "text"
            for key in tweet.get("attachments", {}).get("media_keys", []):
                media = media_map.get(key, {})
                thumb = media.get("preview_image_url") or media.get("url")
                m_type = media.get("type", "")
                if m_type == "photo":
                    media_type = "image"
                elif m_type in ("video", "animated_gif"):
                    media_type = "video"
                if thumb:
                    break

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=tweet_id,
                    url=f"https://x.com/{username}/status/{tweet_id}" if username else f"https://x.com/i/status/{tweet_id}",
                    published_at=created,
                    media_type=media_type,
                    author=f"@{username}" if username else None,
                    title=None,
                    content_text=tweet.get("text"),
                    thumbnail_url=thumb,
                    raw_payload=tweet,
                )
            )

        return items
