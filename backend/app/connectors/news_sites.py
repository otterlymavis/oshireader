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
from urllib.parse import parse_qs, quote, quote_plus, urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    GOOGLE_NEWS_HEADERS,
    SourceItemCreate,
    build_google_news_jina_items,
    build_google_news_public_proxy_items,
    contains_keyword,
    fetch_google_news_via_public_proxy,
    fetch_search_rss_via_proxy,
    is_recent_search_result,
    jina_reader_headers,
    mark_date_provenance,
    parse_feed_date,
    race_jina_and_public_proxy,
    title_contains_keyword,
)
from app.source_health import record_jina_result

log = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def _unwrap_bing_news_url(link: str) -> str:
    parsed = urlparse(link)
    if _is_bing_news_redirect(link):
        target = parse_qs(parsed.query).get("url", [""])[0].strip()
        target_parsed = urlparse(target)
        if target_parsed.scheme in {"http", "https"} and target_parsed.netloc:
            return target
    return link


def _is_bing_news_redirect(link: str) -> bool:
    parsed = urlparse(link)
    host = parsed.netloc.lower()
    return (
        (host == "bing.com" or host.endswith(".bing.com"))
        and parsed.path.endswith("/news/apiclick.aspx")
    )


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


def _parse_optional_ameba_timestamp(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None
    return None


def _parse_ameba_activity_timestamp(*values: object) -> datetime | None:
    dates = [parsed for value in values if (parsed := _parse_optional_ameba_timestamp(value))]
    if dates:
        return mark_date_provenance(max(dates), date_parsed=True)
    return None


def _parse_jst_datetime(value: str | None) -> datetime | None:
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone(timedelta(hours=9)))
            return mark_date_provenance(parsed.astimezone(timezone.utc), date_parsed=True)
        except ValueError:
            pass
    return None


class _GNewsSiteConnector(BaseConnector):
    """Fetch news for a keyword filtered to a single site via Google News RSS."""

    SITE: str = ""
    TITLE_SUFFIX_RE: re.Pattern | None = None
    SUPPORTS_MEDIA_FILTER = False
    GNEWS_HL = "ja"
    GNEWS_GL = "JP"
    GNEWS_CEID = "JP:ja"
    BING_MKT = "ja-JP"
    ACCEPT_LANGUAGE = GOOGLE_NEWS_HEADERS["Accept-Language"]

    def _headers(self) -> dict[str, str]:
        return {**GOOGLE_NEWS_HEADERS, "Accept-Language": self.ACCEPT_LANGUAGE}

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []
        return await self._fetch_gnews(keyword)

    async def _fetch_gnews(self, keyword: str) -> list[SourceItemCreate]:
        # Always query with when:10y rather than trying an unscoped query first and
        # retrying with when:10y only on an empty result. when:10y is a superset of
        # the unscoped query (Google's default ranking can omit matches that
        # when:10y surfaces) and every result is filtered to the last 31 days
        # downstream regardless (is_recent_search_result), so the two-step retry
        # bought no extra recall — only a second jina.ai request on every poll that
        # found nothing on the first try, which is the common case across ~20
        # platforms every 15 minutes and the scarcest resource in this chain.
        encoded = quote(f"{keyword} site:{self.SITE} when:10y")
        url = (
            f"https://news.google.com/rss/search?q={encoded}"
            f"&hl={quote(self.GNEWS_HL)}"
            f"&gl={quote(self.GNEWS_GL)}"
            f"&ceid={quote(self.GNEWS_CEID)}"
        )
        # Google News is unreachable directly from Render's outbound IP (see
        # CLAUDE.md) and the Cloudflare Worker proxy is now also blocked by
        # Google from Cloudflare's IP ranges, so neither is worth the timeout
        # budget: go straight to the jina.ai reader proxy and a second,
        # independent public proxy (see race_jina_and_public_proxy), then Bing.
        items = await race_jina_and_public_proxy(
            self._fetch_gnews_jina(keyword, url, history_years=10),
            self._fetch_gnews_public_proxy(keyword, url),
        )
        if items:
            return items
        return await self._fetch_bing_news(keyword, history_years=10)

    async def _fetch_gnews_jina(
        self,
        keyword: str,
        google_news_url: str,
        history_years: int | None,
    ) -> tuple[list[SourceItemCreate], bool]:
        proxy_url = "https://r.jina.ai/http://" + google_news_url.replace("https://", "")
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True, headers=jina_reader_headers(self.ACCEPT_LANGUAGE)
            ) as client:
                resp = await client.get(proxy_url)
                if not resp.is_success:
                    log.warning("%s Google News Jina fallback returned %d", self.PLATFORM, resp.status_code)
                    record_jina_result(self.PLATFORM, succeeded=False, error=f"http_{resp.status_code}")
                    return [], False
        except Exception as exc:
            log.warning("%s Google News Jina fallback error: %s", self.PLATFORM, exc)
            record_jina_result(self.PLATFORM, succeeded=False, error=type(exc).__name__)
            return [], False

        record_jina_result(self.PLATFORM, succeeded=True)
        items = build_google_news_jina_items(
            resp.text,
            keyword,
            platform=self.PLATFORM,
            title_suffix_re=self.TITLE_SUFFIX_RE,
            raw_payload_extra={"site": self.SITE, "history_years": history_years},
        )
        return items, True

    async def _fetch_gnews_public_proxy(
        self,
        keyword: str,
        google_news_url: str,
    ) -> list[SourceItemCreate]:
        content = await fetch_google_news_via_public_proxy(google_news_url, accept_language=self.ACCEPT_LANGUAGE)
        if not content:
            return []
        return await build_google_news_public_proxy_items(
            content,
            keyword,
            platform=self.PLATFORM,
            title_suffix_re=self.TITLE_SUFFIX_RE,
            raw_payload_extra={"site": self.SITE},
        )

    async def _fetch_bing_news(
        self,
        keyword: str,
        history_years: int | None,
    ) -> list[SourceItemCreate]:
        query = f"{keyword} site:{self.SITE}"
        url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&mkt={quote(self.BING_MKT)}"
        source = "bing_news_proxy"
        content = await fetch_search_rss_via_proxy(
            query,
            target="bing",
            mkt=self.BING_MKT,
            accept_language=self.ACCEPT_LANGUAGE,
        )
        try:
            if not content:
                source = "bing_news"
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=self._headers()) as client:
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
            raw_link = entry.get("link", "")
            link = _unwrap_bing_news_url(raw_link)
            item_id = link if _is_bing_news_redirect(raw_link) else (entry.get("id") or link)
            if self.TITLE_SUFFIX_RE:
                title = self.TITLE_SUFFIX_RE.sub("", title).strip()
            if not link or not title or item_id in seen:
                continue
            if not title_contains_keyword(keyword, title):
                continue
            seen.add(item_id)
            published = parse_feed_date(entry)
            if published is None:
                continue
            if not is_recent_search_result(published):
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
            if published is None:
                continue
            if not is_recent_search_result(published):
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


class _RSSFirstGNewsSiteConnector(_GNewsSiteConnector):
    """Try direct site RSS feeds before the filtered news-search fallback."""

    RSS_URLS: tuple[str, ...] = ()

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []
        for rss_url in self.RSS_URLS:
            items = await self._fetch_direct_rss(rss_url, keyword)
            if items:
                return items
        return await super().fetch(keyword, mode)


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

            published = _parse_ameba_activity_timestamp(
                raw.get("entryUpdatedDatetime"),
                raw.get("updatedTime"),
                raw.get("updatedAt"),
                raw.get("entryCreatedDatetime"),
                raw.get("publishedTime"),
            )
            if published is None:
                continue

            seen.add(item_id)
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=item_url,
                    published_at=published,
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
            published = _parse_jst_datetime(time_element.get("datetime") if time_element else None)
            if published is None:
                continue
            if not is_recent_search_result(published):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_url,
                    url=item_url,
                    published_at=published,
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


class TheTVConnector(_GNewsSiteConnector):
    PLATFORM = "thetv"
    SITE = "thetv.jp"
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*WEBザテレビジョン\s*$')


class NatalieConnector(_GNewsSiteConnector):
    PLATFORM = "natalie"
    SITE = "natalie.mu"
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*音楽ナタリー\s*$')


class BillboardJapanConnector(_RSSFirstGNewsSiteConnector):
    PLATFORM = "billboardjapan"
    SITE = "billboard-japan.com"
    RSS_URLS = ("https://www.billboard-japan.com/d_news/doc.xml",)
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*Billboard\s*JAPAN\s*$', re.I)


class SoompiConnector(_RSSFirstGNewsSiteConnector):
    PLATFORM = "soompi"
    SITE = "soompi.com"
    RSS_URLS = ("https://www.soompi.com/feed",)
    GNEWS_HL = "en"
    GNEWS_GL = "US"
    GNEWS_CEID = "US:en"
    BING_MKT = "en-US"
    ACCEPT_LANGUAGE = "en,ko;q=0.9,ja;q=0.7"
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*Soompi\s*$', re.I)


class AllkpopConnector(_GNewsSiteConnector):
    PLATFORM = "allkpop"
    SITE = "allkpop.com"
    GNEWS_HL = "en"
    GNEWS_GL = "US"
    GNEWS_CEID = "US:en"
    BING_MKT = "en-US"
    ACCEPT_LANGUAGE = "en,ko;q=0.9,ja;q=0.7"
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*allkpop\s*$', re.I)


class KpopOfficialConnector(_RSSFirstGNewsSiteConnector):
    PLATFORM = "kpopofficial"
    SITE = "kpopofficial.com"
    RSS_URLS = ("https://kpopofficial.com/feed/",)
    GNEWS_HL = "en"
    GNEWS_GL = "US"
    GNEWS_CEID = "US:en"
    BING_MKT = "en-US"
    ACCEPT_LANGUAGE = "en,ko;q=0.9,ja;q=0.7"
    TITLE_SUFFIX_RE = re.compile(r'\s*[|\-]\s*Kpop Official\s*$', re.I)


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
