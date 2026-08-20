from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx

from app.connectors.base import (
    BaseConnector,
    CollectionMode,
    GOOGLE_NEWS_HEADERS,
    SourceItemCreate,
    gather_limited,
    is_recent_search_result,
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
_DEFAULT_5CH_PROXY_URL = "https://oshireader-feed-poller.oshireader-otterlymavis.workers.dev/fivech-proxy"

_SUBJECT_LINE_RE = re.compile(r"^(?P<thread_id>\d+)\.dat<>(?P<title>.+?)\s*\((?P<posts>\d+)\)\s*$")
_DAT_DATE_RE = re.compile(
    r"(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})"
    r"\([^)]+\)\s+"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
)
_ITEST_THREAD_RE = re.compile(
    r"^\*\s+\[(?P<date>\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}時\d{1,2}分)\s+"
    r"話題度:(?P<momentum>\d+)\s+(?P<posts>\d+)レス\s+(?P<title>.+?)\]"
    r"\((?P<url>https://itest\.5ch\.(?:io|net)/(?P<server>[^/]+)/test/read\.cgi/(?P<board>[^/]+)/(?P<thread_id>\d+))\)"
)
_ITEST_DATE_RE = re.compile(
    r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日\s+"
    r"(?P<hour>\d{1,2})時(?P<minute>\d{1,2})分"
)
_DIRECT_SUBJECT_CONCURRENCY = 4
_DIRECT_DAT_CONCURRENCY = 3
_ITEST_REQUEST_CONCURRENCY = 3
_ITEST_PROXY_ATTEMPTS = 3
_ITEST_JINA_ATTEMPTS = 3
_ITEST_DAT_CONCURRENCY = 1
_DIRECT_DAT_LIMIT = 25
_REAL_5CH_LIMIT = 25
_DIRECT_SUFFICIENT_RESULT_COUNT = 5
_DIRECT_EXTRA_SCAN_TIMEOUT_SECONDS = 24.0
# Advance by the intended extra-board scan window, not the per-batch concurrency.
# The timeout loop may process several subject batches before it stops.
_DIRECT_EXTRA_SCAN_CURSOR_STEP = 32
_DIRECT_EXTRA_SCAN_OFFSET_LIMIT = 512
_ITEST_FETCH_TIMEOUT_SECONDS = 5.0
_DIRECT_BBSMENU_URL = "http://menu.2ch.sc/bbsmenu.html"
_DIRECT_BBSMENU_CACHE_TTL_SECONDS = 3600.0

_ITEST_BOARD_KEYS: tuple[str, ...] = (
    "nogizaka",
    "keyakizaka46",
    "akb",
    "akbsaloon",
    "world48",
    "idol",
    "uraidol",
    "indieidol",
    "netidol",
    "idolplus",
    "geino",
    "mnewsplus",
    "mnewsalpha",
    "news",
    "newsplus",
    "musicnews",
    "drama",
    "tvsaloon",
    "tv",
    "tvd",
    "am",
    "musicj",
    "musicjm",
    "musicjf",
    "musicjg",
    "music",
    "streaming",
    "sns",
)

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
    "http://anago.2ch.sc/news5plus/",
    "http://sweet.2ch.sc/headline/",
    "http://ai.2ch.sc/newsalpha/",
    "http://ai.2ch.sc/newsplus/",
    "http://nozomi.2ch.sc/snsplus/",
    "http://hayabusa3.2ch.sc/news/",
    "http://hayabusa3.2ch.sc/news4viptasu/",
    "http://ikura.2ch.sc/musicnews/",
    "http://awabi.2ch.sc/drama/",
    "http://awabi.2ch.sc/cinema/",
    "http://anago.2ch.sc/tvsaloon/",
    "http://toro.2ch.sc/tv/",
    "http://awabi.2ch.sc/tvd/",
    "http://nozomi.2ch.sc/nhkdrama/",
    "http://anago.2ch.sc/cm/",
    "http://anago.2ch.sc/actor/",
    "http://anago.2ch.sc/mendol/",
    "http://toro.2ch.sc/sfx/",
    "http://maguro.2ch.sc/fortune/",
    "http://sweet.2ch.sc/patisserie/",
    "http://anago.2ch.sc/mass/",
    "http://ai.2ch.sc/kokusai/",
    "http://nozomi.2ch.sc/4649/",
    "http://anago.2ch.sc/am/",
    "http://awabi.2ch.sc/musicj/",
    "http://awabi.2ch.sc/musicjm/",
    "http://awabi.2ch.sc/musicjf/",
    "http://toro.2ch.sc/musicjg/",
    "http://awabi.2ch.sc/music/",
    "http://anago.2ch.sc/streaming/",
    "http://anago.2ch.sc/sns/",
)
_BBSMENU_BOARD_RE = re.compile(
    r"""href\s*=\s*["']?(?P<url>(?:https?:)?//[a-z0-9.-]+\.2ch\.sc/[a-z0-9_-]+/)["'\s>]""",
    re.IGNORECASE,
)
_direct_bbsmenu_cache: tuple[str, ...] | None = None
_direct_bbsmenu_cache_at = 0.0
_direct_bbsmenu_cache_lock = asyncio.Lock()
_direct_extra_scan_offsets: dict[str, int] = {}


@dataclass(frozen=True)
class _ThreadHit:
    board_url: str
    board_key: str
    host: str
    thread_id: str
    title: str
    posts: int


def _decode_shift_jis(content: bytes) -> str:
    return content.decode("shift_jis", errors="ignore")


def _board_parts(board_url: str) -> tuple[str, str]:
    parsed = urlparse(board_url)
    host = parsed.netloc
    board_key = parsed.path.strip("/").split("/")[-1]
    return host, board_key


def _normalize_board_url(url: str) -> str | None:
    raw_url = html.unescape(url.strip())
    if raw_url.startswith("//"):
        raw_url = f"http:{raw_url}"
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc.endswith(".2ch.sc"):
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 1 or not re.fullmatch(r"[a-z0-9_-]+", path_parts[0], re.IGNORECASE):
        return None
    return f"http://{parsed.netloc.lower()}/{path_parts[0]}/"


def _parse_bbsmenu_board_urls(text: str) -> tuple[str, ...]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in _BBSMENU_BOARD_RE.finditer(text):
        normalized = _normalize_board_url(match.group("url"))
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return tuple(urls)


def _merge_board_urls(*groups: tuple[str, ...]) -> tuple[str, ...]:
    urls: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for url in group:
            normalized = _normalize_board_url(url)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            urls.append(normalized)
    return tuple(urls)


def _cached_direct_board_urls() -> tuple[str, ...] | None:
    if _direct_bbsmenu_cache is None:
        return None
    if time.monotonic() - _direct_bbsmenu_cache_at > _DIRECT_BBSMENU_CACHE_TTL_SECONDS:
        return None
    return _direct_bbsmenu_cache


def _rotated_extra_board_urls(keyword: str, board_urls: tuple[str, ...]) -> tuple[str, ...]:
    if not board_urls:
        return board_urls
    key = keyword.strip().casefold()
    offset = _direct_extra_scan_offsets.get(key, 0) % len(board_urls)
    if key not in _direct_extra_scan_offsets and len(_direct_extra_scan_offsets) >= _DIRECT_EXTRA_SCAN_OFFSET_LIMIT:
        _direct_extra_scan_offsets.pop(next(iter(_direct_extra_scan_offsets)))
    _direct_extra_scan_offsets[key] = (offset + _DIRECT_EXTRA_SCAN_CURSOR_STEP) % len(board_urls)
    if offset == 0:
        return board_urls
    return board_urls[offset:] + board_urls[:offset]


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
    return bool(settings.admin_api_token.strip() and _fivech_proxy_urls())


def _fivech_proxy_urls() -> tuple[str, ...]:
    urls: list[str] = [_DEFAULT_5CH_PROXY_URL]
    configured = settings.source_5ch_proxy_url.strip()
    if configured and configured not in urls:
        urls.append(configured)
    return tuple(urls)


def _thread_url(hit: _ThreadHit) -> str:
    return f"http://{hit.host}/test/read.cgi/{hit.board_key}/{hit.thread_id}/"


def _thread_created_at(thread_id: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(thread_id), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


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


def _parse_itest_datetime(value: str) -> datetime | None:
    match = _ITEST_DATE_RE.search(value)
    if not match:
        return None
    try:
        local = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            tzinfo=JST,
        )
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


class FiveChConnector(BaseConnector):
    PLATFORM = "5ch"
    SUPPORTS_MEDIA_FILTER = False
    # The itest.5ch.io + proxy + jina fallback chain needs longer than the
    # scheduler's default fetch deadline.
    MIN_FETCH_TIMEOUT_SECONDS = 35.0

    async def fetch(self, keyword: str, mode: CollectionMode) -> list[SourceItemCreate]:
        if mode == CollectionMode.MEDIA_ONLY:
            return []

        direct_items = await self._fetch_direct(keyword)
        parsed_direct_items = [
            item for item in direct_items
            if (item.raw_payload or {}).get("date_parsed") is True
        ]
        if len(parsed_direct_items) >= _DIRECT_SUFFICIENT_RESULT_COUNT:
            return parsed_direct_items[:_REAL_5CH_LIMIT]

        try:
            items = await asyncio.wait_for(
                self._fetch_real_itest(keyword),
                timeout=_ITEST_FETCH_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, TimeoutError):
            log.warning("5ch real itest scan timed out for %r; using 2ch.sc latest-reply results", keyword)
            items = []

        if items:
            if parsed_direct_items:
                items = self._merge_latest_reply_items([*items, *parsed_direct_items])
            return items[:_REAL_5CH_LIMIT]
        if direct_items:
            return self._prioritize_parsed_direct_items(direct_items)
        log.warning("5ch latest-reply scans returned no items for %r; no source-dated fallback available", keyword)
        return []

    def _prioritize_parsed_direct_items(
        self,
        items: list[SourceItemCreate],
    ) -> list[SourceItemCreate]:
        return sorted(
            items,
            key=lambda item: (
                bool((item.raw_payload or {}).get("date_parsed") is True),
                item.published_at,
            ),
            reverse=True,
        )[:_REAL_5CH_LIMIT]

    def _merge_latest_reply_items(self, items: list[SourceItemCreate]) -> list[SourceItemCreate]:
        by_key: dict[str, SourceItemCreate] = {}
        for item in items:
            key = self._thread_merge_key(item)
            existing = by_key.get(key)
            if existing is None or item.published_at > existing.published_at:
                by_key[key] = item
        merged = list(by_key.values())
        merged.sort(key=lambda item: item.published_at, reverse=True)
        return merged

    def _thread_merge_key(self, item: SourceItemCreate) -> str:
        payload = item.raw_payload or {}
        board = payload.get("board")
        thread_id = payload.get("thread_id")
        if board and thread_id:
            return f"thread:{board}:{thread_id}"
        return f"url:{item.url.rstrip('/')}"

    async def _fetch_real_itest(self, keyword: str) -> list[SourceItemCreate]:
        items: list[SourceItemCreate] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=GOOGLE_NEWS_HEADERS) as client:
            for start in range(0, len(_ITEST_BOARD_KEYS), _ITEST_REQUEST_CONCURRENCY):
                board_batch = _ITEST_BOARD_KEYS[start:start + _ITEST_REQUEST_CONCURRENCY]
                board_results = await gather_limited(
                    [self._fetch_itest_board(client, board_key, keyword) for board_key in board_batch],
                    concurrency=_ITEST_REQUEST_CONCURRENCY,
                )

                for result in board_results:
                    if isinstance(result, Exception):
                        log.debug("5ch itest board scan failed: %s", result)
                        continue
                    for item in result:
                        if item.item_id in seen:
                            continue
                        seen.add(item.item_id)
                        items.append(item)

        items.sort(key=lambda item: item.published_at, reverse=True)
        return items[:_REAL_5CH_LIMIT]

    async def _fetch_itest_board(
        self,
        client: httpx.AsyncClient,
        board_key: str,
        keyword: str,
    ) -> list[SourceItemCreate]:
        url = f"https://r.jina.ai/http://https://itest.5ch.io/subback/{board_key}"
        text = await self._fetch_itest_board_via_proxy(client, board_key)
        try:
            if text is None:
                for attempt in range(1, _ITEST_JINA_ATTEMPTS + 1):
                    response = await client.get(url)
                    if response.is_success:
                        text = response.text
                        break
                    log.debug(
                        "5ch itest board returned %d for %s attempt %d",
                        response.status_code,
                        board_key,
                        attempt,
                    )
                    if attempt < _ITEST_JINA_ATTEMPTS:
                        await asyncio.sleep(0.5 * attempt)
        except Exception as exc:
            log.debug("5ch itest board fetch error for %s: %s", board_key, exc)
        if not text:
            return []

        items = self._parse_itest_board(text, board_key, keyword)
        if not items:
            return []
        dated = await gather_limited(
            [self._apply_itest_latest_post_at(client, item) for item in items],
            concurrency=_ITEST_DAT_CONCURRENCY,
        )
        return [
            item for item in dated
            if (
                isinstance(item, SourceItemCreate)
                and (item.raw_payload or {}).get("date_source") == "dat_latest_post"
                and is_recent_search_result(item.published_at)
            )
        ]

    def _parse_itest_board(
        self,
        text: str,
        board_key: str,
        keyword: str,
    ) -> list[SourceItemCreate]:
        items: list[SourceItemCreate] = []
        for line in text.splitlines():
            match = _ITEST_THREAD_RE.match(line.strip())
            if not match:
                continue
            title = html.unescape(match.group("title")).strip()
            if not title_contains_keyword(keyword, title):
                continue
            server = match.group("server")
            board = match.group("board")
            thread_id = match.group("thread_id")
            subback_at = _parse_itest_datetime(match.group("date"))
            date_parsed = subback_at is not None
            if subback_at is None:
                subback_at = _thread_created_at(thread_id)
            if subback_at is None:
                continue
            items.append(
                SourceItemCreate(
                    platform=self.PLATFORM,
                    item_id=f"5ch:{server}:{board}:{thread_id}",
                    url=f"https://itest.5ch.io/{server}/test/read.cgi/{board}/{thread_id}",
                    published_at=subback_at,
                    media_type="text",
                    title=title,
                    content_text=f"{match.group('posts')} posts",
                    thumbnail_url=None,
                    raw_payload={
                        "source": "5ch_itest",
                        "keyword": keyword,
                        "server": server,
                        "board": board,
                        "thread_id": thread_id,
                        "posts": int(match.group("posts")),
                        "momentum": int(match.group("momentum")),
                        "subback_published_at": subback_at.isoformat(),
                        "date_source": "subback",
                        "date_parsed": date_parsed,
                    },
                )
            )
            if len(items) >= _REAL_5CH_LIMIT:
                break
        return items

    async def _apply_itest_latest_post_at(
        self,
        client: httpx.AsyncClient,
        item: SourceItemCreate,
    ) -> Optional[SourceItemCreate]:
        payload = item.raw_payload or {}
        latest = await self._fetch_itest_latest_post_at(
            client,
            str(payload.get("server") or ""),
            str(payload.get("board") or ""),
            str(payload.get("thread_id") or ""),
        )
        if latest is not None:
            item.published_at = latest
            item.raw_payload = {
                **payload,
                "last_post_at": latest.isoformat(),
                "date_source": "dat_latest_post",
                "date_parsed": True,
            }
        return item

    async def _fetch_itest_board_via_proxy(self, client: httpx.AsyncClient, board_key: str) -> str | None:
        token = settings.admin_api_token.strip()
        if not token:
            return None
        for proxy_url in _fivech_proxy_urls():
            url = f"{proxy_url}?{urlencode({'resource': 'itest_subback', 'board_key': board_key})}"
            for attempt in range(1, _ITEST_PROXY_ATTEMPTS + 1):
                try:
                    response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                    if response.is_success and response.text:
                        return response.text
                    log.debug(
                        "5ch itest proxy returned %d for %s via %s attempt %d",
                        response.status_code,
                        board_key,
                        proxy_url,
                        attempt,
                    )
                except Exception as exc:
                    log.debug(
                        "5ch itest proxy fetch error for %s via %s attempt %d: %s",
                        board_key,
                        proxy_url,
                        attempt,
                        exc,
                    )
                if attempt < _ITEST_PROXY_ATTEMPTS:
                    await asyncio.sleep(0.5 * attempt)
        return None

    async def _fetch_itest_latest_post_at(
        self,
        client: httpx.AsyncClient,
        server: str,
        board_key: str,
        thread_id: str,
    ) -> datetime | None:
        text = await self._fetch_itest_dat_via_proxy(client, server, board_key, thread_id)
        if text:
            latest = _parse_dat_latest_post_at(text)
            if latest is not None:
                return latest

        if (
            not re.fullmatch(r"[a-z0-9-]{2,32}", server)
            or board_key not in _ITEST_BOARD_KEYS
            or not re.fullmatch(r"\d{9,12}", thread_id)
        ):
            return None
        url = f"https://r.jina.ai/http://http://{server}.5ch.net/{board_key}/dat/{thread_id}.dat"
        try:
            for attempt in range(1, _ITEST_JINA_ATTEMPTS + 1):
                response = await client.get(url)
                if response.is_success and response.text:
                    latest = _parse_dat_latest_post_at(response.text)
                    if latest is not None:
                        return latest
                else:
                    log.debug(
                        "5ch itest dat returned %d for %s/%s/%s attempt %d",
                        response.status_code,
                        server,
                        board_key,
                        thread_id,
                        attempt,
                    )
                if attempt < _ITEST_JINA_ATTEMPTS:
                    await asyncio.sleep(0.5 * attempt)
        except Exception as exc:
            log.debug("5ch itest dat fetch error for %s/%s/%s: %s", server, board_key, thread_id, exc)
        return None

    async def _fetch_itest_dat_via_proxy(
        self,
        client: httpx.AsyncClient,
        server: str,
        board_key: str,
        thread_id: str,
    ) -> str | None:
        token = settings.admin_api_token.strip()
        if not token:
            return None
        if (
            not re.fullmatch(r"[a-z0-9-]{2,32}", server)
            or board_key not in _ITEST_BOARD_KEYS
            or not re.fullmatch(r"\d{9,12}", thread_id)
        ):
            return None
        params = {
            "resource": "itest_dat",
            "server": server,
            "board_key": board_key,
            "thread_id": thread_id,
        }
        for proxy_url in _fivech_proxy_urls():
            url = f"{proxy_url}?{urlencode(params)}"
            try:
                response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                if response.is_success and response.text:
                    return response.text
                log.debug(
                    "5ch itest dat proxy returned %d for %s/%s/%s via %s",
                    response.status_code,
                    server,
                    board_key,
                    thread_id,
                    proxy_url,
                )
            except Exception as exc:
                log.debug(
                    "5ch itest dat proxy fetch error for %s/%s/%s via %s: %s",
                    server,
                    board_key,
                    thread_id,
                    proxy_url,
                    exc,
                )
        return None

    async def _fetch_direct(self, keyword: str) -> list[SourceItemCreate]:
        async with httpx.AsyncClient(timeout=8.0, headers=HEADERS, follow_redirects=True) as client:
            seen: set[tuple[str, str, str]] = set()
            priority_hits = await self._fetch_direct_hits(client, _DIRECT_BOARD_URLS, keyword, seen)
            priority_items = await self._build_direct_items(client, priority_hits, keyword)
            board_urls = await self._fetch_direct_board_urls(client)
            priority_urls = set(_merge_board_urls(_DIRECT_BOARD_URLS))
            extra_board_urls = tuple(url for url in board_urls if url not in priority_urls)
            extra_board_urls = _rotated_extra_board_urls(keyword, extra_board_urls)
            extra_hits = await self._fetch_direct_hits(
                client,
                extra_board_urls,
                keyword,
                seen,
                remaining=max(0, _DIRECT_DAT_LIMIT - len(priority_hits)),
                timeout_seconds=_DIRECT_EXTRA_SCAN_TIMEOUT_SECONDS,
            )
            extra_items = await self._build_direct_items(client, extra_hits, keyword)

        items = [*priority_items, *extra_items]
        items.sort(key=lambda item: item.published_at, reverse=True)
        return items[:_REAL_5CH_LIMIT]

    async def _fetch_direct_hits(
        self,
        client: httpx.AsyncClient,
        board_urls: tuple[str, ...],
        keyword: str,
        seen: set[tuple[str, str, str]],
        remaining: int = _DIRECT_DAT_LIMIT,
        timeout_seconds: float | None = None,
    ) -> list[_ThreadHit]:
        hits: list[_ThreadHit] = []
        if remaining <= 0:
            return hits
        deadline = (
            time.monotonic() + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        for start in range(0, len(board_urls), _DIRECT_SUBJECT_CONCURRENCY):
            if deadline is not None and time.monotonic() >= deadline:
                log.warning("5ch expanded 2ch.sc board scan timed out for %r; using partial results", keyword)
                return hits
            board_batch = board_urls[start:start + _DIRECT_SUBJECT_CONCURRENCY]
            tasks = [
                asyncio.create_task(self._fetch_subject(client, board_url, keyword))
                for board_url in board_batch
            ]
            try:
                timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
                for task in asyncio.as_completed(tasks, timeout=timeout):
                    try:
                        result = await task
                    except asyncio.TimeoutError:
                        log.warning("5ch expanded 2ch.sc board scan timed out for %r; using partial results", keyword)
                        return hits
                    except Exception as exc:
                        log.debug("5ch subject scan failed: %s", exc)
                        continue
                    for hit in result:
                        key = (hit.host, hit.board_key, hit.thread_id)
                        if key in seen:
                            continue
                        seen.add(key)
                        hits.append(hit)
                        if len(hits) >= remaining:
                            return hits
            finally:
                pending = [task for task in tasks if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
        return hits

    async def _build_direct_items(
        self,
        client: httpx.AsyncClient,
        hits: list[_ThreadHit],
        keyword: str,
    ) -> list[SourceItemCreate]:
        if not hits:
            return []
        dated = await gather_limited(
            [self._build_direct_item(client, hit, keyword) for hit in hits[:_DIRECT_DAT_LIMIT]],
            concurrency=_DIRECT_DAT_CONCURRENCY,
        )
        return [item for item in dated if isinstance(item, SourceItemCreate)]

    async def _fetch_direct_board_urls(self, client: httpx.AsyncClient) -> tuple[str, ...]:
        cached = _cached_direct_board_urls()
        if cached is not None:
            return cached

        async with _direct_bbsmenu_cache_lock:
            cached = _cached_direct_board_urls()
            if cached is not None:
                return cached
            return await self._refresh_direct_board_urls(client)

    async def _refresh_direct_board_urls(self, client: httpx.AsyncClient) -> tuple[str, ...]:
        global _direct_bbsmenu_cache, _direct_bbsmenu_cache_at
        try:
            response = await client.get(_DIRECT_BBSMENU_URL)
            text = response.text if isinstance(getattr(response, "text", None), str) else _decode_shift_jis(response.content)
            if response.is_success and text:
                menu_urls = _parse_bbsmenu_board_urls(text)
                if menu_urls:
                    merged = _merge_board_urls(_DIRECT_BOARD_URLS, menu_urls)
                    _direct_bbsmenu_cache = merged
                    _direct_bbsmenu_cache_at = time.monotonic()
                    return merged
                log.debug("5ch bbsmenu returned no parseable board URLs")
            else:
                log.debug("5ch bbsmenu returned status %d", response.status_code)
        except Exception as exc:
            log.debug("5ch bbsmenu fetch error: %s", exc)
        if _direct_bbsmenu_cache is not None:
            return _direct_bbsmenu_cache
        return _DIRECT_BOARD_URLS

    async def _fetch_subject(
        self,
        client: httpx.AsyncClient,
        board_url: str,
        keyword: str,
    ) -> list[_ThreadHit]:
        if _fivech_proxy_configured():
            content = await self._fetch_proxy_resource(client, board_url, "subject")
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
        return await self._fetch_subject_via_proxy(client, board_url, keyword)

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
        if published_at is None:
            return None

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
            published_at = await self._fetch_latest_post_at_via_proxy(client, hit)
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
        return await self._fetch_latest_post_at_via_proxy(client, hit)

    async def _fetch_subject_via_proxy(
        self,
        client: httpx.AsyncClient,
        board_url: str,
        keyword: str,
    ) -> list[_ThreadHit]:
        content = await self._fetch_proxy_resource(client, board_url, "subject")
        if not content:
            return []
        return _parse_subject(_decode_shift_jis(content), board_url, keyword)

    async def _fetch_latest_post_at_via_proxy(
        self,
        client: httpx.AsyncClient,
        hit: _ThreadHit,
    ) -> datetime | None:
        content = await self._fetch_proxy_resource(client, hit.board_url, "dat", hit.thread_id)
        if not content:
            return None
        return _parse_dat_latest_post_at(_decode_shift_jis(content))

    async def _fetch_proxy_resource(
        self,
        client: httpx.AsyncClient,
        board_url: str,
        resource: str,
        thread_id: str | None = None,
    ) -> bytes | None:
        token = settings.admin_api_token.strip()
        if not token:
            return None
        params = {"board_url": board_url, "resource": resource}
        if thread_id:
            params["thread_id"] = thread_id
        for proxy_url in _fivech_proxy_urls():
            url = f"{proxy_url}?{urlencode(params)}"
            try:
                response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                if response.is_success:
                    return response.content
            except Exception as exc:
                log.debug("5ch proxy fetch error for %s %s via %s: %s", board_url, resource, proxy_url, exc)
        return None
