from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import feedparser
import httpx

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    SourceItemCreate,
    parse_feed_date,
    title_contains_keyword,
)

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
_DIRECT_REQUEST_CONCURRENCY = 4
_DIRECT_DAT_LIMIT = 50

# 5ch itself is often Cloudflare-blocked for server-side fetches.  2ch.sc mirrors
# the same board/thread formats and exposes subject.txt/dat files directly.
_DIRECT_BOARD_URLS: tuple[str, ...] = (
    "http://sweet.2ch.sc/headline/",
    "http://ai.2ch.sc/newsalpha/",
    "http://hayabusa3.2ch.sc/mnewsalpha/",
    "http://ai.2ch.sc/newsplus/",
    "http://hayabusa3.2ch.sc/mnewsplus/",
    "http://nozomi.2ch.sc/snsplus/",
    "http://hayabusa3.2ch.sc/news/",
    "http://ikura.2ch.sc/musicnews/",
    "http://anago.2ch.sc/geino/",
    "http://awabi.2ch.sc/drama/",
    "http://anago.2ch.sc/tvsaloon/",
    "http://toro.2ch.sc/tv/",
    "http://awabi.2ch.sc/tvd/",
    "http://anago.2ch.sc/am/",
    "http://nozomi.2ch.sc/idol/",
    "http://awabi.2ch.sc/akb/",
    "http://toro.2ch.sc/nogizaka/",
    "http://tarte.2ch.sc/keyakizaka46/",
    "http://awabi.2ch.sc/uraidol/",
    "http://anago.2ch.sc/indieidol/",
    "http://anago.2ch.sc/netidol/",
    "http://tarte.2ch.sc/akbsaloon/",
    "http://tarte.2ch.sc/idolplus/",
    "http://tarte.2ch.sc/world48/",
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
            subject_results = await _gather_limited(
                [self._fetch_subject(client, board_url, keyword) for board_url in _DIRECT_BOARD_URLS]
            )

            hits: list[_ThreadHit] = []
            seen: set[tuple[str, str, str]] = set()
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
        try:
            resp = await client.get(board_url.rstrip("/") + "/subject.txt")
            if not resp.is_success:
                log.debug("5ch subject returned status %d for %s", resp.status_code, board_url)
                return []
            return _parse_subject(_decode_shift_jis(resp.content), board_url, keyword)
        except Exception as exc:
            log.debug("5ch subject fetch error for %s: %s", board_url, exc)
            return []

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
        try:
            resp = await client.get(f"{hit.board_url}dat/{hit.thread_id}.dat")
            if not resp.is_success:
                return None
            return _parse_dat_latest_post_at(_decode_shift_jis(resp.content))
        except Exception as exc:
            log.debug("5ch dat fetch error for %s/%s: %s", hit.board_key, hit.thread_id, exc)
            return None

    async def _fetch_gnews(self, keyword: str) -> list[SourceItemCreate]:
        encoded = quote(f"{keyword} site:5ch.net OR site:2ch.sc")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP%3Aja"

        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    log.warning("5ch via Google News returned status %d", resp.status_code)
                    return []
            feed = await asyncio.to_thread(feedparser.parse, resp.content)
        except Exception as exc:
            log.warning("5ch Google News fetch error: %s", exc)
            return []

        items: list[SourceItemCreate] = []
        seen: set[str] = set()
        for entry in feed.entries[:25]:
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

        return items
