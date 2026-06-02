from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from app.connectors.base import BaseConnector, SourceItemCreate

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
}


class ModelPressConnector(BaseConnector):
    PLATFORM = "mdpr"
    SUPPORTS_MEDIA_FILTER = False

    _SEARCH = "https://mdpr.jp/search"

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if mode == "media_only":
            return []

        try:
            async with httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True) as client:
                resp = await client.get(self._SEARCH, params={"word": keyword})
                if not resp.is_success:
                    log.debug("ModelPress search returned status %d", resp.status_code)
                    return []
        except Exception as exc:
            log.debug("ModelPress fetch error: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items: list[SourceItemCreate] = []
        seen: set[str] = set()

        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not re.search(r"/(?:news|music|drama|interview|cinema|k-enta)/\d+", href):
                continue

            url = href if href.startswith("http") else f"https://mdpr.jp{href}"
            item_id = url.rstrip("/").split("/")[-1]
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)

            title = a.get_text(" ", strip=True)
            if not title:
                continue

            thumb = None
            img = a.select_one("img")
            if img:
                thumb = img.get("data-src") or img.get("src")
                if thumb and thumb.startswith("//"):
                    thumb = f"https:{thumb}"

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=url,
                    published_at=datetime.now(timezone.utc),
                    media_type="article",
                    title=title,
                    thumbnail_url=thumb,
                    raw_payload={"keyword": keyword},
                )
            )
            if len(items) >= 25:
                break

        return items
