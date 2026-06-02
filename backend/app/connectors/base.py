from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


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
    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        ...
