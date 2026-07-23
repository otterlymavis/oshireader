from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Optional
from urllib.parse import urlencode
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


def parse_feed_date(entry: feedparser.FeedParserDict) -> datetime:
    """Return the newest feed activity date, marking fetch-time fallback dates."""
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
    return mark_date_provenance(datetime.now(timezone.utc), date_parsed=False)


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
            date_parsed = True
        except (TypeError, ValueError, IndexError, AttributeError):
            published = datetime.now(timezone.utc)
            date_parsed = False
        items.append({
            "title": match.group("title").strip(),
            "url": url,
            "published_at": mark_date_provenance(published, date_parsed=date_parsed),
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


async def fetch_search_rss_via_proxy(query: str, target: str = "google") -> bytes | None:
    proxy_url = settings.source_rss_proxy_url.strip()
    token = settings.admin_api_token.strip()
    if not proxy_url or not token:
        return None
    url = f"{proxy_url}?{urlencode({'target': target, 'query': query})}"
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
        if "date_parsed" not in self.raw_payload:
            marker = getattr(self.published_at, "date_parsed", None)
            if marker is not None:
                self.raw_payload = {**self.raw_payload, "date_parsed": marker}

    @property
    def composite_id(self) -> str:
        return f"{self.platform}:{self.item_id}"


class BaseConnector(ABC):
    PLATFORM: str = ""
    SUPPORTS_MEDIA_FILTER: bool = False

    @abstractmethod
    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        ...
