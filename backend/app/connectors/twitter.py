from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import (
    BaseConnector,
    SourceUnavailableError,
    SourceItemCreate,
    contains_keyword,
    fetch_google_news_direct,
    fetch_google_news_via_public_proxy,
    fetch_search_rss_via_proxy,
    first_nonempty_result,
    is_recent_search_result,
    parse_feed_document,
    parse_feed_date,
)
from app.models import CollectionMode

log = logging.getLogger(__name__)


class TwitterConnector(BaseConnector):
    PLATFORM = "twitter"
    SUPPORTS_MEDIA_FILTER = True

    _SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

    def __init__(self, bearer_token: str) -> None:
        self.bearer_token = bearer_token

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if self.bearer_token:
            items = await self._fetch_api(keyword, mode)
            if items:
                return items
        if mode == CollectionMode.MEDIA_ONLY:
            return []
        return await self._fetch_public_index(keyword)

    async def _fetch_public_index(self, keyword: str) -> list[SourceItemCreate]:
        query = f"{keyword} site:x.com when:1y"
        encoded = quote(query)
        url = (
            f"https://news.google.com/rss/search?q={encoded}"
            "&hl=ja&gl=JP&ceid=JP%3Aja"
        )

        async def direct_items() -> list[SourceItemCreate]:
            content = await fetch_google_news_direct(url)
            if not content:
                raise SourceUnavailableError("direct Google News unavailable")
            return await self._public_index_items(
                content,
                keyword,
                "google_news_direct",
            )

        async def public_proxy_items() -> list[SourceItemCreate]:
            content = await fetch_google_news_via_public_proxy(url)
            if not content:
                raise SourceUnavailableError("public Google News proxy unavailable")
            return await self._public_index_items(
                content,
                keyword,
                "google_news_public_proxy",
            )

        async def search_proxy_items(target: str, source: str) -> list[SourceItemCreate]:
            content = await fetch_search_rss_via_proxy(query, target=target)
            if not content:
                raise SourceUnavailableError(f"{source} unavailable")
            return await self._public_index_items(
                content,
                keyword,
                source,
            )

        return await first_nonempty_result(
            direct_items(),
            public_proxy_items(),
            search_proxy_items("google", "google_news_proxy"),
            search_proxy_items("bing", "bing_news_proxy"),
        )

    async def _public_index_items(
        self,
        content: bytes | None,
        keyword: str,
        source: str,
    ) -> list[SourceItemCreate]:
        if not content:
            return []
        feed = await parse_feed_document(content)
        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            link = entry.get("link", "")
            item_id = entry.get("id") or link
            if not title or not link or item_id in seen:
                continue
            if not contains_keyword(keyword, title):
                continue
            published = parse_feed_date(entry)
            if published is None or not is_recent_search_result(published):
                continue
            seen.add(item_id)
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=published,
                    media_type="text",
                    title=title,
                    content_text=entry.get("summary") or None,
                    raw_payload={
                        "source": source,
                        "keyword": keyword,
                        # Search-index dates are not trustworthy tweet creation
                        # dates. Keep these feed-visible but notification-ineligible.
                        "date_parsed": False,
                    },
                )
            )
            if len(items) >= 25:
                break
        return items

    async def _fetch_api(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
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
                    raw_payload={"source": "twitter_api", "date_parsed": True},
                )
            )

        return items
