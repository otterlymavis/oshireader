import json
import logging
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


class TVERConnector(BaseConnector):
    PLATFORM = "tver"
    SUPPORTS_MEDIA_FILTER = True

    _SEARCH = "https://tver.jp/search/result"

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        params = {"keyword": keyword}
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True) as client:
                resp = await client.get(self._SEARCH, params=params)
                if not resp.is_success:
                    log.debug("TVer search returned %d", resp.status_code)
                    return []
        except Exception as exc:
            log.debug("TVer fetch error: %s", exc)
            return []

        # TVer is a Next.js app — try to extract server-side JSON first
        items = self._from_next_data(resp.text)
        if not items:
            items = self._from_html(resp.text)
        return items

    # ── Next.js __NEXT_DATA__ extraction ─────────────────────────────────────

    def _from_next_data(self, html: str) -> list[SourceItemCreate]:
        try:
            soup = BeautifulSoup(html, "lxml")
            tag = soup.find("script", {"id": "__NEXT_DATA__"})
            if not tag or not tag.string:
                return []
            data = json.loads(tag.string)
            page_props = data.get("props", {}).get("pageProps", {})

            # TVer has changed its data structure across versions; try common locations
            raw_episodes = (
                page_props.get("episodes")
                or page_props.get("searchResult", {}).get("episodes")
                or page_props.get("contents")
                or []
            )

            return [item for ep in raw_episodes[:25] if (item := self._ep_to_item(ep)) is not None]
        except Exception as exc:
            log.debug("TVer __NEXT_DATA__ parse failed: %s", exc)
            return []

    def _ep_to_item(self, ep: dict) -> SourceItemCreate | None:
        ep_id = ep.get("id") or ep.get("episode_id") or ep.get("episodeID")
        title = (
            ep.get("title")
            or ep.get("episode_title")
            or ep.get("seriesTitle")
            or ep.get("displayTitle")
        )
        if not ep_id or not title:
            return None

        thumb = (
            ep.get("thumbnailURL")
            or ep.get("thumbnail_url")
            or (ep.get("thumbnail") or {}).get("url")
        )
        broadcast = (
            ep.get("broadcastDateLabel")
            or ep.get("broadcastDate")
            or (ep.get("provider") or {}).get("name")
        )

        return SourceItemCreate(
            platform=self.PLATFORM,
            item_id=str(ep_id),
            url=f"https://tver.jp/episodes/{ep_id}",
            published_at=datetime.now(timezone.utc),
            media_type="video",
            title=str(title),
            thumbnail_url=thumb,
            author=broadcast,
            content_text=ep.get("description"),
            raw_payload=ep,
        )

    # ── HTML fallback ─────────────────────────────────────────────────────────

    def _from_html(self, html: str) -> list[SourceItemCreate]:
        try:
            soup = BeautifulSoup(html, "lxml")
            seen: set[str] = set()
            items: list[SourceItemCreate] = []

            for a in soup.select("a[href*='/episodes/']"):
                href = a.get("href", "")
                ep_id = href.split("/episodes/")[-1].strip("/")
                if not ep_id or ep_id in seen:
                    continue
                seen.add(ep_id)

                url = href if href.startswith("http") else f"https://tver.jp{href}"
                title = a.get("aria-label") or a.get_text(strip=True)
                if not title:
                    continue

                img = a.select_one("img[src]")
                thumb = img["src"] if img else None

                items.append(SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=ep_id,
                    url=url,
                    published_at=datetime.now(timezone.utc),
                    media_type="video",
                    title=title,
                    thumbnail_url=thumb,
                    content_text=None,
                    author=None,
                    raw_payload={},
                ))
                if len(items) >= 25:
                    break

            return items
        except Exception as exc:
            log.debug("TVer HTML fallback failed: %s", exc)
            return []
