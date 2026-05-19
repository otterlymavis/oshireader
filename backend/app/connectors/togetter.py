import logging
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from app.connectors.base import BaseConnector, SourceItemCreate

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
}


class TogetterConnector(BaseConnector):
    PLATFORM = "togetter"
    SUPPORTS_MEDIA_FILTER = False

    _SEARCH = "https://togetter.com/search"

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if mode == "media_only":
            return []

        params = {"q": keyword}
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True) as client:
                resp = await client.get(self._SEARCH, params=params)
                if not resp.is_success:
                    log.debug("Togetter search returned %d", resp.status_code)
                    return []
        except Exception as exc:
            log.debug("Togetter fetch error: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items: list[SourceItemCreate] = []

        for a in soup.select("a[href^='https://togetter.com/li/']")[:25]:
            url = a.get("href", "")
            togetter_id = url.rstrip("/").split("/")[-1]
            if not togetter_id:
                continue

            title = a.get_text(strip=True)
            if not title:
                continue

            # Try to find an image inside the same container
            parent = a.find_parent(["li", "div", "article"])
            thumb = None
            if parent:
                img = parent.select_one("img[src]")
                if img:
                    src = img.get("src", "")
                    if src.startswith("http"):
                        thumb = src

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=togetter_id,
                    url=url,
                    published_at=datetime.now(timezone.utc),
                    media_type="article",
                    title=title,
                    thumbnail_url=thumb,
                    content_text=None,
                    author=None,
                    raw_payload={"keyword": keyword},
                )
            )

        return items
