"""
Thin site-specific connectors that filter Google News RSS by domain.
BARKS also tries its direct RSS feed first.
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

import feedparser
import httpx

from app.connectors.base import BaseConnector, CollectionMode, SourceItemCreate, parse_feed_date

log = logging.getLogger(__name__)


class _GNewsSiteConnector(BaseConnector):
    """Fetch news for a keyword filtered to a single site via Google News RSS."""

    SITE: str = ""
    TITLE_SUFFIX_RE: re.Pattern | None = None
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []
        return await self._fetch_gnews(keyword)

    async def _fetch_gnews(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(f"{keyword} site:{self.SITE}")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("%s Google News returned %d", self.PLATFORM, resp.status_code)
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("%s Google News error: %s", self.PLATFORM, exc)
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
            if self.TITLE_SUFFIX_RE:
                title = self.TITLE_SUFFIX_RE.sub("", title).strip()
            if not title:
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=parse_feed_date(entry),
                    media_type="article",
                    title=title,
                    content_text=entry.get("summary") or None,
                    raw_payload={"site": self.SITE, "keyword": keyword},
                )
            )
        return items

    async def _fetch_direct_rss(self, rss_url: str, keyword: str) -> list[SourceItemCreate]:
        """Fetch a direct RSS feed and keyword-filter the entries."""
        kw = keyword.lower()
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(rss_url)
                if not resp.is_success:
                    log.warning("%s direct RSS returned %d", self.PLATFORM, resp.status_code)
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("%s direct RSS error: %s", self.PLATFORM, exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            summary = entry.get("summary") or ""
            if kw not in title.lower() and kw not in summary.lower():
                continue
            link = entry.get("link", "")
            if not link:
                continue
            item_id = entry.get("id") or link
            if item_id in seen:
                continue
            seen.add(item_id)
            if self.TITLE_SUFFIX_RE:
                title = self.TITLE_SUFFIX_RE.sub("", title).strip()
            if not title:
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=parse_feed_date(entry),
                    media_type="article",
                    title=title,
                    content_text=summary or None,
                    raw_payload={"site": self.SITE, "keyword": keyword, "source": "direct_rss"},
                )
            )
        return items


# ---------------------------------------------------------------------------
# Site connectors
# ---------------------------------------------------------------------------

class AmebloConnector(_GNewsSiteConnector):
    PLATFORM = "ameblo"
    SITE = "ameblo.jp"


class AERAConnector(_GNewsSiteConnector):
    PLATFORM = "aera"
    SITE = "dot.asahi.com"
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*AERA\s*(dot\.)?\s*$', re.I)


class HochiConnector(_GNewsSiteConnector):
    PLATFORM = "hochi"
    SITE = "hochi.news"
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*スポーツ報知.*$')


class SponichiConnector(_GNewsSiteConnector):
    PLATFORM = "sponichi"
    SITE = "sponichi.co.jp"
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*スポニチ.*$')


class LivedoorConnector(_GNewsSiteConnector):
    PLATFORM = "livedoor"
    SITE = "news.livedoor.com"


class MantanWebConnector(_GNewsSiteConnector):
    PLATFORM = "mantanweb"
    SITE = "mantan-web.jp"
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*まんたんウェブ.*$')


class RealSoundConnector(_GNewsSiteConnector):
    PLATFORM = "realsound"
    SITE = "realsound.jp"


class BARKSConnector(_GNewsSiteConnector):
    PLATFORM = "barks"
    SITE = "barks.jp"
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*BARKS\s*$', re.I)

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []
        items = await self._fetch_direct_rss("https://www.barks.jp/news/rss/", keyword)
        if not items:
            items = await self._fetch_gnews(keyword)
        return items
