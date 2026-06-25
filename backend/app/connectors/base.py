from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Optional
import unicodedata

import feedparser

from app.models import CollectionMode

GOOGLE_NEWS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "ja,en;q=0.9",
}

_GNEWS_MD_ITEM_RE = re.compile(
    r"^### \[(?P<title>.+?)\]\((?P<url>https://news\.google\.com/rss/articles/[^)]+)\)"
    r".*?\n\n(?P<date>[A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4} [0-9:]{8} GMT)",
    re.M | re.S,
)


def parse_feed_date(entry: feedparser.FeedParserDict) -> datetime:
    """Return a timezone-aware UTC datetime from a feedparser entry, falling back to now."""
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


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
            published = datetime.now(timezone.utc)
        items.append({
            "title": match.group("title").strip(),
            "url": url,
            "published_at": published,
        })
    return items


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

    @property
    def composite_id(self) -> str:
        return f"{self.platform}:{self.item_id}"


class BaseConnector(ABC):
    PLATFORM: str = ""
    SUPPORTS_MEDIA_FILTER: bool = False

    @abstractmethod
    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        ...
