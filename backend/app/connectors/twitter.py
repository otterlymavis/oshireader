import logging
import asyncio
from datetime import datetime
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import (
    BaseConnector,
    GOOGLE_NEWS_HEADERS,
    SourceItemCreate,
    contains_keyword,
    fetch_search_rss_via_proxy,
    parse_feed_date,
    parse_google_news_markdown,
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
        if not self.bearer_token:
            return await self._fetch_public_index(keyword, mode)
        if mode != CollectionMode.MEDIA_ONLY:
            public_items = await self._fetch_public_index(keyword, mode)
            if public_items:
                return public_items
        items = await self._fetch_api(keyword, mode)
        if items:
            return items
        return [] if mode == CollectionMode.MEDIA_ONLY else await self._fetch_public_index(keyword, mode)

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
                    raw_payload=tweet,
                )
            )

        return items

    async def _fetch_public_index(
        self,
        keyword: str,
        mode: CollectionMode,
    ) -> list[SourceItemCreate]:
        encoded = quote(f"{keyword} site:x.com when:1y")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        feed = None
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("X public-index fallback returned status %d", resp.status_code)
                else:
                    feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("X public-index fallback failed: %s", exc)

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in (feed.entries if feed else [])[:25]:
            title = (entry.get("title") or "").strip()
            link = entry.get("link", "")
            item_id = entry.get("id") or link
            if not link or not contains_keyword(keyword, title) or item_id in seen:
                continue
            if mode == CollectionMode.MEDIA_ONLY:
                continue
            published = parse_feed_date(entry)
            if published is None:
                continue
            seen.add(item_id)
            items.append(SourceItemCreate(
                platform=self.PLATFORM,
                item_id=item_id,
                url=link,
                published_at=published,
                media_type="text",
                title=title,
                content_text=entry.get("summary") or None,
                raw_payload={"source": "google_news_public_index", "keyword": keyword},
            ))
        if items:
            return items
        items = await self._fetch_public_index_jina(keyword, mode, url)
        if items:
            return items
        return await self._fetch_public_index_proxy(keyword, mode)

    async def _fetch_public_index_jina(
        self,
        keyword: str,
        mode: CollectionMode,
        google_news_url: str,
    ) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []
        proxy_url = "https://r.jina.ai/http://" + google_news_url.replace("https://", "")
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(proxy_url)
                if not resp.is_success:
                    log.warning("X public-index Jina fallback returned status %d", resp.status_code)
                    return []
        except Exception as exc:
            log.warning("X public-index Jina fallback failed: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in parse_google_news_markdown(resp.text)[:25]:
            title = entry["title"]
            link = entry["url"]
            if not link or not contains_keyword(keyword, title) or link in seen:
                continue
            seen.add(link)
            items.append(SourceItemCreate(
                platform=self.PLATFORM,
                item_id=link,
                url=link,
                published_at=entry["published_at"],
                media_type="text",
                title=title,
                content_text=None,
                raw_payload={"source": "google_news_jina", "keyword": keyword},
            ))
        return items

    async def _fetch_public_index_proxy(
        self,
        keyword: str,
        mode: CollectionMode,
    ) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []
        query = f"{keyword} site:x.com when:1y"
        for target, source in (("google", "google_news_proxy"), ("bing", "bing_news_proxy")):
            content = await fetch_search_rss_via_proxy(query, target=target)
            if not content:
                continue
            feed = await asyncio.to_thread(feedparser.parse, content)
            items: list[SourceItemCreate] = []
            seen: set[str] = set()
            for entry in feed.entries[:25]:
                title = (entry.get("title") or "").strip()
                link = entry.get("link", "")
                item_id = entry.get("id") or link
                if not link or not contains_keyword(keyword, title) or item_id in seen:
                    continue
                published = parse_feed_date(entry)
                if published is None:
                    continue
                seen.add(item_id)
                items.append(SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=published,
                    media_type="text",
                    title=title,
                    content_text=entry.get("summary") or None,
                    raw_payload={"source": source, "keyword": keyword},
                ))
            if items:
                return items
        return []
