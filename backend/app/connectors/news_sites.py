"""
Thin site-specific connectors that filter Google News RSS by domain.
BARKS also tries its direct RSS feed first.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, quote_plus, urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    GOOGLE_NEWS_HEADERS,
    SourceItemCreate,
    contains_keyword,
    fetch_search_rss_via_proxy,
    parse_feed_date,
    parse_google_news_markdown,
    title_contains_keyword,
)

log = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")
_SEARCH_RESULT_MAX_AGE = timedelta(days=31)
_SEARCH_RESULT_FUTURE_GRACE = timedelta(days=1)


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


def _parse_jst_datetime(value: str | None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone(timedelta(hours=9)))
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _is_recent_search_result(published_at: datetime) -> bool:
    published = published_at.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    return now - _SEARCH_RESULT_MAX_AGE <= published <= now + _SEARCH_RESULT_FUTURE_GRACE


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
        feed = None
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("%s Google News returned %d", self.PLATFORM, resp.status_code)
                else:
                    feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("%s Google News error: %s", self.PLATFORM, exc)

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in (feed.entries if feed else [])[:25]:
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
            published = parse_feed_date(entry)
            if not _is_recent_search_result(published):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=published,
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
        if items:
            return items
        items = await self._fetch_gnews_jina(keyword, url, history_years)
        if items:
            return items
        items = await self._fetch_proxy_google_news(keyword, history_years)
        if items:
            return items
        return await self._fetch_bing_news(keyword, history_years)

    async def _fetch_gnews_jina(
        self,
        keyword: str,
        google_news_url: str,
        history_years: int | None,
    ) -> list[SourceItemCreate]:
        proxy_url = "https://r.jina.ai/http://" + google_news_url.replace("https://", "")
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(proxy_url)
                if not resp.is_success:
                    log.warning("%s Google News Jina fallback returned %d", self.PLATFORM, resp.status_code)
                    return []
        except Exception as exc:
            log.warning("%s Google News Jina fallback error: %s", self.PLATFORM, exc)
            return []

        items: list[SourceItemCreate] = []
        for entry in parse_google_news_markdown(resp.text)[:25]:
            title = entry["title"]
            if self.TITLE_SUFFIX_RE:
                title = self.TITLE_SUFFIX_RE.sub("", title).strip()
            if not title or not title_contains_keyword(keyword, title):
                continue
            if not _is_recent_search_result(entry["published_at"]):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=entry["url"],
                    url=entry["url"],
                    published_at=entry["published_at"],
                    media_type="article",
                    title=title,
                    content_text=None,
                    raw_payload={
                        "site": self.SITE,
                        "keyword": keyword,
                        "history_years": history_years,
                        "source": "google_news_jina",
                    },
                )
            )
        return items

    async def _fetch_proxy_google_news(
        self,
        keyword: str,
        history_years: int | None,
    ) -> list[SourceItemCreate]:
        history = f" when:{history_years}y" if history_years else ""
        query = f"{keyword} site:{self.SITE}{history}"
        content = await fetch_search_rss_via_proxy(query, target="google")
        if not content:
            return []
        feed = await asyncio.to_thread(feedparser.parse, content)
        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries[:25]:
            title = (entry.get("title") or "").strip()
            link = entry.get("link", "")
            item_id = entry.get("id") or link
            if self.TITLE_SUFFIX_RE:
                title = self.TITLE_SUFFIX_RE.sub("", title).strip()
            if not link or not title or item_id in seen:
                continue
            if not title_contains_keyword(keyword, title):
                continue
            seen.add(item_id)
            published = parse_feed_date(entry)
            if not _is_recent_search_result(published):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=published,
                    media_type="article",
                    title=title,
                    content_text=entry.get("summary") or None,
                    raw_payload={
                        "site": self.SITE,
                        "keyword": keyword,
                        "history_years": history_years,
                        "source": "google_news_proxy",
                    },
                )
            )
        return items

    async def _fetch_bing_news(
        self,
        keyword: str,
        history_years: int | None,
    ) -> list[SourceItemCreate]:
        query = f"{keyword} site:{self.SITE}"
        url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&mkt=ja-JP"
        source = "bing_news_proxy"
        content = await fetch_search_rss_via_proxy(query, target="bing")
        try:
            if not content:
                source = "bing_news"
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                    resp = await client.get(url)
                    if not resp.is_success:
                        log.warning("%s Bing News fallback returned %d", self.PLATFORM, resp.status_code)
                        return []
                content = resp.content
            feed = await asyncio.to_thread(feedparser.parse, content)
        except Exception as exc:
            log.warning("%s Bing News fallback error: %s", self.PLATFORM, exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries[:25]:
            title = (entry.get("title") or "").strip()
            link = entry.get("link", "")
            item_id = entry.get("id") or link
            if self.TITLE_SUFFIX_RE:
                title = self.TITLE_SUFFIX_RE.sub("", title).strip()
            if not link or not title or item_id in seen:
                continue
            if not title_contains_keyword(keyword, title):
                continue
            seen.add(item_id)
            published = parse_feed_date(entry)
            if not _is_recent_search_result(published):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=published,
                    media_type="article",
                    title=title,
                    content_text=entry.get("summary") or None,
                    raw_payload={
                        "site": self.SITE,
                        "keyword": keyword,
                        "history_years": history_years,
                        "source": source,
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
            published = parse_feed_date(entry)
            if not _is_recent_search_result(published):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=published,
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

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []
        items = await self._fetch_direct_search(keyword)
        if items:
            return items
        return await super().fetch(keyword, mode)

    async def _fetch_direct_search(self, keyword: str) -> list[SourceItemCreate]:
        url = f"https://realsound.jp/?s={quote(keyword)}"
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                response = await client.get(url)
                if not response.is_success:
                    log.warning("realsound direct search returned %d", response.status_code)
                    return []
        except Exception as exc:
            log.warning("realsound direct search error: %s", exc)
            return []

        soup = BeautifulSoup(response.text, "lxml")
        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for article in soup.select("article.entry-summary"):
            title_link = article.select_one("h3.entry-title a[href]")
            if title_link is None:
                continue
            title = title_link.get_text(" ", strip=True)
            item_url = urljoin("https://realsound.jp/", title_link.get("href", ""))
            if not title or not item_url or not title_contains_keyword(keyword, title):
                continue
            if item_url in seen:
                continue
            seen.add(item_url)

            time_element = article.select_one("time[datetime]")
            excerpt = article.select_one(".entry-excerpt")
            author = article.select_one(".entry-author")
            image = article.select_one("img[src]")
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_url,
                    url=item_url,
                    published_at=_parse_jst_datetime(
                        time_element.get("datetime") if time_element else None
                    ),
                    media_type="article",
                    author=author.get_text(" ", strip=True) if author else None,
                    title=title,
                    content_text=excerpt.get_text(" ", strip=True) if excerpt else None,
                    thumbnail_url=(
                        urljoin("https://realsound.jp/", image.get("src", ""))
                        if image
                        else None
                    ),
                    raw_payload={
                        "site": self.SITE,
                        "keyword": keyword,
                        "source": "direct_search",
                    },
                )
            )
            if len(items) >= 25:
                break
        return items


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
