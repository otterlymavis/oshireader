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

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if mode == "media_only":
            return []

        target_url = f"https://find.5ch.io/search?q={keyword}"
        jina_url = f"https://r.jina.ai/{target_url}"
        
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
                resp = await client.get(jina_url)
                if not resp.is_success:
                    log.debug("Jina 5ch search returned status %d", resp.status_code)
                    return []
        except Exception as exc:
            log.debug("Jina 5ch fetch error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        
        # Match markdown links from Jina response: [title](url)
        matches = re.findall(r"\[([^\]]+)\]\((https?://[^)]+/test/read\.cgi/[^)]+)\)", resp.text)
        
        for title, url in matches:
            title = title.strip()
            # Strip thread reply counts if present, e.g. "(123)"
            title = re.sub(r"\s*\(\d+\)$", "", title)
            if not title:
                continue
                
            tid = _thread_id(url)
            if tid in seen:
                continue
            seen.add(tid)

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=tid,
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
            if len(items) >= 25:
                break

        return items
