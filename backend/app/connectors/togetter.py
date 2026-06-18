import logging
import asyncio
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import BaseConnector, CollectionMode, SourceItemCreate, contains_keyword, parse_feed_date
from app.connectors.scrapling_helpers import attr_of, first, scrapling_page, text_of

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
}


class TogetterConnector(BaseConnector):
    PLATFORM = "togetter"
    SUPPORTS_MEDIA_FILTER = False

    _SEARCH = "https://togetter.com/search"

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        params = {"q": keyword}
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True) as client:
                resp = await client.get(self._SEARCH, params=params)
                if not resp.is_success:
                    log.warning("Togetter search returned %d", resp.status_code)
                    return []
        except Exception as exc:
            log.warning("Togetter fetch error: %s", exc)
            return []

        page = scrapling_page(resp.text, self._SEARCH)
        items: list[SourceItemCreate] = []

        seen_ids: set[str] = set()
        for a in page.css("a[href^='https://togetter.com/li/']"):
            if len(items) >= 25:
                break
            url = attr_of(a, "href")
            togetter_id = url.rstrip("/").split("/")[-1]
            if not togetter_id or togetter_id in seen_ids:
                continue
            seen_ids.add(togetter_id)

            # The <li> is the outermost article container; <time datetime> lives there
            li_parent = a.find_ancestor(lambda node: node.tag == "li")
            container = li_parent or a.find_ancestor(lambda node: node.tag in {"div", "article"})

            # Title is on the h3 inside the li, not always on this <a>
            title = ""
            if li_parent:
                h3 = first(li_parent.css("h3"))
                if h3:
                    title = text_of(h3)
            if not title:
                title = text_of(a)
            if not title:
                continue
            if not contains_keyword(keyword, title, text_of(container) if container else None):
                continue

            thumb = None
            published = datetime.now(timezone.utc)

            if container:
                img = first(container.css("img[src]"))
                if img:
                    src = attr_of(img, "src")
                    if src.startswith("http"):
                        thumb = src

                time_el = first(container.css("time[datetime]"))
                if time_el:
                    dt_str = attr_of(time_el, "datetime")
                    try:
                        parsed = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        published = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                    except (ValueError, AttributeError):
                        pass

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=togetter_id,
                    url=url,
                    published_at=published,
                    media_type="article",
                    title=title,
                    thumbnail_url=thumb,
                    content_text=None,
                    author=None,
                    raw_payload={"keyword": keyword},
                )
            )

        if items:
            return items
        return await self._fetch_indexed_history(keyword)

    async def _fetch_indexed_history(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(f"{keyword} site:togetter.com when:10y")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("Togetter indexed-history fallback failed: %s", exc)
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
                media_type="article",
                title=title,
                content_text=entry.get("summary") or None,
                raw_payload={"source": "google_news_history", "keyword": keyword},
            ))
        return items
