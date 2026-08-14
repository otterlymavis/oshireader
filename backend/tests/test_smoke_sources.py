from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.connectors.base import SourceItemCreate
from app.models import CollectionMode
from scripts.smoke_sources import KOREAN_ARTIST_KEYWORDS, _check_connector, check_connector, parse_args


def test_korean_artist_smoke_preset_is_representative_and_unique():
    assert KOREAN_ARTIST_KEYWORDS == ["BTS", "BLACKPINK", "IU", "NewJeans", "SEVENTEEN"]
    assert len(KOREAN_ARTIST_KEYWORDS) == len(set(KOREAN_ARTIST_KEYWORDS))


def test_live_smoke_does_not_exempt_tokenless_twitter_from_empty_failures(monkeypatch):
    monkeypatch.setattr("sys.argv", ["smoke_sources.py"])

    assert parse_args().allow_empty == []


class FakeConnector:
    PLATFORM = "fake"

    def __init__(self, items=None, error: Exception | None = None):
        self.items = items or []
        self.error = error

    async def fetch(self, keyword: str, mode: CollectionMode):
        if self.error:
            raise self.error
        return self.items


class ErrorThenItemsConnector:
    PLATFORM = "fake"

    def __init__(self):
        self.calls = 0

    async def fetch(self, keyword: str, mode: CollectionMode):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("first keyword failed")
        return [_item("Aiko result")]


def _item(
    title: str,
    item_id: str = "item",
    content_text: str | None = None,
    platform: str = "fake",
    raw_payload: dict | None = None,
) -> SourceItemCreate:
    return SourceItemCreate(
        platform=platform,
        item_id=item_id,
        url=f"https://example.com/{item_id}",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        media_type="article",
        title=title,
        content_text=content_text,
        raw_payload=raw_payload,
    )


@pytest.mark.asyncio
async def test_check_connector_counts_kept_and_dropped_items():
    connector = FakeConnector([
        _item("吉沢亮 latest interview", item_id="kept"),
        _item("Unrelated article", item_id="dropped"),
    ])

    result = await check_connector(connector, "吉沢亮", CollectionMode.ALL_INFO, sample_limit=3)

    assert result.platform == "fake"
    assert result.fetched == 2
    assert result.kept == 1
    assert result.dropped == 1
    assert result.samples == ["Unrelated article"]
    assert result.error is None


@pytest.mark.asyncio
async def test_check_connector_limits_dropped_samples():
    connector = FakeConnector([
        _item("Unrelated one", item_id="one"),
        _item("Unrelated two", item_id="two"),
    ])

    result = await check_connector(connector, "吉沢亮", CollectionMode.ALL_INFO, sample_limit=1)

    assert result.dropped == 2
    assert result.samples == ["Unrelated one"]


@pytest.mark.asyncio
async def test_check_connector_reports_connector_errors():
    connector = FakeConnector(error=RuntimeError("boom"))

    result = await check_connector(connector, "吉沢亮", CollectionMode.ALL_INFO, sample_limit=3)

    assert result.platform == "fake"
    assert result.fetched == 0
    assert result.kept == 0
    assert result.dropped == 0
    assert result.samples == []
    assert result.error == "boom"


@pytest.mark.asyncio
async def test_rich_check_connector_marks_keyword_mismatch():
    result = await _check_connector(
        FakeConnector([_item("unrelated result")]),
        ["Aiko"],
        CollectionMode.ALL_INFO,
        page_limit=0,
    )

    assert result.ok is False
    assert result.status == "keyword_mismatch"
    assert result.samples[0]["keyword_match"] is False


@pytest.mark.asyncio
async def test_rich_check_connector_accepts_keyword_matching_items():
    result = await _check_connector(
        FakeConnector([_item("Aiko result")]),
        ["Aiko"],
        CollectionMode.ALL_INFO,
        page_limit=0,
    )

    assert result.ok is True
    assert result.status == "ok"
    assert result.samples[0]["keyword_match"] is True


@pytest.mark.asyncio
async def test_rich_check_connector_reports_any_fetch_error_across_keywords():
    result = await _check_connector(
        ErrorThenItemsConnector(),
        ["Aiko", "IU"],
        CollectionMode.ALL_INFO,
        page_limit=0,
    )

    assert result.ok is False
    assert result.status == "fetch_failed"
    assert result.error == "RuntimeError: first keyword failed"


@pytest.mark.asyncio
async def test_rich_check_connector_uses_ingestion_relevance():
    result = await _check_connector(
        FakeConnector([
            _item(
                "hashtagged note without visible term",
                platform="note",
                raw_payload={"matched_hashtag": "Aiko"},
            )
        ]),
        ["Aiko"],
        CollectionMode.ALL_INFO,
        page_limit=0,
    )

    assert result.ok is True
    assert result.status == "ok"
    assert result.samples[0]["keyword_match"] is False
    assert result.samples[0]["primary_text_match"] is True
