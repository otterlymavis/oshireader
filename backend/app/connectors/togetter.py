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
                    log.warning("Togetter search returned %d", resp.status_code)
                    return []
        except Exception as exc:
            log.warning("Togetter fetch error: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items: list[SourceItemCreate] = []

        seen_ids: set[str] = set()
        for a in soup.select("a[href^='https://togetter.com/li/']"):
            if len(items) >= 25:
                break
            url = a.get("href", "")
            togetter_id = url.rstrip("/").split("/")[-1]
            if not togetter_id or togetter_id in seen_ids:
                continue
            seen_ids.add(togetter_id)

            # The <li> is the outermost article container; <time datetime> lives there
            li_parent = a.find_parent("li")
            container = li_parent or a.find_parent(["div", "article"])

            # Title is on the h3 inside the li, not always on this <a>
            title = ""
            if li_parent:
                h3 = li_parent.find("h3")
                if h3:
                    title = h3.get_text(strip=True)
            if not title:
                title = a.get_text(strip=True)
            if not title:
                continue

            thumb = None
            published = datetime.now(timezone.utc)

            if container:
                img = container.select_one("img[src]")
                if img:
                    src = img.get("src", "")
                    if src.startswith("http"):
                        thumb = src

                time_el = container.select_one("time[datetime]")
                if time_el:
                    dt_str = (time_el.get("datetime") or "").strip()
                    try:
                        parsed = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        published = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                    except (ValueError, AttributeError):
                        pass

            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=togetter_id,
                    url=url,
                    published_at=published,
                    media_type="article",
                    title=title,
                    thumbnail_url=thumb,
                    content_text=None,
                    author=None,
                    raw_payload={"keyword": keyword},
                )
            )

        return items
