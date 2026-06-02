from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.connectors.base import BaseConnector, SourceItemCreate

log = logging.getLogger(__name__)


def _clean_markdown_title(value: str) -> str:
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
    value = value.replace("_", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


class YahooNewsConnector(BaseConnector):
    PLATFORM = "yahoonews"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if mode == "media_only":
            return []

        encoded = quote(keyword.strip())
        if not encoded:
            return []

        url = f"https://r.jina.ai/http://https://news.yahoo.co.jp/search?p={encoded}"
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.debug("YahooNews search mirror returned status %d", resp.status_code)
                    return []
        except Exception as exc:
            log.debug("YahooNews fetch error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        pattern = re.compile(r"^\s*\d+\.\s+\[(.+?)\]\((https://news\.yahoo\.co\.jp/articles/([A-Za-z0-9]+))\)", re.M | re.S)
        for match in pattern.finditer(resp.text):
            title = _clean_markdown_title(match.group(1))
            article_url = match.group(2)
            item_id = match.group(3)
            if not title or item_id in seen:
                continue
            seen.add(item_id)
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=article_url,
                    published_at=datetime.now(timezone.utc),
                    media_type="article",
                    title=title,
                    raw_payload={"keyword": keyword},
                )
            )
            if len(items) >= 25:
                break

        return items
