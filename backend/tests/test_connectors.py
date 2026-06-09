"""Unit tests for connector utilities that don't need HTTP calls."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx as _httpx_mod
import pytest

from app.connectors.base import SourceItemCreate, parse_feed_date
from app.connectors.fivech import FiveChConnector
from app.connectors.girlschannel import GirlsChannelConnector
from app.connectors.mdpr import ModelPressConnector
from app.connectors.mdpr import _clean_title as _clean_mdpr_title
from app.connectors.niconico import NicoNicoConnector
from app.connectors.note import NoteConnector
from app.connectors.oricon import OriconConnector
from app.connectors.oricon import _clean_title as _clean_oricon_title
from app.connectors.rss import RSSConnector
from app.connectors.togetter import TogetterConnector
from app.connectors.tver import _parse_tver_date
from app.connectors.twitter import TwitterConnector
from app.connectors.yahoonews import YahooNewsConnector
from app.connectors.yahoonews import _clean_markdown_title
from app.connectors.youtube import _parse_youtube_relative


def _entry(**kwargs) -> SimpleNamespace:
    """Build a minimal feedparser-like entry using attribute access (as parse_feed_date uses getattr)."""
    return SimpleNamespace(**kwargs)


class _FeedEntry(dict):
    """feedparser entry stub — supports both dict .get() and attribute access."""

    def __getattr__(self, key):
        return self.get(key)


class _FakeFeed:
    def __init__(self, entries):
        self.entries = entries


def _rss_entry(link="https://example.com/1", title="Title", summary="", item_id=None):
    return _FeedEntry(id=item_id or link, link=link, title=title, summary=summary)


def _http_mock(status_code=200, content=b"", text="", is_success=True):
    """Returns a mock httpx.AsyncClient class (context manager) yielding a fake response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.text = text
    resp.is_success = is_success
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


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

    def test_future_date_rolls_back_to_prior_year(self):
        # If parsed month/day is >7 days in the future relative to "now", roll back to last year.
        # Pin now to Jan 5 so Dec 25 is clearly in the future → should become Dec 25 of prior year.
        fixed_now = datetime(2024, 1, 5, 12, 0, 0, tzinfo=timezone.utc)
        with patch("app.connectors.tver.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromtimestamp = datetime.fromtimestamp
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _parse_tver_date({"broadcastDateLabel": "12月25日(水)放送分"})
        assert result is not None
        assert result.year == 2023, "Dec 25 is >7 days ahead of Jan 5 — must roll back one year"
        assert result.month == 12
        assert result.day == 25


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


class TestTwitterConnectorNoToken:
    """TwitterConnector must return [] immediately when no bearer token is configured."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_bearer_token_is_empty_string(self):
        result = await TwitterConnector(bearer_token="").fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_for_media_only_with_no_token(self):
        result = await TwitterConnector(bearer_token="").fetch("Aiko", "media_only")
        assert result == []


# ---------------------------------------------------------------------------
# HTTP-level connector tests (mock httpx.AsyncClient + feedparser.parse)
# ---------------------------------------------------------------------------

class TestFiveChFetch:
    @pytest.mark.asyncio
    async def test_returns_items_on_success(self):
        valid = _rss_entry(link="https://5ch.net/t1", title="Aiko thread")
        no_link = _FeedEntry(id="nl", link="", title="skip me")
        no_title = _rss_entry(link="https://5ch.net/t3", title="")
        fake_feed = _FakeFeed([valid, no_link, no_title])
        with patch("app.connectors.fivech.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.fivech.feedparser.parse", return_value=fake_feed):
            result = await FiveChConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].platform == "5ch"
        assert result[0].url == "https://5ch.net/t1"

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        with patch("app.connectors.fivech.httpx.AsyncClient",
                   _http_mock(status_code=503, is_success=False)):
            result = await FiveChConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_network_exception(self):
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_httpx_mod.ConnectError("timeout"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.fivech.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await FiveChConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicates_by_item_id(self):
        e1 = _rss_entry(link="https://5ch.net/t1", item_id="dup_id", title="A")
        e2 = _rss_entry(link="https://5ch.net/t2", item_id="dup_id", title="B")
        fake_feed = _FakeFeed([e1, e2])
        with patch("app.connectors.fivech.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.fivech.feedparser.parse", return_value=fake_feed):
            result = await FiveChConnector().fetch("Aiko", "all_info")
        assert len(result) == 1


class TestGirlsChannelFetch:
    @pytest.mark.asyncio
    async def test_returns_items_on_success(self):
        valid = _rss_entry(link="https://girlschannel.net/t1", title="アイコ")
        no_link = _FeedEntry(id="nl", link="", title="skip")
        no_title = _rss_entry(link="https://girlschannel.net/t2", title="")
        fake_feed = _FakeFeed([valid, no_link, no_title])
        with patch("app.connectors.girlschannel.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.girlschannel.feedparser.parse", return_value=fake_feed):
            result = await GirlsChannelConnector().fetch("アイコ", "all_info")
        assert len(result) == 1
        assert result[0].platform == "girlschannel"

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        with patch("app.connectors.girlschannel.httpx.AsyncClient",
                   _http_mock(status_code=500, is_success=False)):
            result = await GirlsChannelConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_httpx_mod.ConnectError("x"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.girlschannel.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await GirlsChannelConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicates_by_item_id(self):
        e1 = _rss_entry(link="https://girlschannel.net/t1", item_id="dup", title="A")
        e2 = _rss_entry(link="https://girlschannel.net/t2", item_id="dup", title="B")
        fake_feed = _FakeFeed([e1, e2])
        with patch("app.connectors.girlschannel.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.girlschannel.feedparser.parse", return_value=fake_feed):
            result = await GirlsChannelConnector().fetch("アイコ", "all_info")
        assert len(result) == 1


class TestMdprFetch:
    @pytest.mark.asyncio
    async def test_returns_items_with_title_cleaned(self):
        valid = _rss_entry(link="https://mdpr.jp/a1", title="Aiko - モデルプレス")
        no_link = _FeedEntry(id="nl", link="", title="skip")
        no_title = _rss_entry(link="https://mdpr.jp/a2", title="   ")
        fake_feed = _FakeFeed([valid, no_link, no_title])
        with patch("app.connectors.mdpr.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.mdpr.feedparser.parse", return_value=fake_feed):
            result = await ModelPressConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].title == "Aiko"

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        with patch("app.connectors.mdpr.httpx.AsyncClient",
                   _http_mock(status_code=403, is_success=False)):
            result = await ModelPressConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_httpx_mod.ConnectError("x"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.mdpr.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await ModelPressConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicates_by_item_id(self):
        e1 = _rss_entry(link="https://mdpr.jp/a1", item_id="dup", title="A")
        e2 = _rss_entry(link="https://mdpr.jp/a2", item_id="dup", title="B")
        fake_feed = _FakeFeed([e1, e2])
        with patch("app.connectors.mdpr.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.mdpr.feedparser.parse", return_value=fake_feed):
            result = await ModelPressConnector().fetch("Aiko", "all_info")
        assert len(result) == 1


class TestOriconFetch:
    @pytest.mark.asyncio
    async def test_returns_items_with_title_cleaned_and_author_set(self):
        valid = _rss_entry(link="https://oricon.co.jp/a1", title="受賞 - ORICON NEWS")
        no_link = _FeedEntry(id="nl", link="", title="skip")
        empty_title = _rss_entry(link="https://oricon.co.jp/a2", title="  - ORICON NEWS")
        fake_feed = _FakeFeed([valid, no_link, empty_title])
        with patch("app.connectors.oricon.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.oricon.feedparser.parse", return_value=fake_feed):
            result = await OriconConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].title == "受賞"
        assert result[0].author == "ORICON NEWS"

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        with patch("app.connectors.oricon.httpx.AsyncClient",
                   _http_mock(status_code=500, is_success=False)):
            result = await OriconConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_httpx_mod.ConnectError("x"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.oricon.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await OriconConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicates_by_item_id(self):
        e1 = _rss_entry(link="https://oricon.co.jp/a1", item_id="dup", title="A")
        e2 = _rss_entry(link="https://oricon.co.jp/a2", item_id="dup", title="B")
        fake_feed = _FakeFeed([e1, e2])
        with patch("app.connectors.oricon.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.oricon.feedparser.parse", return_value=fake_feed):
            result = await OriconConnector().fetch("Aiko", "all_info")
        assert len(result) == 1


class TestRSSConnectorFetch:
    @pytest.mark.asyncio
    async def test_returns_keyword_matching_items(self):
        matching = _rss_entry(link="https://natalie.mu/1", title="Aiko 最新情報")
        non_matching = _rss_entry(link="https://natalie.mu/2", title="他のニュース")
        fake_feed = _FakeFeed([matching, non_matching])
        with patch("app.connectors.rss.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.rss.feedparser.parse", return_value=fake_feed):
            result = await RSSConnector().fetch("aiko", "all_info")
        urls = [r.url for r in result]
        assert all("natalie.mu/1" in u for u in urls)
        assert not any("natalie.mu/2" in u for u in urls)

    @pytest.mark.asyncio
    async def test_skips_entry_without_link(self):
        no_link = _FeedEntry(id="x", link="", title="Aiko news", summary="aiko content")
        fake_feed = _FakeFeed([no_link])
        with patch("app.connectors.rss.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.rss.feedparser.parse", return_value=fake_feed):
            result = await RSSConnector().fetch("aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_all_feeds_return_empty_on_http_error(self):
        with patch("app.connectors.rss.httpx.AsyncClient",
                   _http_mock(status_code=500, is_success=False)):
            result = await RSSConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_extracts_image_enclosure_as_thumbnail(self):
        entry = _FeedEntry(
            id="id1", link="https://natalie.mu/1",
            title="Aiko event", summary="aiko concert info",
            enclosures=[{"type": "image/jpeg", "href": "https://example.com/t.jpg"}],
        )
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.rss.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.rss.feedparser.parse", return_value=fake_feed):
            result = await RSSConnector().fetch("aiko", "all_info")
        assert any(r.thumbnail_url == "https://example.com/t.jpg" for r in result)

    @pytest.mark.asyncio
    async def test_feedparser_exception_is_caught(self):
        with patch("app.connectors.rss.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.rss.feedparser.parse", side_effect=ValueError("bad feed")):
            result = await RSSConnector().fetch("aiko", "all_info")
        assert result == []


_TOGETTER_HTML = """
<html><body><ul>
  <li>
    <h3>アイコの人気まとめ</h3>
    <a href="https://togetter.com/li/1234567">アイコのまとめ</a>
    <time datetime="2024-01-15T12:00:00+00:00">Jan 15</time>
    <img src="https://i.togetter.com/t.jpg" />
  </li>
</ul></body></html>
"""


class TestTogetterFetch:
    @pytest.mark.asyncio
    async def test_returns_items_from_html(self):
        with patch("app.connectors.togetter.httpx.AsyncClient",
                   _http_mock(text=_TOGETTER_HTML, is_success=True)):
            result = await TogetterConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].platform == "togetter"
        assert result[0].item_id == "1234567"
        assert result[0].title == "アイコの人気まとめ"
        assert result[0].thumbnail_url == "https://i.togetter.com/t.jpg"

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        with patch("app.connectors.togetter.httpx.AsyncClient",
                   _http_mock(status_code=503, is_success=False)):
            result = await TogetterConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_httpx_mod.ConnectError("x"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.togetter.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await TogetterConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicates_same_togetter_id(self):
        html = """
        <html><body><ul>
          <li><h3>Title A</h3>
            <a href="https://togetter.com/li/99999">TA</a>
          </li>
          <li><h3>Title B</h3>
            <a href="https://togetter.com/li/99999">TB</a>
          </li>
        </ul></body></html>
        """
        with patch("app.connectors.togetter.httpx.AsyncClient",
                   _http_mock(text=html, is_success=True)):
            result = await TogetterConnector().fetch("Aiko", "all_info")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_skips_entry_with_no_title(self):
        # No h3 and empty anchor text → entry is skipped
        html = """
        <html><body><ul>
          <li>
            <a href="https://togetter.com/li/11111"></a>
          </li>
        </ul></body></html>
        """
        with patch("app.connectors.togetter.httpx.AsyncClient",
                   _http_mock(text=html, is_success=True)):
            result = await TogetterConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_caps_at_25_items(self):
        entries = "\n".join(
            f'<li><h3>Title {i}</h3><a href="https://togetter.com/li/{i:05d}">T{i}</a></li>'
            for i in range(1, 30)
        )
        html = f"<html><body><ul>{entries}</ul></body></html>"
        with patch("app.connectors.togetter.httpx.AsyncClient",
                   _http_mock(text=html, is_success=True)):
            result = await TogetterConnector().fetch("Aiko", "all_info")
        assert len(result) == 25

    @pytest.mark.asyncio
    async def test_bad_datetime_string_does_not_crash(self):
        # Invalid datetime attribute → fromisoformat raises → falls back to datetime.now()
        html = """
        <html><body><ul>
          <li>
            <h3>タイトル</h3>
            <a href="https://togetter.com/li/22222">T</a>
            <time datetime="not-a-valid-date">text</time>
          </li>
        </ul></body></html>
        """
        with patch("app.connectors.togetter.httpx.AsyncClient",
                   _http_mock(text=html, is_success=True)):
            result = await TogetterConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].item_id == "22222"


_JINA_MARKDOWN = """
# Yahoo News Search Results

1. [アイコの新曲情報](https://news.yahoo.co.jp/articles/abc123)
2. [別のニュース](https://news.yahoo.co.jp/articles/xyz789)
3. [重複エントリ](https://news.yahoo.co.jp/articles/abc123)
"""


class TestYahooNewsFetch:
    @pytest.mark.asyncio
    async def test_empty_keyword_returns_empty(self):
        result = await YahooNewsConnector().fetch("", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_whitespace_keyword_returns_empty(self):
        result = await YahooNewsConnector().fetch("   ", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_gnews_rss_returns_items(self):
        entry = _FeedEntry(
            id="https://news.yahoo.co.jp/articles/abc123",
            link="https://news.yahoo.co.jp/articles/abc123",
            title="アイコの最新情報",
            summary="",
        )
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.yahoonews.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.yahoonews.feedparser.parse", return_value=fake_feed):
            result = await YahooNewsConnector().fetch("アイコ", "all_info")
        assert len(result) == 1
        assert result[0].platform == "yahoonews"

    @pytest.mark.asyncio
    async def test_falls_back_to_jina_when_gnews_empty(self):
        # _JINA_MARKDOWN has 3 entries but one is a dup (abc123 appears twice) → 2 unique items
        empty_feed = _FakeFeed([])
        with patch("app.connectors.yahoonews.httpx.AsyncClient",
                   _http_mock(content=b"<rss/>", text=_JINA_MARKDOWN)), \
             patch("app.connectors.yahoonews.feedparser.parse", return_value=empty_feed):
            result = await YahooNewsConnector().fetch("アイコ", "all_info")
        assert len(result) == 2
        assert all(r.platform == "yahoonews" for r in result)

    @pytest.mark.asyncio
    async def test_jina_non_success_returns_empty(self):
        with patch("app.connectors.yahoonews.httpx.AsyncClient",
                   _http_mock(status_code=500, is_success=False)):
            result = await YahooNewsConnector().fetch("アイコ", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_jina_exception_returns_empty(self):
        empty_feed = _FakeFeed([])
        call_count = [0]

        async def _get_side_effect(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                resp = MagicMock()
                resp.is_success = True
                resp.content = b""
                resp.text = ""
                return resp
            raise _httpx_mod.ConnectError("jina down")

        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_get_side_effect)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.yahoonews.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.yahoonews.feedparser.parse", return_value=empty_feed):
            result = await YahooNewsConnector().fetch("アイコ", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_gnews_feedparser_exception_falls_back_to_jina(self):
        # feedparser.parse raises → _fetch_gnews_rss except branch → falls back to jina
        with patch("app.connectors.yahoonews.httpx.AsyncClient",
                   _http_mock(content=b"<rss/>", text=_JINA_MARKDOWN)), \
             patch("app.connectors.yahoonews.feedparser.parse",
                   side_effect=ValueError("bad gnews feed")):
            result = await YahooNewsConnector().fetch("アイコ", "all_info")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_jina_caps_at_25_items(self):
        entries = "\n".join(
            f"{i+1}. [Title{i}](https://news.yahoo.co.jp/articles/art{i:03d})"
            for i in range(30)
        )
        empty_feed = _FakeFeed([])
        with patch("app.connectors.yahoonews.httpx.AsyncClient",
                   _http_mock(content=b"<rss/>", text=entries)), \
             patch("app.connectors.yahoonews.feedparser.parse", return_value=empty_feed):
            result = await YahooNewsConnector().fetch("アイコ", "all_info")
        assert len(result) == 25

    @pytest.mark.asyncio
    async def test_gnews_rss_skips_bad_entries(self):
        # Entries with no link, duplicate id, and empty title are skipped
        valid = _FeedEntry(
            id="https://news.yahoo.co.jp/articles/abc1",
            link="https://news.yahoo.co.jp/articles/abc1",
            title="アイコ最新情報",
        )
        no_link = _FeedEntry(id="nl", link="", title="T")
        dup1 = _FeedEntry(
            id="https://news.yahoo.co.jp/articles/dup",
            link="https://news.yahoo.co.jp/articles/dup",
            title="Dup A",
        )
        dup2 = _FeedEntry(
            id="https://news.yahoo.co.jp/articles/dup",
            link="https://news.yahoo.co.jp/articles/dup",
            title="Dup B",
        )
        empty_title = _FeedEntry(
            id="https://news.yahoo.co.jp/articles/et",
            link="https://news.yahoo.co.jp/articles/et",
            title="",
        )
        fake_feed = _FakeFeed([valid, no_link, dup1, dup2, empty_title])
        with patch("app.connectors.yahoonews.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.yahoonews.feedparser.parse", return_value=fake_feed):
            result = await YahooNewsConnector().fetch("アイコ", "all_info")
        # valid + dup1 survive; no_link, dup2, empty_title are skipped
        assert len(result) == 2


# ---------------------------------------------------------------------------
# NicoNico connector tests
# ---------------------------------------------------------------------------

def _nico_ctx(side_effect=None, api_data=None, gnews_content=b"<rss/>"):
    """Create a shared httpx context mock; supports optional call-count-based side effects."""
    if side_effect is not None:
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=side_effect)
    else:
        resp = MagicMock()
        resp.is_success = True
        resp.json.return_value = api_data or {}
        resp.content = gnews_content
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


class TestNicoNicoFetch:
    @pytest.mark.asyncio
    async def test_api_returns_items_on_success(self):
        api_data = {
            "data": [{
                "contentId": "sm12345",
                "title": "Aiko cover",
                "startTime": "2024-01-15T10:00:00+00:00",
                "userId": 123456,
                "thumbnailUrl": "https://cdn.nicovideo.jp/t.jpg",
                "description": "Nice cover",
            }]
        }
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(api_data=api_data)):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].platform == "niconico"
        assert result[0].item_id == "sm12345"
        assert result[0].thumbnail_url == "https://cdn.nicovideo.jp/t.jpg"
        assert result[0].author == "123456"

    @pytest.mark.asyncio
    async def test_api_uses_channel_id_as_author_when_no_user_id(self):
        api_data = {"data": [{"contentId": "sm1", "title": "T", "channelId": "ch999"}]}
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(api_data=api_data)):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert result[0].author == "ch999"

    @pytest.mark.asyncio
    async def test_api_bad_start_time_does_not_crash(self):
        api_data = {"data": [{"contentId": "sm2", "title": "T", "startTime": "not-a-date"}]}
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(api_data=api_data)):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].published_at is not None

    @pytest.mark.asyncio
    async def test_api_skips_entry_without_content_id(self):
        api_data = {"data": [{"title": "No ID"}]}
        # API returns empty list → gnews fallback → gnews also empty
        call_count = [0]
        api_resp = MagicMock()
        api_resp.is_success = True
        api_resp.json.return_value = api_data
        gnews_resp = MagicMock()
        gnews_resp.is_success = False
        gnews_resp.status_code = 404

        async def _side(url, **kw):
            call_count[0] += 1
            return api_resp if call_count[0] == 1 else gnews_resp

        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(side_effect=_side)):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_api_http_error_falls_back_to_gnews(self):
        call_count = [0]
        api_resp = MagicMock()
        api_resp.is_success = False
        api_resp.status_code = 403
        gnews_resp = MagicMock()
        gnews_resp.is_success = True
        gnews_resp.content = b"<rss/>"

        async def _side(url, **kw):
            call_count[0] += 1
            return api_resp if call_count[0] == 1 else gnews_resp

        valid = _rss_entry(link="https://www.nicovideo.jp/watch/sm99", title="Aiko")
        no_link = _FeedEntry(id="nl", link="", title="skip")
        dup1 = _rss_entry(link="https://www.nicovideo.jp/watch/sm1", item_id="dup", title="D1")
        dup2 = _rss_entry(link="https://www.nicovideo.jp/watch/sm2", item_id="dup", title="D2")
        no_title = _rss_entry(link="https://www.nicovideo.jp/watch/sm3", title="")
        fake_feed = _FakeFeed([valid, no_link, dup1, dup2, no_title])
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.niconico.feedparser.parse", return_value=fake_feed):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert len(result) == 2  # valid + dup1; dup2, no_link, no_title skipped
        assert result[0].media_type == "video"

    @pytest.mark.asyncio
    async def test_api_json_exception_falls_back_to_gnews(self):
        # resp.json() raises → _fetch_api except → gnews fallback
        call_count = [0]
        api_resp = MagicMock()
        api_resp.is_success = True
        api_resp.json.side_effect = ValueError("bad json")
        gnews_resp = MagicMock()
        gnews_resp.is_success = True
        gnews_resp.content = b"<rss/>"

        async def _side(url, **kw):
            call_count[0] += 1
            return api_resp if call_count[0] == 1 else gnews_resp

        gnews_entry = _rss_entry(link="https://www.nicovideo.jp/watch/sm88", title="Aiko")
        fake_feed = _FakeFeed([gnews_entry])
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.niconico.feedparser.parse", return_value=fake_feed):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_gnews_http_error_returns_empty(self):
        call_count = [0]
        api_resp = MagicMock()
        api_resp.is_success = True
        api_resp.json.return_value = {"data": []}
        gnews_resp = MagicMock()
        gnews_resp.is_success = False
        gnews_resp.status_code = 503

        async def _side(url, **kw):
            call_count[0] += 1
            return api_resp if call_count[0] == 1 else gnews_resp

        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(side_effect=_side)):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_gnews_feedparser_exception_returns_empty(self):
        call_count = [0]
        api_resp = MagicMock()
        api_resp.is_success = True
        api_resp.json.return_value = {"data": []}
        gnews_resp = MagicMock()
        gnews_resp.is_success = True
        gnews_resp.content = b"<rss/>"

        async def _side(url, **kw):
            call_count[0] += 1
            return api_resp if call_count[0] == 1 else gnews_resp

        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.niconico.feedparser.parse", side_effect=ValueError("x")):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert result == []


# ---------------------------------------------------------------------------
# Note connector tests
# ---------------------------------------------------------------------------

def _note_api_resp(notes, is_success=True, structure="contents"):
    """Build a mock httpx response for note.com search API."""
    if structure == "contents":
        data = {"data": {"notes": {"contents": notes}}}
    elif structure == "flat":
        data = {"data": {"notes": notes}}
    else:
        data = structure
    resp = MagicMock()
    resp.is_success = is_success
    resp.status_code = 200 if is_success else 500
    resp.json.return_value = data
    resp.content = b""
    return resp


class TestNoteFetch:
    @pytest.mark.asyncio
    async def test_empty_keyword_returns_empty(self):
        result = await NoteConnector().fetch("   ", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_api_returns_items(self):
        notes = [{
            "key": "abc123",
            "name": "Aiko の新曲レビュー",
            "user": {"urlname": "reviewer", "name": "Reviewer San"},
            "publishAt": "2024-01-15T10:00:00+00:00",
            "body": "素晴らしい曲です",
            "eyecatch": "https://assets.st-note.com/img/t.jpg",
        }]
        resp = _note_api_resp(notes)
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].platform == "note"
        assert result[0].item_id == "abc123"
        assert result[0].author == "Reviewer San"
        assert result[0].thumbnail_url == "https://assets.st-note.com/img/t.jpg"

    @pytest.mark.asyncio
    async def test_search_api_non_list_notes_returns_empty(self):
        # notes dict without "contents" key → isinstance(notes, list) fails → return []
        # then rss fallback also fails → final result []
        resp = _note_api_resp(None, structure={"data": {"notes": {"unexpected_shape": "data"}}})
        call_count = [0]
        rss_resp = MagicMock()
        rss_resp.is_success = False
        rss_resp.status_code = 404

        async def _side(url, **kw):
            call_count[0] += 1
            return resp if call_count[0] == 1 else rss_resp

        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_side)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_api_http_error_falls_back_to_rss(self):
        call_count = [0]
        api_resp = MagicMock()
        api_resp.is_success = False
        api_resp.status_code = 500
        rss_resp = MagicMock()
        rss_resp.is_success = True
        rss_resp.content = b"<rss/>"

        async def _side(url, **kw):
            call_count[0] += 1
            return api_resp if call_count[0] == 1 else rss_resp

        rss_entry = _rss_entry(link="https://note.com/u/n/abc1", title="Aiko article")
        fake_feed = _FakeFeed([rss_entry])
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_side)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.note.feedparser.parse", return_value=fake_feed):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].platform == "note"

    @pytest.mark.asyncio
    async def test_search_api_json_exception_falls_back_to_rss(self):
        call_count = [0]
        api_resp = MagicMock()
        api_resp.is_success = True
        api_resp.json.side_effect = ValueError("bad json")
        rss_resp = MagicMock()
        rss_resp.is_success = True
        rss_resp.content = b"<rss/>"

        async def _side(url, **kw):
            call_count[0] += 1
            return api_resp if call_count[0] == 1 else rss_resp

        rss_entry = _rss_entry(link="https://note.com/u/n/abc2", title="Article")
        fake_feed = _FakeFeed([rss_entry])
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_side)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.note.feedparser.parse", return_value=fake_feed):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_search_api_skips_note_without_key(self):
        notes = [{"name": "No Key"}, {"key": "k2", "name": "Has Key"}]
        resp = _note_api_resp(notes)
        call_count = [0]
        rss_resp = MagicMock()
        rss_resp.is_success = False
        rss_resp.status_code = 404

        async def _side(url, **kw):
            call_count[0] += 1
            return resp if call_count[0] == 1 else rss_resp

        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_side)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].item_id == "k2"

    @pytest.mark.asyncio
    async def test_search_api_url_without_urlname(self):
        # No urlname → URL uses /n/<key> format
        notes = [{"key": "k3", "name": "Title", "user": {}}]
        resp = _note_api_resp(notes)
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert result[0].url == "https://note.com/n/k3"

    @pytest.mark.asyncio
    async def test_search_api_bad_publish_at_uses_now(self):
        notes = [{"key": "k4", "name": "Title", "publishAt": "not-a-date"}]
        resp = _note_api_resp(notes)
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].published_at is not None

    @pytest.mark.asyncio
    async def test_rss_feedparser_exception_returns_empty(self):
        # feedparser.parse raises inside _fetch_hashtag_rss → except branch
        call_count = [0]
        api_resp = MagicMock()
        api_resp.is_success = False
        api_resp.status_code = 404
        rss_resp = MagicMock()
        rss_resp.is_success = True
        rss_resp.content = b"<rss/>"

        async def _side(url, **kw):
            call_count[0] += 1
            return api_resp if call_count[0] == 1 else rss_resp

        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_side)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.note.feedparser.parse", side_effect=ValueError("bad")):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_rss_skips_entry_without_link_and_extracts_enclosure_thumb(self):
        # Covers: no-link skip, image enclosure thumbnail extraction
        no_link = _FeedEntry(id="nl", link="", title="skip")
        with_enclosure = _FeedEntry(
            id="enc1", link="https://note.com/u/n/enc1",
            title="Article with thumb",
            enclosures=[{"type": "image/jpeg", "href": "https://assets.st-note.com/enc/t.jpg"}],
        )
        fake_feed = _FakeFeed([no_link, with_enclosure])
        call_count = [0]
        api_resp = MagicMock()
        api_resp.is_success = False
        api_resp.status_code = 404
        rss_resp = MagicMock()
        rss_resp.is_success = True
        rss_resp.content = b"<rss/>"

        async def _side(url, **kw):
            call_count[0] += 1
            return api_resp if call_count[0] == 1 else rss_resp

        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_side)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.note.feedparser.parse", return_value=fake_feed):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].thumbnail_url == "https://assets.st-note.com/enc/t.jpg"

    @pytest.mark.asyncio
    async def test_rss_extracts_thumbnail_from_media_thumbnail(self):
        entry = _FeedEntry(
            id="nid1", link="https://note.com/u/n/nid1",
            title="Article",
            media_thumbnail=[{"url": "https://assets.st-note.com/media/t.jpg"}],
        )
        fake_feed = _FakeFeed([entry])
        # API fails → RSS called
        call_count = [0]
        api_resp = MagicMock()
        api_resp.is_success = False
        api_resp.status_code = 404
        rss_resp = MagicMock()
        rss_resp.is_success = True
        rss_resp.content = b"<rss/>"

        async def _side(url, **kw):
            call_count[0] += 1
            return api_resp if call_count[0] == 1 else rss_resp

        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=_side)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.note.feedparser.parse", return_value=fake_feed):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert result[0].thumbnail_url == "https://assets.st-note.com/media/t.jpg"


# ---------------------------------------------------------------------------
# Twitter connector tests
# ---------------------------------------------------------------------------

def _twitter_api_data(tweets=None, users=None, media=None):
    return {
        "data": tweets or [],
        "includes": {
            "users": users or [],
            "media": media or [],
        },
    }


class TestTwitterFetch:
    @pytest.mark.asyncio
    async def test_returns_items_on_success(self):
        data = _twitter_api_data(
            tweets=[{
                "id": "t1",
                "author_id": "u1",
                "text": "Aiko new song!",
                "created_at": "2024-01-15T10:00:00Z",
                "attachments": {},
            }],
            users=[{"id": "u1", "username": "aikoFan", "name": "Aiko Fan"}],
        )
        resp = MagicMock()
        resp.is_success = True
        resp.raise_for_status = MagicMock()
        resp.json.return_value = data
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.twitter.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await TwitterConnector(bearer_token="tok").fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].platform == "twitter"
        assert result[0].url == "https://x.com/aikoFan/status/t1"
        assert result[0].author == "@aikoFan"
        assert result[0].media_type == "text"
        assert result[0].content_text == "Aiko new song!"

    @pytest.mark.asyncio
    async def test_media_only_mode_sets_thumbnail_and_video_type(self):
        data = _twitter_api_data(
            tweets=[{
                "id": "t2",
                "author_id": "u2",
                "text": "Check out Aiko's MV!",
                "created_at": "2024-01-15T12:00:00Z",
                "attachments": {"media_keys": ["mk1"]},
            }],
            users=[{"id": "u2", "username": "mv_channel", "name": "MV Channel"}],
            media=[{"media_key": "mk1", "preview_image_url": "https://pbs.twimg.com/mv/t.jpg"}],
        )
        resp = MagicMock()
        resp.is_success = True
        resp.raise_for_status = MagicMock()
        resp.json.return_value = data
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.twitter.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await TwitterConnector(bearer_token="tok").fetch("Aiko", "media_only")
        assert result[0].media_type == "video"
        assert result[0].thumbnail_url == "https://pbs.twimg.com/mv/t.jpg"

    @pytest.mark.asyncio
    async def test_tweet_without_username_uses_fallback_url(self):
        data = _twitter_api_data(
            tweets=[{
                "id": "t3",
                "author_id": "u_unknown",
                "text": "Tweet",
                "created_at": "2024-01-15T14:00:00Z",
                "attachments": {},
            }],
        )
        resp = MagicMock()
        resp.is_success = True
        resp.raise_for_status = MagicMock()
        resp.json.return_value = data
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.twitter.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await TwitterConnector(bearer_token="tok").fetch("Aiko", "all_info")
        assert result[0].url == "https://x.com/i/status/t3"
        assert result[0].author is None
