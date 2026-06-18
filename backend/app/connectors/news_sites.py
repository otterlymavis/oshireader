"""
Thin site-specific connectors that filter Google News RSS by domain.
BARKS also tries its direct RSS feed first.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    SourceItemCreate,
    contains_keyword,
    parse_feed_date,
    title_contains_keyword,
)

log = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def _clean_html_fragment(value: str | None) -> str | None:
    if not value:
        return None
    text = BeautifulSoup(value, "lxml").get_text(" ", strip=True)
    cleaned = _WHITESPACE_RE.sub(" ", text).strip()
    return cleaned or None


def _extract_window_state(html: str) -> dict:
    marker = "window.__STATE__="
    start = html.find(marker)
    if start < 0:
        return {}
    decoder = json.JSONDecoder()
    try:
        state, _ = decoder.raw_decode(html[start + len(marker):])
    except json.JSONDecodeError:
        return {}
    return state if isinstance(state, dict) else {}


def _parse_ameba_timestamp(value: object) -> datetime:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except Exception:
            pass
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


class _GNewsSiteConnector(BaseConnector):
    """Fetch news for a keyword filtered to a single site via Google News RSS."""

    SITE: str = ""
    TITLE_SUFFIX_RE: re.Pattern | None = None
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []
        items = await self._fetch_gnews(keyword)
        if not items:
            items = await self._fetch_gnews(keyword, history_years=10)
        return items

    async def _fetch_gnews(self, keyword: str, history_years: int | None = None) -> list[SourceItemCreate]:
        history = f" when:{history_years}y" if history_years else ""
        encoded = quote(f"{keyword} site:{self.SITE}{history}")
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
            summary = entry.get("summary") or ""
            if not title:
                continue
            if not title_contains_keyword(keyword, title):
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
                    raw_payload={
                        "site": self.SITE,
                        "keyword": keyword,
                        "history_years": history_years,
                    },
                )
            )
        return items

    async def _fetch_direct_rss(self, rss_url: str, keyword: str) -> list[SourceItemCreate]:
        """Fetch a direct RSS feed and keyword-filter the entries."""
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
            if not title_contains_keyword(keyword, title):
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

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        stripped = keyword.strip()
        if not stripped:
            return []

        items = await self._fetch_ameba_search(stripped)
        if not items:
            items = await self._fetch_gnews(stripped)
        if not items:
            items = await self._fetch_gnews(stripped, history_years=10)
        return items

    async def _fetch_ameba_search(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(keyword, safe="")
        url = f"https://search.ameba.jp/search/{encoded}.html"
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                    ),
                    "Accept-Language": "ja,en;q=0.9",
                },
            ) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("%s Ameba search returned %d", self.PLATFORM, resp.status_code)
                    return []
        except Exception as exc:
            log.warning("%s Ameba search error: %s", self.PLATFORM, exc)
            return []

        state = _extract_window_state(resp.text)
        entry_map = (state.get("blogEntry") or {}).get("blogEntryMap") or {}
        if not isinstance(entry_map, dict):
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for raw in entry_map.values():
            if not isinstance(raw, dict):
                continue

            item_id = str(raw.get("entryId") or "").strip()
            ameba_id = str(raw.get("amebaId") or "").strip()
            if not item_id or item_id in seen:
                continue

            title = _clean_html_fragment(raw.get("entryTitle")) or ""
            content = _clean_html_fragment(raw.get("entryContent"))
            blog_title = _clean_html_fragment(raw.get("blogTitle"))
            if not title:
                continue
            if not contains_keyword(keyword, title, content, blog_title):
                continue

            display_title = title
            if not contains_keyword(keyword, display_title) and content:
                display_title = f"{title} - {content}"

            item_url = (
                f"https://ameblo.jp/{ameba_id}/entry-{item_id}.html"
                if ameba_id
                else str(raw.get("url") or "")
            )
            if not item_url:
                continue

            seen.add(item_id)
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=item_url,
                    published_at=_parse_ameba_timestamp(
                        raw.get("entryCreatedDatetime") or raw.get("publishedTime")
                    ),
                    media_type="article",
                    author=blog_title,
                    title=display_title,
                    content_text=content,
                    thumbnail_url=raw.get("firstImageUrl") or None,
                    raw_payload={
                        "site": self.SITE,
                        "keyword": keyword,
                        "source": "ameba_search",
                        "ameba_id": ameba_id or None,
                    },
                )
            )
            if len(items) >= 25:
                break
        return items


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


class CinemaCafeConnector(_GNewsSiteConnector):
    PLATFORM = "cinemacafe"
    SITE = "cinemacafe.net"


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
        if not items:
            items = await self._fetch_gnews(keyword, history_years=10)
        return items
