"""Unit tests for connector utilities that don't need HTTP calls."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.connectors.base import SourceItemCreate, parse_feed_date
from app.connectors.fivech import FiveChConnector
from app.connectors.girlschannel import GirlsChannelConnector
from app.connectors.mdpr import ModelPressConnector
from app.connectors.mdpr import _clean_title as _clean_mdpr_title
from app.connectors.note import NoteConnector
from app.connectors.oricon import OriconConnector
from app.connectors.oricon import _clean_title as _clean_oricon_title
from app.connectors.rss import RSSConnector
from app.connectors.togetter import TogetterConnector
from app.connectors.tver import _parse_tver_date
from app.connectors.yahoonews import YahooNewsConnector
from app.connectors.yahoonews import _clean_markdown_title
from app.connectors.youtube import _parse_youtube_relative


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


class TestParseTverDate:
    def test_unix_timestamp_int(self):
        result = _parse_tver_date({"publishedAt": 1717200000})
        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result == datetime.fromtimestamp(1717200000, tz=timezone.utc)

    def test_iso_string_value(self):
        result = _parse_tver_date({"publishedAt": "2024-06-01T10:00:00Z"})
        assert result is not None
        assert result == datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)

    def test_second_key_used_as_fallback(self):
        # publishedAt is missing, deliveryStartAt has the value
        result = _parse_tver_date({"deliveryStartAt": 1717200000})
        assert result == datetime.fromtimestamp(1717200000, tz=timezone.utc)

    def test_broadcast_date_label_year_only(self):
        result = _parse_tver_date({"broadcastDateLabel": "2021年放送"})
        assert result == datetime(2021, 6, 1, tzinfo=timezone.utc)

    def test_broadcast_date_label_month_day(self):
        # Pin "now" so the test is deterministic regardless of run date
        fixed_now = datetime(2024, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
        with patch("app.connectors.tver.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromtimestamp = datetime.fromtimestamp
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _parse_tver_date({"broadcastDateLabel": "6月5日(金)放送分"})
        assert result is not None
        assert result.month == 6
        assert result.day == 5

    def test_broadcast_date_label_with_time(self):
        fixed_now = datetime(2024, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
        with patch("app.connectors.tver.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromtimestamp = datetime.fromtimestamp
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _parse_tver_date({"broadcastDateLabel": "5月29日(金) 18:29"})
        assert result is not None
        assert result.month == 5
        assert result.day == 29
        assert result.hour == 18
        assert result.minute == 29

    def test_returns_none_when_no_date_fields(self):
        result = _parse_tver_date({})
        assert result is None

    def test_result_is_utc_aware(self):
        result = _parse_tver_date({"publishedAt": 1700000000})
        assert result is not None
        assert result.tzinfo == timezone.utc


class TestParseYouTubeRelative:
    def _approx(self, result: datetime, expected: datetime, tolerance_sec: int = 5) -> bool:
        return abs((result - expected).total_seconds()) <= tolerance_sec

    def test_english_days_ago(self):
        before = datetime.now(timezone.utc)
        result = _parse_youtube_relative("2 days ago")
        assert result is not None
        assert self._approx(result, before - __import__("datetime").timedelta(days=2), tolerance_sec=60)

    def test_english_hours_ago(self):
        before = datetime.now(timezone.utc)
        result = _parse_youtube_relative("3 hours ago")
        assert result is not None
        # within 60s of 3h ago
        from datetime import timedelta
        assert self._approx(result, before - timedelta(hours=3), tolerance_sec=60)

    def test_japanese_days_ago(self):
        result = _parse_youtube_relative("5日前")
        assert result is not None

    def test_japanese_months_ago(self):
        result = _parse_youtube_relative("2ヶ月前")
        assert result is not None

    def test_japanese_weeks_ago(self):
        result = _parse_youtube_relative("1週間前")
        assert result is not None

    def test_returns_none_for_empty_string(self):
        assert _parse_youtube_relative("") is None

    def test_returns_none_for_unrecognized_text(self):
        assert _parse_youtube_relative("just now") is None

    def test_result_is_utc_aware(self):
        result = _parse_youtube_relative("1 day ago")
        assert result is not None
        assert result.tzinfo == timezone.utc


class TestCleanOriconTitle:
    def test_strips_oricon_news_suffix(self):
        assert _clean_oricon_title("アイコが受賞 - ORICON NEWS") == "アイコが受賞"

    def test_strips_japanese_variant(self):
        assert _clean_oricon_title("新曲リリース｜オリコンニュース") == "新曲リリース｜オリコンニュース"

    def test_strips_short_japanese_variant(self):
        assert _clean_oricon_title("新曲リリース - オリコン") == "新曲リリース"

    def test_plain_title_unchanged(self):
        assert _clean_oricon_title("アイコが新アルバムを発表") == "アイコが新アルバムを発表"


class TestCleanMdprTitle:
    def test_strips_modelpress_suffix(self):
        assert _clean_mdpr_title("アイコが新曲 - モデルプレス") == "アイコが新曲"

    def test_strips_pipe_variant(self):
        assert _clean_mdpr_title("アイコが新曲 | モデルプレス") == "アイコが新曲"

    def test_trailing_whitespace_in_suffix_ignored(self):
        assert _clean_mdpr_title("アイコ -  モデルプレス ") == "アイコ"

    def test_plain_title_unchanged(self):
        assert _clean_mdpr_title("アイコの最新情報") == "アイコの最新情報"

    def test_strips_surrounding_whitespace(self):
        assert _clean_mdpr_title("  Title  ") == "Title"


class TestCleanMarkdownTitle:
    def test_removes_image_markdown(self):
        result = _clean_markdown_title("Check ![img](https://example.com/img.png) this")
        assert result == "Check this"

    def test_removes_underscores(self):
        result = _clean_markdown_title("Hello_World")
        assert result == "HelloWorld"

    def test_collapses_whitespace(self):
        result = _clean_markdown_title("too   many   spaces")
        assert result == "too many spaces"

    def test_strips_leading_trailing(self):
        result = _clean_markdown_title("  trimmed  ")
        assert result == "trimmed"

    def test_plain_text_unchanged(self):
        result = _clean_markdown_title("アイコの新曲リリース")
        assert result == "アイコの新曲リリース"

    def test_combined_cleanup(self):
        result = _clean_markdown_title("  Hello_![x](u)  World  ")
        assert result == "Hello World"


class TestConnectorMediaOnlyEarlyReturn:
    """Text/article connectors must return [] immediately for media_only — no HTTP calls made."""

    @pytest.mark.asyncio
    async def test_rss_connector_returns_empty_for_media_only(self):
        result = await RSSConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_fivech_connector_returns_empty_for_media_only(self):
        result = await FiveChConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_girlschannel_connector_returns_empty_for_media_only(self):
        result = await GirlsChannelConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_modelpress_connector_returns_empty_for_media_only(self):
        result = await ModelPressConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_note_connector_returns_empty_for_media_only(self):
        result = await NoteConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_oricon_connector_returns_empty_for_media_only(self):
        result = await OriconConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_togetter_connector_returns_empty_for_media_only(self):
        result = await TogetterConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_yahoonews_connector_returns_empty_for_media_only(self):
        result = await YahooNewsConnector().fetch("Aiko", "media_only")
        assert result == []
