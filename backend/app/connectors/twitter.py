from datetime import datetime

import httpx

from app.connectors.base import BaseConnector, SourceItemCreate


class TwitterConnector(BaseConnector):
    PLATFORM = "twitter"
    SUPPORTS_MEDIA_FILTER = True

    _SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

    def __init__(self, bearer_token: str) -> None:
        self.bearer_token = bearer_token

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if not self.bearer_token:
            return []

        query = keyword
        if mode == "media_only":
            query += " has:media"

        params = {
            "query": query,
            "max_results": 25,
            "tweet.fields": "created_at,author_id,text",
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "name,username",
            "media.fields": "preview_image_url,url",
        }
        headers = {"Authorization": f"Bearer {self.bearer_token}"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(self._SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
        media_map = {m["media_key"]: m for m in data.get("includes", {}).get("media", [])}

        items: list[SourceItemCreate] = []
        for tweet in data.get("data", []):
            tweet_id = tweet["id"]
            user = users.get(tweet.get("author_id", ""), {})
            username = user.get("username", "")
            created = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))

            thumb = None
            for key in tweet.get("attachments", {}).get("media_keys", []):
                m = media_map.get(key, {})
                thumb = m.get("preview_image_url") or m.get("url")
                if thumb:
                    break

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=tweet_id,
                    url=f"https://x.com/{username}/status/{tweet_id}",
                    published_at=created,
                    media_type="video" if thumb else "text",
                    author=f"@{username}" if username else None,
                    title=None,
                    content_text=tweet.get("text"),
                    thumbnail_url=thumb,
                    raw_payload=tweet,
                )
            )

        return items
