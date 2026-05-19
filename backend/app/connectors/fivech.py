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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _thread_id(url: str) -> str:
    # Extract thread ID from URLs like https://[board].5ch.net/test/read.cgi/[board]/[id]/
    m = re.search(r"/(\d{9,})", url)
    return m.group(1) if m else url[-20:].replace("/", "_")


class FiveChConnector(BaseConnector):
    PLATFORM = "5ch"
    SUPPORTS_MEDIA_FILTER = False

    _SEARCH = "https://find.5ch.net/"

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if mode == "media_only":
            return []

        params = {"q": keyword, "type": "thread"}
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True) as client:
                resp = await client.get(self._SEARCH, params=params)
                if not resp.is_success:
                    log.debug("5ch search returned %d", resp.status_code)
                    return []
        except Exception as exc:
            log.debug("5ch fetch error: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items: list[SourceItemCreate] = []

        # find.5ch.net search result links
        for a in soup.select("a[href*='5ch.net/test/read.cgi']")[:25]:
            url = a.get("href", "")
            if not url:
                continue
            title = a.get_text(strip=True) or a.get("title", "")
            if not title:
                parent = a.find_parent(["li", "div", "article"])
                title = parent.get_text(strip=True)[:120] if parent else url

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=_thread_id(url),
                    url=url,
                    published_at=datetime.now(timezone.utc),
                    media_type="text",
                    title=title,
                    content_text=None,
                    author=None,
                    thumbnail_url=None,
                    raw_payload={"keyword": keyword},
                )
            )

        return items
