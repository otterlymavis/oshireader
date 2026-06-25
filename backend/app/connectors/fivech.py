from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlparse

import feedparser
import httpx

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    GOOGLE_NEWS_HEADERS,
    SourceItemCreate,
    fetch_search_rss_via_proxy,
    parse_feed_date,
    parse_google_news_markdown,
    title_contains_keyword,
)
from app.config import settings

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Monazilla/1.00 OshiReader/1.0",
    "Accept": "text/plain,text/html;q=0.8,*/*;q=0.5",
    "Accept-Language": "ja,en;q=0.9",
}

JST = timezone(timedelta(hours=9))

_SUBJECT_LINE_RE = re.compile(r"^(?P<thread_id>\d+)\.dat<>(?P<title>.+?)\s*\((?P<posts>\d+)\)\s*$")
_DAT_DATE_RE = re.compile(
    r"(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})"
    r"\([^)]+\)\s+"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
)
_DIRECT_REQUEST_CONCURRENCY = 12
_DIRECT_DAT_LIMIT = 25

# 5ch itself is often Cloudflare-blocked for server-side fetches.  2ch.sc mirrors
# the same board/thread formats and exposes subject.txt/dat files directly.
_DIRECT_BOARD_URLS: tuple[str, ...] = (
    "http://toro.2ch.sc/nogizaka/",
    "http://tarte.2ch.sc/keyakizaka46/",
    "http://awabi.2ch.sc/akb/",
    "http://tarte.2ch.sc/akbsaloon/",
    "http://tarte.2ch.sc/world48/",
    "http://nozomi.2ch.sc/idol/",
    "http://awabi.2ch.sc/uraidol/",
    "http://anago.2ch.sc/indieidol/",
    "http://anago.2ch.sc/netidol/",
    "http://tarte.2ch.sc/idolplus/",
    "http://anago.2ch.sc/geino/",
    "http://hayabusa3.2ch.sc/mnewsalpha/",
    "http://hayabusa3.2ch.sc/mnewsplus/",
    "http://sweet.2ch.sc/headline/",
    "http://ai.2ch.sc/newsalpha/",
    "http://ai.2ch.sc/newsplus/",
    "http://nozomi.2ch.sc/snsplus/",
    "http://hayabusa3.2ch.sc/news/",
    "http://ikura.2ch.sc/musicnews/",
    "http://awabi.2ch.sc/drama/",
    "http://anago.2ch.sc/tvsaloon/",
    "http://toro.2ch.sc/tv/",
    "http://awabi.2ch.sc/tvd/",
    "http://anago.2ch.sc/am/",
    "http://awabi.2ch.sc/musicj/",
    "http://awabi.2ch.sc/musicjm/",
    "http://awabi.2ch.sc/musicjf/",
    "http://toro.2ch.sc/musicjg/",
    "http://awabi.2ch.sc/music/",
    "http://anago.2ch.sc/streaming/",
    "http://anago.2ch.sc/sns/",
)


@dataclass(frozen=True)
class _ThreadHit:
    board_url: str
    board_key: str
    host: str
    thread_id: str
    title: str
    posts: int


async def _gather_limited(coros):
    semaphore = asyncio.Semaphore(_DIRECT_REQUEST_CONCURRENCY)

    async def run(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*(run(coro) for coro in coros), return_exceptions=True)


def _decode_shift_jis(content: bytes) -> str:
    return content.decode("shift_jis", errors="ignore")


def _board_parts(board_url: str) -> tuple[str, str]:
    parsed = urlparse(board_url)
    host = parsed.netloc
    board_key = parsed.path.strip("/").split("/")[-1]
    return host, board_key


def _parse_subject(text: str, board_url: str, keyword: str) -> list[_ThreadHit]:
    host, board_key = _board_parts(board_url)
    hits: list[_ThreadHit] = []
    for line in text.splitlines():
        match = _SUBJECT_LINE_RE.match(line.strip())
        if not match:
            continue
        title = html.unescape(match.group("title")).strip()
        if not title_contains_keyword(keyword, title):
            continue
        hits.append(
            _ThreadHit(
                board_url=board_url.rstrip("/") + "/",
                board_key=board_key,
                host=host,
                thread_id=match.group("thread_id"),
                title=title,
                posts=int(match.group("posts")),
            )
        )
    return hits


def _has_subject_rows(text: str) -> bool:
    return any(_SUBJECT_LINE_RE.match(line.strip()) for line in text.splitlines())


def _fivech_proxy_configured() -> bool:
    return bool(settings.source_5ch_proxy_url.strip() and settings.admin_api_token.strip())


def _thread_url(hit: _ThreadHit) -> str:
    return f"http://{hit.host}/test/read.cgi/{hit.board_key}/{hit.thread_id}/"


def _thread_created_at(thread_id: str) -> datetime:
    try:
        return datetime.fromtimestamp(int(thread_id), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def _parse_dat_latest_post_at(text: str) -> datetime | None:
    for line in reversed([line for line in text.splitlines() if line.strip()]):
        parts = line.split("<>")
        if len(parts) < 3:
            continue
        match = _DAT_DATE_RE.search(parts[2])
        if not match:
            continue
        try:
            local = datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                int(match.group("hour")),
                int(match.group("minute")),
                int(match.group("second")),
                tzinfo=JST,
            )
        except ValueError:
            continue
        return local.astimezone(timezone.utc)
    return None


class FiveChConnector(BaseConnector):
    PLATFORM = "5ch"
    SUPPORTS_MEDIA_FILTER = False

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        items = await self._fetch_direct(keyword)
        if items:
            return items
        log.warning("5ch direct thread scan returned no items for %r; using Google News fallback", keyword)
        return await self._fetch_gnews(keyword)

    async def _fetch_direct(self, keyword: str) -> list[SourceItemCreate]:
        async with httpx.AsyncClient(timeout=8.0, headers=HEADERS, follow_redirects=True) as client:
            hits: list[_ThreadHit] = []
            seen: set[tuple[str, str, str]] = set()
            for start in range(0, len(_DIRECT_BOARD_URLS), _DIRECT_REQUEST_CONCURRENCY):
                board_batch = _DIRECT_BOARD_URLS[start:start + _DIRECT_REQUEST_CONCURRENCY]
                subject_results = await _gather_limited(
                    [self._fetch_subject(client, board_url, keyword) for board_url in board_batch]
                )

                for result in subject_results:
                    if isinstance(result, Exception):
                        log.debug("5ch subject scan failed: %s", result)
                        continue
                    for hit in result:
                        key = (hit.host, hit.board_key, hit.thread_id)
                        if key in seen:
                            continue
                        seen.add(key)
                        hits.append(hit)
                if len(hits) >= _DIRECT_DAT_LIMIT:
                    break

            if not hits:
                return []

            dated = await _gather_limited(
                [self._build_direct_item(client, hit, keyword) for hit in hits[:_DIRECT_DAT_LIMIT]]
            )

        items: list[SourceItemCreate] = [
            item for item in dated if isinstance(item, SourceItemCreate)
        ]
        items.sort(key=lambda item: item.published_at, reverse=True)
        return items[:25]

    async def _fetch_subject(
        self,
        client: httpx.AsyncClient,
        board_url: str,
        keyword: str,
    ) -> list[_ThreadHit]:
        if _fivech_proxy_configured():
            content = await self._fetch_proxy_resource(board_url, "subject")
            if content:
                text = _decode_shift_jis(content)
                hits = _parse_subject(text, board_url, keyword)
                if hits or _has_subject_rows(text):
                    return hits
        try:
            resp = await client.get(board_url.rstrip("/") + "/subject.txt")
            if resp.is_success:
                text = _decode_shift_jis(resp.content)
                hits = _parse_subject(text, board_url, keyword)
                if hits or _has_subject_rows(text):
                    return hits
                log.debug("5ch subject response had no parseable rows for %s", board_url)
            log.debug("5ch subject returned status %d for %s", resp.status_code, board_url)
        except Exception as exc:
            log.debug("5ch subject fetch error for %s: %s", board_url, exc)
        return await self._fetch_subject_via_proxy(board_url, keyword)

    async def _build_direct_item(
        self,
        client: httpx.AsyncClient,
        hit: _ThreadHit,
        keyword: str,
    ) -> SourceItemCreate:
        published_at = await self._fetch_latest_post_at(client, hit)
        date_parsed = published_at is not None
        if published_at is None:
            published_at = _thread_created_at(hit.thread_id)

        return SourceItemCreate(
            platform=self.PLATFORM,
            item_id=f"2ch.sc:{hit.host}:{hit.board_key}:{hit.thread_id}",
            url=_thread_url(hit),
            published_at=published_at,
            media_type="text",
            title=hit.title,
            content_text=f"{hit.posts} posts",
            thumbnail_url=None,
            raw_payload={
                "source": "2ch.sc_subject",
                "keyword": keyword,
                "host": hit.host,
                "board": hit.board_key,
                "thread_id": hit.thread_id,
                "posts": hit.posts,
                "date_parsed": date_parsed,
            },
        )

    async def _fetch_latest_post_at(
        self,
        client: httpx.AsyncClient,
        hit: _ThreadHit,
    ) -> datetime | None:
        if _fivech_proxy_configured():
            published_at = await self._fetch_latest_post_at_via_proxy(hit)
            if published_at is not None:
                return published_at
        try:
            resp = await client.get(f"{hit.board_url}dat/{hit.thread_id}.dat")
            if resp.is_success:
                published_at = _parse_dat_latest_post_at(_decode_shift_jis(resp.content))
                if published_at is not None:
                    return published_at
        except Exception as exc:
            log.debug("5ch dat fetch error for %s/%s: %s", hit.board_key, hit.thread_id, exc)
        return await self._fetch_latest_post_at_via_proxy(hit)

    async def _fetch_subject_via_proxy(self, board_url: str, keyword: str) -> list[_ThreadHit]:
        content = await self._fetch_proxy_resource(board_url, "subject")
        if not content:
            return []
        return _parse_subject(_decode_shift_jis(content), board_url, keyword)

    async def _fetch_latest_post_at_via_proxy(self, hit: _ThreadHit) -> datetime | None:
        content = await self._fetch_proxy_resource(hit.board_url, "dat", hit.thread_id)
        if not content:
            return None
        return _parse_dat_latest_post_at(_decode_shift_jis(content))

    async def _fetch_proxy_resource(
        self,
        board_url: str,
        resource: str,
        thread_id: str | None = None,
    ) -> bytes | None:
        proxy_url = settings.source_5ch_proxy_url.strip()
        token = settings.admin_api_token.strip()
        if not proxy_url or not token:
            return None
        params = {"board_url": board_url, "resource": resource}
        if thread_id:
            params["thread_id"] = thread_id
        url = f"{proxy_url}?{urlencode(params)}"
        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
                response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                if not response.is_success:
                    return None
                return response.content
        except Exception as exc:
            log.debug("5ch proxy fetch error for %s %s: %s", board_url, resource, exc)
            return None

    async def _fetch_gnews(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(f"{keyword} site:5ch.net OR site:2ch.sc")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"

        feed = None
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("5ch via Google News returned status %d", resp.status_code)
                else:
                    feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("5ch Google News fetch error: %s", exc)

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in (feed.entries if feed else [])[:25]:
            link = entry.get("link", "")
            if not link:
                continue
            item_id = entry.get("id") or link
            if item_id in seen:
                continue
            seen.add(item_id)
            title = (entry.get("title") or "").strip()
            summary = entry.get("summary") or ""
            if not title:
                continue
            if not title_contains_keyword(keyword, title):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=item_id,
                    url=link,
                    published_at=parse_feed_date(entry),
                    media_type="text",
                    title=title,
                    content_text=summary or None,
                    thumbnail_url=None,
                    raw_payload={"source": "google_news", "keyword": keyword},
                )
            )

        if items:
            return items
        items = await self._fetch_gnews_jina(keyword, url)
        if items:
            return items
        return await self._fetch_gnews_proxy(keyword)

    async def _fetch_gnews_jina(self, keyword: str, google_news_url: str) -> list[SourceItemCreate]:
        proxy_url = "https://r.jina.ai/http://" + google_news_url.replace("https://", "")
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
                resp = await client.get(proxy_url)
                if not resp.is_success:
                    log.warning("5ch Google News Jina fallback returned status %d", resp.status_code)
                    return []
        except Exception as exc:
            log.warning("5ch Google News Jina fallback error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        for entry in parse_google_news_markdown(resp.text)[:25]:
            title = entry["title"]
            if not title_contains_keyword(keyword, title):
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=entry["url"],
                    url=entry["url"],
                    published_at=entry["published_at"],
                    media_type="text",
                    title=title,
                    content_text=None,
                    thumbnail_url=None,
                    raw_payload={"source": "google_news_jina", "keyword": keyword},
                )
            )
        return items

    async def _fetch_gnews_proxy(self, keyword: str) -> list[SourceItemCreate]:
        query = f"{keyword} site:5ch.net OR site:2ch.sc"
        for target, source in (("google", "google_news_proxy"), ("bing", "bing_news_proxy")):
            content = await fetch_search_rss_via_proxy(query, target=target)
            if not content:
                continue
            feed = await asyncio.to_thread(feedparser.parse, content)
            items: list[SourceItemCreate] = []
            seen: set[str] = set()
            for entry in feed.entries[:25]:
                link = entry.get("link", "")
                item_id = entry.get("id") or link
                title = (entry.get("title") or "").strip()
                if not link or not title or item_id in seen:
                    continue
                if not title_contains_keyword(keyword, title):
                    continue
                seen.add(item_id)
                items.append(
                    SourceItemCreate(
                        platform=self.PLATFORM,
                        item_id=item_id,
                        url=link,
                        published_at=parse_feed_date(entry),
                        media_type="text",
                        title=title,
                        content_text=entry.get("summary") or None,
                        thumbnail_url=None,
                        raw_payload={"source": source, "keyword": keyword},
                    )
                )
            if items:
                return items
        return []
