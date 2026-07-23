import logging
import asyncio
from datetime import datetime, timedelta, timezone
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
_MAX_INDEX_AGE = timedelta(days=31)
_FUTURE_GRACE = timedelta(days=1)


def _is_recent(published_at: datetime) -> bool:
    published = published_at.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    return now - _MAX_INDEX_AGE <= published <= now + _FUTURE_GRACE


class TogetterConnector(BaseConnector):
    PLATFORM = "togetter"
    SUPPORTS_MEDIA_FILTER = False

    _SEARCH = "https://togetter.com/search"

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        try:
            async with httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True) as client:
                pages = await asyncio.gather(
                    self._fetch_search_page(
                        client,
                        {"q": keyword, "t": "q", "sort": "created_at"},
                        keyword,
                        "direct_search",
                    ),
                    self._fetch_search_page(
                        client,
                        {"q": f"tag:{keyword}", "sort": "created_at"},
                        keyword,
                        "tag_search",
                    ),
                )
        except Exception as exc:
            log.warning("Togetter fetch error: %s", exc)
            return []

        items = self._merge_items([item for page_items in pages for item in page_items])
        if items:
            return items[:25]
        return await self._fetch_indexed_history(keyword)

    async def _fetch_search_page(
        self,
        client: httpx.AsyncClient,
        params: dict[str, str],
        keyword: str,
        source: str,
    ) -> list[SourceItemCreate]:
        resp = await client.get(self._SEARCH, params=params)
        if not resp.is_success:
            log.warning("Togetter search returned %d", resp.status_code)
            return []
        return self._parse_html(resp.text, keyword, source)

    def _parse_html(self, html: str, keyword: str, source: str) -> list[SourceItemCreate]:
        page = scrapling_page(html, self._SEARCH)
        items: list[SourceItemCreate] = []
        seen_ids: set[str] = set()
        tag_context = source == "tag_search" and f"tag:{keyword}" in html
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
            matched_visible_text = contains_keyword(keyword, title, text_of(container) if container else None)
            if not matched_visible_text and not tag_context:
                continue
            if not matched_visible_text:
                title = f"{keyword}: {title}"

            thumb = None
            published: datetime | None = None

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

            date_parsed = published is not None
            if not published:
                published = datetime.now(timezone.utc)
            elif not _is_recent(published):
                continue

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=togetter_id,
                    url=url,
                    published_at=published,
                    media_type="article",
                    title=title,
                    thumbnail_url=thumb,
                    content_text=f"Togetter tag: {keyword}" if source == "tag_search" else None,
                    author=None,
                    raw_payload={"keyword": keyword, "source": source, "date_parsed": date_parsed},
                )
            )

        return items

    def _merge_items(self, candidates: list[SourceItemCreate]) -> list[SourceItemCreate]:
        by_id: dict[str, SourceItemCreate] = {}
        for item in candidates:
            existing = by_id.get(item.item_id)
            if existing is None or item.published_at > existing.published_at:
                by_id[item.item_id] = item
        return sorted(
            by_id.values(),
            key=lambda item: (
                bool((item.raw_payload or {}).get("date_parsed", True)),
                item.published_at,
            ),
            reverse=True,
        )

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
            published = parse_feed_date(entry)
            if published is None:
                continue
            if not _is_recent(published):
                continue
            seen.add(item_id)
            items.append(SourceItemCreate(
                platform=self.PLATFORM,
                item_id=item_id,
                url=link,
                published_at=published,
                media_type="article",
                title=title,
                content_text=entry.get("summary") or None,
                raw_payload={"source": "google_news_history", "keyword": keyword},
            ))
        return items
