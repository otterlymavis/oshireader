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
    "Referer": "https://girlschannel.net/",
}


def _topic_id(url: str) -> str:
    m = re.search(r"/topics/(\d+)", url)
    return m.group(1) if m else url[-12:]


def _parse_date(text: str) -> datetime:
    # GC dates look like "2024.05.11 12:34"
    m = re.search(r"(\d{4})[./](\d{1,2})[./](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


class GirlsChannelConnector(BaseConnector):
    PLATFORM = "girlschannel"
    SUPPORTS_MEDIA_FILTER = False

    _SEARCH = "https://girlschannel.net/topics/search/"

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        if mode == "media_only":
            return []

        params = {"q": keyword}
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True) as client:
                resp = await client.get(self._SEARCH, params=params)
                if not resp.is_success:
                    log.debug("GirlsChannel search returned %d", resp.status_code)
                    return []
        except Exception as exc:
            log.debug("GirlsChannel fetch error: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items: list[SourceItemCreate] = []

        for article in soup.select("article, .topic-item, li.topic")[:25]:
            a = article.select_one("a[href*='/topics/']")
            if not a:
                continue
            href = a.get("href", "")
            url = href if href.startswith("http") else f"https://girlschannel.net{href}"
            title = a.get_text(strip=True) or article.get_text(strip=True)[:120]

            date_el = article.select_one("time, .date, .time")
            date_text = date_el.get_text(strip=True) if date_el else ""
            published = _parse_date(date_text)

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=_topic_id(url),
                    url=url,
                    published_at=published,
                    media_type="text",
                    title=title,
                    content_text=None,
                    author=None,
                    thumbnail_url=None,
                    raw_payload={"keyword": keyword},
                )
            )

        return items
