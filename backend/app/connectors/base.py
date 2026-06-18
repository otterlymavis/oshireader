from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import unicodedata

import feedparser

from app.models import CollectionMode


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
