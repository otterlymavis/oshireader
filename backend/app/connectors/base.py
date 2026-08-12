from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Optional
from urllib.parse import quote, urlencode
import unicodedata

import feedparser
import httpx

from app.config import settings
from app.models import CollectionMode

GOOGLE_NEWS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "ja,en;q=0.9",
}

def jina_reader_headers(accept_language: str = GOOGLE_NEWS_HEADERS["Accept-Language"]) -> dict[str, str]:
    """Headers for requests to r.jina.ai's reader proxy.

    Anonymous r.jina.ai traffic to news.google.com is subject to a shared,
    rolling abuse block ("AbuseAlleviationError") once Google flags the
    proxy's IP pool — unrelated to this app's own request volume. An API
    key moves the request onto jina's authenticated quota, which isn't
    subject to that shared block.
    """
    headers = {**GOOGLE_NEWS_HEADERS, "Accept-Language": accept_language}
    if settings.jina_api_key:
        headers["Authorization"] = f"Bearer {settings.jina_api_key}"
    return headers


async def fetch_google_news_via_public_proxy(
    google_news_url: str,
    accept_language: str = GOOGLE_NEWS_HEADERS["Accept-Language"],
) -> bytes | None:
    """Fetch a Google News RSS URL through allorigins.win, a free public
    read-through proxy, as a second, independent hop between jina.ai and Bing.

    Different infrastructure and IP space than both jina.ai and our own
    Cloudflare Worker (which Google already blocks for direct Google News
    fetches — see CLAUDE.md), so a block on either of those shouldn't
    correlate with a block here. Returns raw RSS bytes (parse with
    feedparser, same as the Bing fallback) or None on any failure.
    """
    proxy_url = "https://api.allorigins.win/raw?url=" + quote(google_news_url, safe="")
    headers = {**GOOGLE_NEWS_HEADERS, "Accept-Language": accept_language}
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(proxy_url)
            if not resp.is_success:
                return None
            return resp.content
    except Exception:
        return None


SEARCH_RESULT_MAX_AGE = timedelta(days=31)
SEARCH_RESULT_FUTURE_GRACE = timedelta(days=1)

_GNEWS_MD_ITEM_RE = re.compile(
    r"^### \[(?P<title>.+?)\]\((?P<url>https://news\.google\.com/rss/articles/[^)]+)\)"
    r".*?\n\n(?P<date>[A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4} [0-9:]{8} GMT)",
    re.M | re.S,
)


class _FeedDate(datetime):
    def __new__(cls, value: datetime, *, date_parsed: bool):
        result = datetime.__new__(
            cls,
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond,
            tzinfo=value.tzinfo,
            fold=value.fold,
        )
        result.date_parsed = date_parsed
        return result


def mark_date_provenance(value: datetime, *, date_parsed: bool) -> datetime:
    return _FeedDate(value, date_parsed=date_parsed)


def parse_feed_date(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    """Return the newest date supplied by the feed, or None when it is absent."""
    dates: list[datetime] = []
    for attr in ("updated_parsed", "published_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                dates.append(datetime(*t[:6], tzinfo=timezone.utc))
            except Exception:
                pass
    if dates:
        return mark_date_provenance(max(dates), date_parsed=True)
    return None


def parse_google_news_markdown(text: str) -> list[dict]:
    """Parse r.jina.ai's markdown view of a Google News RSS result page."""
    if not isinstance(text, str):
        return []
    items: list[dict] = []
    seen: set[str] = set()
    for match in _GNEWS_MD_ITEM_RE.finditer(text):
        url = match.group("url")
        if url in seen:
            continue
        seen.add(url)
        try:
            published = parsedate_to_datetime(match.group("date")).astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        items.append({
            "title": match.group("title").strip(),
            "url": url,
            "published_at": mark_date_provenance(published, date_parsed=True),
        })
    return items


def is_recent_search_result(published_at: datetime) -> bool:
    """Return whether a search/RSS item belongs in the current source window."""
    published = published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    published = published.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    return now - SEARCH_RESULT_MAX_AGE <= published <= now + SEARCH_RESULT_FUTURE_GRACE


async def fetch_search_rss_via_proxy(
    query: str,
    target: str = "google",
    *,
    hl: str | None = None,
    gl: str | None = None,
    ceid: str | None = None,
    mkt: str | None = None,
    accept_language: str | None = None,
) -> bytes | None:
    proxy_url = settings.source_rss_proxy_url.strip()
    token = settings.admin_api_token.strip()
    if not proxy_url or not token:
        return None
    params = {"target": target, "query": query}
    if hl:
        params["hl"] = hl
    if gl:
        params["gl"] = gl
    if ceid:
        params["ceid"] = ceid
    if mkt:
        params["mkt"] = mkt
    if accept_language:
        params["accept_language"] = accept_language
    url = f"{proxy_url}?{urlencode(params)}"
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if not response.is_success:
                return None
            return response.content
    except Exception:
        return None


def contains_keyword(keyword: str, *values: object) -> bool:
    needle = unicodedata.normalize("NFKC", keyword.strip()).casefold()
    if not needle:
        return False
    return any(
        needle in unicodedata.normalize("NFKC", str(value)).casefold()
        for value in values
        if value
    )


def title_contains_keyword(keyword: str, title: object) -> bool:
    """Use for feed/search results where summaries can contain unrelated cluster text."""
    return contains_keyword(keyword, title)


@dataclass
class SourceItemCreate:
    platform: str
    item_id: str
    url: str
    published_at: datetime
    media_type: str
    author: Optional[str] = None
    title: Optional[str] = None
    content_text: Optional[str] = None
    thumbnail_url: Optional[str] = None
    raw_payload: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.raw_payload = dict(self.raw_payload or {})
        if "date_parsed" not in self.raw_payload:
            marker = getattr(self.published_at, "date_parsed", None)
            if marker is not None:
                self.raw_payload = {**self.raw_payload, "date_parsed": marker}

    @property
    def composite_id(self) -> str:
        return f"{self.platform}:{self.item_id}"


def build_google_news_jina_items(
    markdown_text: str,
    keyword: str,
    *,
    platform: str,
    title_suffix_re: Optional[re.Pattern] = None,
    author: Optional[str] = None,
    raw_payload_extra: Optional[dict] = None,
) -> list[SourceItemCreate]:
    """Parse r.jina.ai's Google News markdown into matching, recent SourceItemCreates.

    Shared by every connector that falls back to Google News via the jina.ai
    reader proxy (direct-to-Google-News fetches 503 from Render's outbound
    IP; see CLAUDE.md). Centralizing this — instead of each connector
    reimplementing it — is what the title/recency filtering lives in one
    place: a per-connector copy previously drifted and shipped without the
    recency check, admitting stale articles as if they were fresh matches.
    """
    items: list[SourceItemCreate] = []
    for entry in parse_google_news_markdown(markdown_text)[:25]:
        title = entry["title"]
        if title_suffix_re:
            title = title_suffix_re.sub("", title).strip()
        if not title or not title_contains_keyword(keyword, title):
            continue
        published = entry.get("published_at")
        if published is None or not is_recent_search_result(published):
            continue
        raw_payload = {"keyword": keyword, "source": "google_news_jina"}
        if raw_payload_extra:
            raw_payload.update(raw_payload_extra)
        items.append(
            SourceItemCreate(
                platform=platform,
                item_id=entry["url"],
                url=entry["url"],
                published_at=published,
                media_type="article",
                title=title,
                content_text=None,
                author=author,
                raw_payload=raw_payload,
            )
        )
    return items


async def build_google_news_public_proxy_items(
    content: bytes,
    keyword: str,
    *,
    platform: str,
    title_suffix_re: Optional[re.Pattern] = None,
    raw_payload_extra: Optional[dict] = None,
) -> list[SourceItemCreate]:
    """Parse a Google News RSS response fetched via the public-proxy fallback
    (allorigins.win) into matching, recent SourceItemCreates.

    Shared by every connector that uses the public-proxy hop, same reasoning
    as build_google_news_jina_items above: a per-connector copy of this
    parsing previously drifted (one added a link to the dedup set before the
    recency check, another after) and centralizing it removes that class of
    bug entirely instead of relying on both copies staying in sync by hand.
    """
    try:
        feed = await asyncio.to_thread(feedparser.parse, content)
    except Exception:
        return []

    items: list[SourceItemCreate] = []
    seen: set[str] = set()
    for entry in feed.entries[:25]:
        title = (entry.get("title") or "").strip()
        link = entry.get("link", "")
        if title_suffix_re:
            title = title_suffix_re.sub("", title).strip()
        if not link or not title or link in seen:
            continue
        if not title_contains_keyword(keyword, title):
            continue
        published = parse_feed_date(entry)
        if published is None or not is_recent_search_result(published):
            continue
        seen.add(link)
        raw_payload = {"keyword": keyword, "source": "google_news_public_proxy"}
        if raw_payload_extra:
            raw_payload.update(raw_payload_extra)
        items.append(
            SourceItemCreate(
                platform=platform,
                item_id=link,
                url=link,
                published_at=published,
                media_type="article",
                title=title,
                content_text=entry.get("summary") or None,
                raw_payload=raw_payload,
            )
        )
    return items


class BaseConnector(ABC):
    PLATFORM: str = ""
    SUPPORTS_MEDIA_FILTER: bool = False
    # Whether this connector's own fetch success/failure should be surfaced via
    # /api/source-health. False for connectors that are structurally unable to
    # be fetched from the backend's host (see CLAUDE.md) but still deliver
    # matches through a separate client-side path — their backend poll result
    # says nothing about whether the source is actually working for the user.
    REPORTS_STATUS_TO_CLIENT: bool = True

    @abstractmethod
    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        ...
