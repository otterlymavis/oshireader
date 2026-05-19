from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SourceItemCreate:
    platform: str
    item_id: str
    url: str
    published_at: datetime
    media_type: str
    author: str | None = None
    title: str | None = None
    content_text: str | None = None
    thumbnail_url: str | None = None
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
