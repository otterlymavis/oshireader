from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.connectors.base import BaseConnector, CollectionMode, SourceItemCreate, parse_feed_date

log = logging.getLogger(__name__)

# Matches /topics/12345/ with or without trailing slash
_TOPIC_ID_RE = re.compile(r'/topics/(\d+)/?')

# Japanese date patterns like "2024年6月15日" or "6/15" or "2024/06/15"
_JP_DATE_RE = re.compile(r'(\d{4})[年/](\d{1,2})[月/](\d{1,2})')
_HOURS_AGO_RE = re.compile(r'(\d+)\s*時間前')
_MINS_AGO_RE  = re.compile(r'(\d+)\s*分前')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://girlschannel.net/",
}


def _parse_jp_date(text: str) -> datetime | None:
    """Try to parse Japanese date text into a UTC datetime."""
    now = datetime.now(timezone.utc)
    if m := _HOURS_AGO_RE.search(text):
        from datetime import timedelta
        return now - timedelta(hours=int(m.group(1)))
    if m := _MINS_AGO_RE.search(text):
        from datetime import timedelta
        return now - timedelta(minutes=int(m.group(1)))
    if m := _JP_DATE_RE.search(text):
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


class GirlsChannelConnector(BaseConnector):
    PLATFORM = "girlschannel"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        items = await self._fetch_direct(keyword)
        if not items:
            items = await self._fetch_gnews(keyword)
        return items

    async def _fetch_direct(self, keyword: str) -> list[SourceItemCreate]:
        """Scrape GirlsChannel search sorted by latest reply."""
        # Try with explicit latest-update sort first; fall back to default order
        for params in [
            {"q": keyword, "order": "topic_updated_at"},
            {"q": keyword},
        ]:
            try:
                async with httpx.AsyncClient(
                    timeout=15.0, headers=HEADERS, follow_redirects=True
                ) as client:
                    resp = await client.get(
                        "https://girlschannel.net/topics/search/",
                        params=params,
                    )
                    if not resp.is_success:
                        log.warning("GirlsChannel direct search returned %d", resp.status_code)
                        continue
                    items = self._parse_html(resp.text, keyword)
                    if items:
                        return items
            except Exception as exc:
                log.warning("GirlsChannel direct fetch error: %s", exc)

        return []

    def _parse_html(self, html: str, keyword: str) -> list[SourceItemCreate]:
        soup = BeautifulSoup(html, "lxml")
        items: list[SourceItemCreate] = []
        seen: set[str] = set()

        for a in soup.find_all("a", href=_TOPIC_ID_RE):
            href = a.get("href", "")
            m = _TOPIC_ID_RE.search(href)
            if not m:
                continue
            topic_id = m.group(1)
            if topic_id in seen:
                continue
            seen.add(topic_id)

            url = f"https://girlschannel.net/topics/{topic_id}/"

            # Title: heading in nearest container, or nested text of the anchor
            title = ""
            container = a.find_parent(["article", "li", "div", "section"])
            if container:
                h = container.find(["h2", "h3", "h4", "h5"])
                if h:
                    title = h.get_text(strip=True)
            if not title:
                title = a.get_text(strip=True)
            if not title:
                continue

            # Date: <time datetime="..."> or plain text date nearby
            published: datetime | None = None
            if container:
                time_el = container.find("time")
                if time_el:
                    dt_str = (time_el.get("datetime") or "").strip()
                    if dt_str:
                        try:
                            parsed = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                            published = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                        except (ValueError, AttributeError):
                            pass
                    if not published:
                        published = _parse_jp_date(time_el.get_text(strip=True))
                if not published:
                    for el in container.find_all(["span", "p", "small", "time"]):
                        published = _parse_jp_date(el.get_text(strip=True))
                        if published:
                            break

            if not published:
                published = datetime.now(timezone.utc)

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=topic_id,
                    url=url,
                    published_at=published,
                    media_type="text",
                    title=title,
                    thumbnail_url=None,
                    content_text=None,
                    raw_payload={"keyword": keyword, "source": "direct"},
                )
            )

            if len(items) >= 25:
                break

        return items

    async def _fetch_gnews(self, keyword: str) -> list[SourceItemCreate]:
        """Fallback: Google News RSS filtered to girlschannel.net."""
        encoded = quote(f"{keyword} site:girlschannel.net")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"

        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("GirlsChannel via Google News returned status %d", resp.status_code)
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("GirlsChannel Google News fetch error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries[:25]:
            link = entry.get("link", "")
            if not link:
                continue
            item_id = entry.get("id") or link
            if item_id in seen:
                continue
            seen.add(item_id)
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=parse_feed_date(entry),
                    media_type="text",
                    title=title,
                    content_text=entry.get("summary") or None,
                    thumbnail_url=None,
                    raw_payload={"source": "google_news", "keyword": keyword},
                )
            )

        return items
