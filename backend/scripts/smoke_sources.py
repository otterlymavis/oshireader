from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.ingestion.scheduler import _build_connectors  # noqa: E402
from app.models import CollectionMode  # noqa: E402
from app.relevance import primary_text_matches  # noqa: E402


@dataclass
class SourceSmokeResult:
    platform: str
    fetched: int
    kept: int
    dropped: int
    samples: list[str]
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


async def run_smoke(keyword: str, platforms: set[str], sample_limit: int) -> list[SourceSmokeResult]:
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

    return await asyncio.gather(
        *[
            check_connector(connector, keyword, CollectionMode.ALL_INFO, sample_limit)
            for connector in connectors
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch sources for a keyword and show which raw items ingestion would drop."
    )
    parser.add_argument("--keyword", required=True, help="Keyword to fetch and validate.")
    parser.add_argument(
        "--platform",
        action="append",
        default=[],
        help="Limit to a platform. Repeat for multiple platforms.",
    )
    parser.add_argument("--samples", type=int, default=3, help="Mismatch samples to print per platform.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results = asyncio.run(run_smoke(args.keyword, set(args.platform), max(args.samples, 0)))
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


if __name__ == "__main__":
    raise SystemExit(main())
