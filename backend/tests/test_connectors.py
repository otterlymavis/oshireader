"""Unit tests for connector utilities that don't need HTTP calls."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.connectors.base import SourceItemCreate, parse_feed_date


def _entry(**kwargs) -> SimpleNamespace:
    """Build a minimal feedparser-like entry using attribute access (as parse_feed_date uses getattr)."""
    return SimpleNamespace(**kwargs)


class TestParseFeedDate:
    def test_returns_published_parsed_when_present(self):
        # feedparser returns time.struct_time-like 9-tuples; we slice [:6]
        entry = _entry(published_parsed=(2024, 6, 15, 10, 30, 0, 5, 167, 0))
        result = parse_feed_date(entry)
        assert result == datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_falls_back_to_updated_parsed(self):
        entry = _entry(
            published_parsed=None,
            updated_parsed=(2024, 3, 1, 8, 0, 0, 4, 61, 0),
        )
        result = parse_feed_date(entry)
        assert result == datetime(2024, 3, 1, 8, 0, 0, tzinfo=timezone.utc)

    def test_falls_back_to_now_when_both_missing(self):
        before = datetime.now(timezone.utc)
        entry = _entry(published_parsed=None, updated_parsed=None)
        result = parse_feed_date(entry)
        after = datetime.now(timezone.utc)
        assert before <= result <= after

    def test_falls_back_to_now_on_malformed_tuple(self):
        # A tuple that produces invalid args for datetime() should not crash
        entry = _entry(published_parsed=(99999, 99, 99, 99, 99, 99))  # month=99 is invalid
        before = datetime.now(timezone.utc)
        result = parse_feed_date(entry)
        after = datetime.now(timezone.utc)
        assert before <= result <= after

    def test_result_is_always_utc_aware(self):
        entry = _entry(published_parsed=(2024, 1, 1, 0, 0, 0, 0, 1, 0))
        result = parse_feed_date(entry)
        assert result.tzinfo == timezone.utc


class TestSourceItemCreateCompositeId:
    def test_composite_id_is_platform_colon_item_id(self):
        item = SourceItemCreate(
            platform="youtube",
            item_id="abc123",
            url="https://youtu.be/abc123",
            published_at=datetime.now(timezone.utc),
            media_type="video",
        )
        assert item.composite_id == "youtube:abc123"

    def test_composite_id_with_special_chars_in_item_id(self):
        item = SourceItemCreate(
            platform="news",
            item_id="https://example.com/article?id=42",
            url="https://example.com/article?id=42",
            published_at=datetime.now(timezone.utc),
            media_type="article",
        )
        assert item.composite_id == "news:https://example.com/article?id=42"
