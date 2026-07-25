#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.connectors.base import contains_keyword  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.ingestion.scheduler import _build_connectors  # noqa: E402
from app.models import CollectionMode  # noqa: E402
from app.relevance import primary_text_matches  # noqa: E402


DEFAULT_KEYWORDS = ["吉沢亮", "乃木坂46"]
KOREAN_ARTIST_KEYWORDS = ["BTS", "BLACKPINK", "IU", "NewJeans", "SEVENTEEN"]
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class SourceSmokeResult:
    platform: str
    fetched: int
    kept: int
    dropped: int
    samples: list[str]
    error: str | None = None


@dataclass
class PageCheck:
    url: str
    ok: bool
    status_code: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    error: str | None = None


@dataclass
class SourceCheck:
    platform: str
    ok: bool
    status: str
    keyword: str | None
    count: int
    seconds: float
    samples: list[dict[str, Any]]
    page_checks: list[PageCheck]
    error: str | None = None


async def check_connector(connector, keyword: str, mode: CollectionMode, sample_limit: int) -> SourceSmokeResult:
    try:
        items = await connector.fetch(keyword, mode)
    except Exception as exc:  # pragma: no cover - operational smoke helper
        return SourceSmokeResult(
            platform=connector.PLATFORM,
            fetched=0,
            kept=0,
            dropped=0,
            samples=[],
            error=str(exc),
        )

    dropped_samples: list[str] = []
    kept = 0
    for item in items:
        if primary_text_matches(keyword, item):
            kept += 1
            continue
        if len(dropped_samples) < sample_limit:
            dropped_samples.append((item.title or item.url or item.item_id or "").strip())

    return SourceSmokeResult(
        platform=connector.PLATFORM,
        fetched=len(items),
        kept=kept,
        dropped=len(items) - kept,
        samples=dropped_samples,
    )


def _build_filtered_connectors(platforms: set[str]):
    db = SessionLocal()
    try:
        connectors = _build_connectors(db)
    finally:
        db.close()

    if platforms:
        connectors = [connector for connector in connectors if connector.PLATFORM in platforms]
        missing = platforms - {connector.PLATFORM for connector in connectors}
        if missing:
            raise SystemExit(f"Unknown platform(s): {', '.join(sorted(missing))}")
    return connectors


async def run_smoke(keyword: str, platforms: set[str], sample_limit: int) -> list[SourceSmokeResult]:
    return await asyncio.gather(
        *[
            check_connector(connector, keyword, CollectionMode.ALL_INFO, sample_limit)
            for connector in _build_filtered_connectors(platforms)
        ]
    )


def _short(value: str | None, limit: int = 120) -> str | None:
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[: limit - 1] + "..."


def _sample_item(item: Any, keyword: str | None) -> dict[str, Any]:
    published = getattr(item, "published_at", None)
    if isinstance(published, datetime):
        published_value = published.isoformat()
    else:
        published_value = str(published) if published else None
    title = getattr(item, "title", None)
    content_text = getattr(item, "content_text", None)
    author = getattr(item, "author", None)
    primary_text = title or content_text
    relevance_match = primary_text_matches(keyword or "", item)
    raw_payload = getattr(item, "raw_payload", None) or {}
    date_source = raw_payload.get("date_source") if isinstance(raw_payload, dict) else None
    last_post_at = raw_payload.get("last_post_at") if isinstance(raw_payload, dict) else None
    subback_published_at = raw_payload.get("subback_published_at") if isinstance(raw_payload, dict) else None
    return {
        "title": _short(primary_text),
        "author": _short(author, 80),
        "keyword_match": contains_keyword(keyword or "", title, content_text, author),
        "primary_text_match": relevance_match,
        "media_type": getattr(item, "media_type", None),
        "published_at": published_value,
        "date_source": date_source,
        "last_post_at": last_post_at,
        "subback_published_at": subback_published_at,
        "url": getattr(item, "url", None),
    }


async def _check_page(client: httpx.AsyncClient, url: str) -> PageCheck:
    try:
        response = await client.get(url)
        content_type = response.headers.get("content-type", "").split(";")[0] or None
        return PageCheck(
            url=url,
            ok=200 <= response.status_code < 400,
            status_code=response.status_code,
            final_url=str(response.url),
            content_type=content_type,
        )
    except Exception as exc:
        return PageCheck(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")


async def _check_connector(connector: Any, keywords: list[str], mode: CollectionMode, page_limit: int) -> SourceCheck:
    started = time.perf_counter()
    last_error: str | None = None
    fetch_failed = False
    last_items: list[Any] = []
    used_keyword: str | None = None

    for keyword in keywords:
        used_keyword = keyword
        try:
            items = await connector.fetch(keyword, mode)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            fetch_failed = True
            continue
        last_items = list(items)
        if last_items:
            break

    samples = [_sample_item(item, used_keyword) for item in last_items[: max(page_limit, 3)]]
    urls = [sample["url"] for sample in samples[:page_limit] if sample.get("url")]
    page_checks: list[PageCheck] = []
    if urls:
        async with httpx.AsyncClient(
            timeout=18.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.9"},
        ) as client:
            page_checks = await asyncio.gather(*[_check_page(client, url) for url in urls])

    if fetch_failed:
        ok = False
        status = "fetch_failed"
    elif last_items:
        failed_pages = [page for page in page_checks if not page.ok]
        keyword_mismatches = [
            sample for sample in samples if not sample.get("primary_text_match")
        ]
        ok = not failed_pages and not keyword_mismatches
        if keyword_mismatches:
            status = "keyword_mismatch"
        elif failed_pages:
            status = "page_failed"
        else:
            status = "ok"
    elif last_error:
        ok = False
        status = "fetch_failed"
    else:
        ok = False
        status = "no_results"

    return SourceCheck(
        platform=connector.PLATFORM,
        ok=ok,
        status=status,
        keyword=used_keyword,
        count=len(last_items),
        seconds=round(time.perf_counter() - started, 2),
        samples=samples,
        page_checks=page_checks,
        error=last_error,
    )


async def _check_backend(base_url: str, keyword: str, limit: int) -> dict[str, Any]:
    headers = {}
    token = os.getenv("ADMIN_API_TOKEN") or settings.admin_api_token
    if token:
        headers["Authorization"] = f"Bearer {token}"

    base_url = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
        health = await client.get(f"{base_url}/api/health")
        health.raise_for_status()

        test_fetch = await client.get(f"{base_url}/api/admin/test-fetch", params={"keyword": keyword})
        test_fetch.raise_for_status()

        feed = await client.get(f"{base_url}/api/feed/", params={"limit": limit, "days": 365})
        feed.raise_for_status()

    feed_items = feed.json()
    counts = test_fetch.json()
    return {
        "health": health.json(),
        "test_fetch": counts,
        "feed_count": len(feed_items),
        "feed_platforms": sorted(
            {
                row.get("item", {}).get("platform")
                for row in feed_items
                if row.get("item", {}).get("platform")
            }
        ),
    }


def _print_simple_report(results: list[SourceSmokeResult]) -> int:
    failed = False
    for result in sorted(results, key=lambda item: item.platform):
        if result.error:
            failed = True
            print(f"ERROR {result.platform:<12} {result.error}")
            continue
        status = "PASS" if result.dropped == 0 else "DROP"
        print(
            f"{status} {result.platform:<12} "
            f"fetched={result.fetched:<3} kept={result.kept:<3} dropped={result.dropped:<3}"
        )
        for sample in result.samples:
            print(f"      - {sample}")
    return 1 if failed else 0


def _print_report(results: list[SourceCheck], backend: dict[str, Any] | None) -> None:
    print("\nSource smoke test")
    print("=================")
    for result in results:
        marker = "PASS" if result.ok else "FAIL"
        print(
            f"{marker:4} {result.platform:12} {result.status:12} "
            f"items={result.count:<3} keyword={result.keyword!r} time={result.seconds:.2f}s"
        )
        if result.error:
            print(f"      error: {result.error}")
        for sample in result.samples[:2]:
            sample_marker = "match" if sample.get("primary_text_match") else "no keyword"
            print(f"      - {_short(sample.get('title') or '(no title)', 100)}")
            print(f"        keyword: {sample_marker}")
            print(f"        {sample.get('url')}")
        for page in result.page_checks:
            page_marker = "ok" if page.ok else "bad"
            detail = page.status_code if page.status_code is not None else page.error
            print(f"        page {page_marker}: {detail} {page.content_type or ''}")

    if backend is not None:
        print("\nBackend display/API check")
        print("=========================")
        if backend.get("error"):
            print(f"error: {backend['error']}")
            return
        print(f"health: {backend['health']}")
        print(f"admin test-fetch counts: {backend['test_fetch']}")
        print(f"feed items returned: {backend['feed_count']}")
        print(f"feed platforms shown: {', '.join(backend['feed_platforms']) or '(none)'}")

    failed = [result for result in results if not result.ok]
    print("\nSummary")
    print("=======")
    print(f"passed={len(results) - len(failed)} failed={len(failed)} total={len(results)}")
    if failed:
        print("failed platforms: " + ", ".join(f"{r.platform}({r.status})" for r in failed))


def _write_json(path: Path, results: list[SourceCheck], backend: dict[str, Any] | None) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(),
        "results": [asdict(result) for result in results],
        "backend": backend,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test OshiReader source connectors and sampled result pages."
    )
    parser.add_argument(
        "-k",
        "--keyword",
        action="append",
        dest="keywords",
        help="Keyword to test. Can be repeated. Defaults to a small JP entertainment set.",
    )
    parser.add_argument(
        "--korean-artists",
        action="store_true",
        help="Test a representative Korean artist set: BTS, BLACKPINK, IU, NewJeans, SEVENTEEN.",
    )
    parser.add_argument(
        "--platform",
        action="append",
        default=[],
        help="Limit to a platform. Repeat for multiple platforms.",
    )
    parser.add_argument("--samples", type=int, default=3, help="Mismatch samples to print in simple mode.")
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Print kept/dropped counts using the same primary-text filter as ingestion.",
    )
    parser.add_argument(
        "--media-only",
        action="store_true",
        help="Use media_only collection mode instead of all_info.",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=1,
        help="How many result URLs per platform to load-check. Use 0 to skip page checks.",
    )
    parser.add_argument(
        "--backend-url",
        help="Optional running backend URL, e.g. http://127.0.0.1:8000, to verify API display/feed output.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Optional path to write a full JSON report.",
    )
    parser.add_argument(
        "--allow-empty",
        action="append",
        default=["twitter"],
        help="Platform allowed to return no results without failing. Can be repeated. Defaults to twitter.",
    )
    return parser.parse_args()


async def _main() -> int:
    args = parse_args()
    keywords = args.keywords or (KOREAN_ARTIST_KEYWORDS if args.korean_artists else DEFAULT_KEYWORDS)
    mode = CollectionMode.MEDIA_ONLY if args.media_only else CollectionMode.ALL_INFO
    platforms = set(args.platform)

    if args.simple:
        sample_limit = max(args.samples, 0)
        per_keyword = []
        for keyword in keywords:
            per_keyword.append(await run_smoke(keyword, platforms, sample_limit))
        merged: dict[str, SourceSmokeResult] = {}
        for keyword_results in per_keyword:
            for result in keyword_results:
                current = merged.setdefault(
                    result.platform,
                    SourceSmokeResult(
                        platform=result.platform,
                        fetched=0,
                        kept=0,
                        dropped=0,
                        samples=[],
                    ),
                )
                current.fetched += result.fetched
                current.kept += result.kept
                current.dropped += result.dropped
                remaining_samples = sample_limit - len(current.samples)
                if remaining_samples > 0:
                    current.samples.extend(result.samples[:remaining_samples])
                if result.error:
                    current.error = result.error
        results = list(merged.values())
        return _print_simple_report(results)

    connectors = _build_filtered_connectors(platforms)
    page_limit = max(args.page_limit, 0)
    results = await asyncio.gather(
        *[_check_connector(connector, keywords, mode, page_limit) for connector in connectors]
    )

    allowed_empty = set(args.allow_empty or [])
    for result in results:
        if result.status == "no_results" and result.platform in allowed_empty:
            result.ok = True
            result.status = "no_results_allowed"

    backend: dict[str, Any] | None = None
    if args.backend_url:
        try:
            backend = await _check_backend(args.backend_url, keywords[0], limit=50)
        except Exception as exc:
            backend = {"error": f"{type(exc).__name__}: {exc}"}

    _print_report(results, backend)
    if args.json_out:
        _write_json(args.json_out, results, backend)
        print(f"\nJSON report written to {args.json_out}")

    failed = [result for result in results if not result.ok]
    if backend and backend.get("error"):
        print(f"\nBackend check failed: {backend['error']}")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
