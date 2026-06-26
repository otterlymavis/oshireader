"""Unit tests for connector utilities that don't need HTTP calls."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx as _httpx_mod
import json as _json_mod
import pytest

from app.connectors.base import (
    SourceItemCreate,
    contains_keyword,
    parse_feed_date,
    parse_google_news_markdown,
    title_contains_keyword,
)
from app.connectors.fivech import FiveChConnector
from app.connectors.girlschannel import GirlsChannelConnector
from app.connectors.mdpr import ModelPressConnector
from app.connectors.mdpr import _clean_title as _clean_mdpr_title
from app.connectors.news_sites import AmebloConnector, CinemaCafeConnector, LivedoorConnector, RealSoundConnector
from app.connectors.niconico import NicoNicoConnector
from app.connectors.note import NoteConnector
from app.connectors.oricon import OriconConnector
from app.connectors.oricon import _clean_title as _clean_oricon_title
from app.connectors.rss import RSSConnector
from app.connectors.togetter import TogetterConnector
from app.connectors.tver import TVERConnector, _parse_tver_date
from app.connectors.twitter import TwitterConnector
from app.connectors.yahoonews import YahooNewsConnector
from app.connectors.yahoonews import _clean_html_summary, _clean_markdown_title
from app.connectors.youtube import YouTubeConnector, _parse_youtube_relative


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
    return _FeedEntry(
        id=item_id or link,
        link=link,
        title=title,
        summary=summary,
        published_parsed=(2026, 6, 24, 10, 0, 0, 2, 175, 0),
    )


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
    def test_returns_published_parsed_when_only_published_present(self):
        # feedparser returns time.struct_time-like 9-tuples; we slice [:6]
        entry = _entry(published_parsed=(2024, 6, 15, 10, 30, 0, 5, 167, 0))
        result = parse_feed_date(entry)
        assert result == datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_prefers_updated_parsed_when_newer(self):
        entry = _entry(
            published_parsed=(2024, 6, 15, 10, 30, 0, 5, 167, 0),
            updated_parsed=(2024, 6, 16, 9, 0, 0, 6, 168, 0),
        )
        result = parse_feed_date(entry)
        assert result == datetime(2024, 6, 16, 9, 0, 0, tzinfo=timezone.utc)

    def test_keeps_published_parsed_when_updated_is_older(self):
        entry = _entry(
            published_parsed=(2024, 6, 16, 9, 0, 0, 6, 168, 0),
            updated_parsed=(2024, 6, 15, 10, 30, 0, 5, 167, 0),
        )
        result = parse_feed_date(entry)
        assert result == datetime(2024, 6, 16, 9, 0, 0, tzinfo=timezone.utc)

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

    def test_parses_google_news_jina_markdown(self):
        text = """### [Aiko fresh article - Example](https://news.google.com/rss/articles/abc123)

[Aiko fresh article](https://news.google.com/rss/articles/abc123)

Wed, 24 Jun 2026 02:07:03 GMT
"""
        result = parse_google_news_markdown(text)
        assert result == [{
            "title": "Aiko fresh article - Example",
            "url": "https://news.google.com/rss/articles/abc123",
            "published_at": datetime(2026, 6, 24, 2, 7, 3, tzinfo=timezone.utc),
        }]


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


class TestContainsKeyword:
    def test_matches_case_insensitively_across_visible_fields(self):
        assert contains_keyword("Aiko", None, "new AIKO story", "author")

    def test_ignores_empty_keyword(self):
        assert contains_keyword("   ", "anything") is False

    def test_returns_false_when_visible_fields_do_not_contain_keyword(self):
        assert contains_keyword("Aiko", "other idol", "unrelated summary") is False

    def test_normalizes_unicode_width_variants(self):
        assert contains_keyword("ABC123", "ＡＢＣ１２３ latest news")


class TestTitleContainsKeyword:
    def test_matches_title_only(self):
        assert title_contains_keyword("Aiko", "new AIKO story")

    def test_ignores_summary_like_extra_text(self):
        assert title_contains_keyword("Aiko", "unrelated story") is False


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

    def test_prefers_newest_update_timestamp(self):
        result = _parse_tver_date({
            "publishedAt": 1717200000,
            "updatedAt": 1717286400,
        })
        assert result == datetime.fromtimestamp(1717286400, tz=timezone.utc)

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

    def test_view_status_start_at_used_when_present(self):
        result = _parse_tver_date({"viewStatus": {"startAt": 1773907200, "endAt": 1782831599}})
        assert result == datetime.fromtimestamp(1773907200, tz=timezone.utc)

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

    def test_english_years_ago(self):
        from datetime import timedelta
        before = datetime.now(timezone.utc)
        result = _parse_youtube_relative("2 years ago")
        assert result is not None
        assert self._approx(result, before - timedelta(days=2 * 365), tolerance_sec=60)

    def test_japanese_years_ago(self):
        result = _parse_youtube_relative("1年前")
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
    async def test_realsound_connector_returns_empty_for_media_only(self):
        result = await RealSoundConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_cinemacafe_connector_returns_empty_for_media_only(self):
        result = await CinemaCafeConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_togetter_connector_returns_empty_for_media_only(self):
        result = await TogetterConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_yahoonews_connector_returns_empty_for_media_only(self):
        result = await YahooNewsConnector().fetch("Aiko", "media_only")
        assert result == []

    @pytest.mark.asyncio
    async def test_cinemacafe_connector_returns_empty_for_media_only(self):
        result = await CinemaCafeConnector().fetch("Aiko", "media_only")
        assert result == []


class TestTwitterConnectorNoToken:
    """Without API credentials, Twitter falls back to indexed public x.com posts."""

    @pytest.mark.asyncio
    async def test_returns_public_index_items_when_bearer_token_is_empty_string(self):
        entry = _rss_entry(link="https://x.com/aiko/status/1", title="Aiko concert update - x.com")
        with patch("app.connectors.twitter.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.twitter.feedparser.parse", return_value=_FakeFeed([entry])):
            result = await TwitterConnector(bearer_token="").fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].platform == "twitter"
        assert result[0].url == "https://x.com/aiko/status/1"

    @pytest.mark.asyncio
    async def test_returns_empty_for_media_only_with_no_token(self):
        result = await TwitterConnector(bearer_token="").fetch("Aiko", "media_only")
        assert result == []


# ---------------------------------------------------------------------------
# HTTP-level connector tests (mock httpx.AsyncClient + feedparser.parse)
# ---------------------------------------------------------------------------

class TestFiveChFetch:
    @pytest.mark.asyncio
    async def test_prefers_real_itest_threads_over_2ch_mirror(self):
        markdown = """
*   [2026年6月25日 17時59分 話題度:43 13レス 【乃木坂46】池田瑛紗応援スレ★104【てれぱん】](https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369)
"""
        dat = "name<>sage<>2026/06/25(木) 17:59:29.00 ID:last<> latest <>\n"

        async def _side(url, **_kw):
            if str(url).startswith("https://r.jina.ai/http://https://itest.5ch.io/subback/nogizaka"):
                return MagicMock(is_success=True, status_code=200, text=markdown)
            if str(url).startswith("https://r.jina.ai/http://http://mevius.5ch.net/nogizaka/dat/1782410369.dat"):
                return MagicMock(is_success=True, status_code=200, text=dat, content=dat.encode())
            return MagicMock(is_success=True, status_code=200, text="", content=b"")

        with patch("app.connectors.fivech.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.fivech.feedparser.parse") as parse:
            result = await FiveChConnector().fetch("乃木坂46", "all_info")

        parse.assert_not_called()
        assert len(result) == 1
        assert result[0].item_id == "5ch:mevius:nogizaka:1782410369"
        assert result[0].url == "https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369"
        assert result[0].published_at == datetime(2026, 6, 25, 8, 59, 29, tzinfo=timezone.utc)
        assert result[0].raw_payload["source"] == "5ch_itest"
        assert result[0].raw_payload["date_source"] == "dat_latest_post"

    @pytest.mark.asyncio
    async def test_real_itest_retries_jina_subback_throttling(self):
        markdown = """
*   [2026年6月25日 17時59分 話題度:43 13レス 【乃木坂46】池田瑛紗応援スレ★104【てれぱん】](https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369)
"""
        dat = "name<>sage<>2026/06/25(木) 17:59:29.00 ID:last<> latest <>\n"
        attempts = 0

        async def _side(url, **_kw):
            nonlocal attempts
            url_text = str(url)
            if url_text.startswith("https://r.jina.ai/http://https://itest.5ch.io/subback/nogizaka"):
                attempts += 1
                if attempts == 1:
                    return MagicMock(is_success=False, status_code=429, text="", content=b"")
                return MagicMock(is_success=True, status_code=200, text=markdown, content=markdown.encode())
            if url_text.startswith("https://r.jina.ai/http://http://mevius.5ch.net/nogizaka/dat/1782410369.dat"):
                return MagicMock(is_success=True, status_code=200, text=dat, content=dat.encode())
            return MagicMock(is_success=True, status_code=200, text="", content=b"")

        with patch("app.connectors.fivech.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.fivech.asyncio.sleep", new=AsyncMock()) as sleep, \
             patch("app.connectors.fivech.feedparser.parse") as parse:
            result = await FiveChConnector().fetch("乃木坂46", "all_info")

        parse.assert_not_called()
        sleep.assert_awaited_once()
        assert attempts == 2
        assert len(result) == 1
        assert result[0].raw_payload["source"] == "5ch_itest"

    @pytest.mark.asyncio
    async def test_real_itest_drops_threads_without_latest_dat_date(self):
        markdown = """
*   [2026年6月25日 17時59分 話題度:43 13レス 【乃木坂46】池田瑛紗応援スレ★104【てれぱん】](https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369)
"""

        async def _side(url, **_kw):
            url_text = str(url)
            if url_text.startswith("https://r.jina.ai/http://https://itest.5ch.io/subback/nogizaka"):
                return MagicMock(is_success=True, status_code=200, text=markdown, content=markdown.encode())
            if url_text.startswith("https://r.jina.ai/http://http://mevius.5ch.net/nogizaka/dat/1782410369.dat"):
                return MagicMock(is_success=True, status_code=200, text="", content=b"")
            return MagicMock(is_success=True, status_code=200, text="", content=b"")

        with patch("app.connectors.fivech.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.fivech.asyncio.sleep", new=AsyncMock()), \
             patch("app.connectors.fivech.feedparser.parse") as parse:
            result = await FiveChConnector().fetch("乃木坂46", "all_info")

        parse.assert_called()
        assert result == []

    @pytest.mark.asyncio
    async def test_real_itest_uses_worker_proxy_when_jina_is_blocked(self):
        from urllib.parse import parse_qs, urlparse

        markdown = """
*   [2026年6月25日 17時59分 話題度:43 13レス 【乃木坂46】池田瑛紗応援スレ★104【てれぱん】](https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369)
"""
        dat = "name<>sage<>2026/06/25(木) 17:59:29.00 ID:last<> latest <>\n"

        async def _side(url, **_kw):
            url_text = str(url)
            if url_text.startswith("https://r.jina.ai/http://https://itest.5ch.io/subback/"):
                return MagicMock(is_success=False, status_code=401, text="", content=b"")
            if url_text.startswith("https://worker.example/fivech-proxy"):
                query = parse_qs(urlparse(url_text).query)
                resource = query.get("resource", [""])[0]
                if resource == "itest_dat":
                    return MagicMock(is_success=True, status_code=200, text=dat, content=dat.encode())
                return MagicMock(is_success=True, status_code=200, text=markdown, content=markdown.encode())
            return MagicMock(is_success=True, status_code=200, text="", content=b"")

        with patch("app.connectors.fivech.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.fivech.settings.admin_api_token", "secret"), \
             patch("app.connectors.fivech.settings.source_5ch_proxy_url", "https://worker.example/fivech-proxy"), \
             patch("app.connectors.fivech._DEFAULT_5CH_PROXY_URL", "https://worker.example/fivech-proxy"), \
             patch("app.connectors.fivech.feedparser.parse") as parse:
            result = await FiveChConnector().fetch("乃木坂46", "all_info")

        parse.assert_not_called()
        assert len(result) == 1
        assert result[0].raw_payload["source"] == "5ch_itest"

    @pytest.mark.asyncio
    async def test_real_itest_uses_latest_dat_post_date(self):
        markdown = """
*   [2026年5月10日 10時00分 話題度:43 13レス 【乃木坂46】池田瑛紗応援スレ★104【てれぱん】](https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369)
"""
        dat = (
            "name<>sage<>2026/06/24(水) 20:18:37.20 ID:first<> first <>\n"
            "name<>sage<>2026/06/26(金) 20:00:21.58 ID:last<> latest <>\n"
        )
        dat_attempts = 0

        async def _side(url, **_kw):
            nonlocal dat_attempts
            url_text = str(url)
            if url_text.startswith("https://r.jina.ai/http://https://itest.5ch.io/subback/nogizaka"):
                return MagicMock(is_success=True, status_code=200, text=markdown, content=markdown.encode())
            if url_text.startswith("https://r.jina.ai/http://http://mevius.5ch.net/nogizaka/dat/1782410369.dat"):
                dat_attempts += 1
                if dat_attempts == 1:
                    return MagicMock(is_success=False, status_code=429, text="", content=b"")
                return MagicMock(is_success=True, status_code=200, text=dat, content=dat.encode())
            return MagicMock(is_success=True, status_code=200, text="", content=b"")

        with patch("app.connectors.fivech.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.fivech.asyncio.sleep", new=AsyncMock()) as sleep, \
             patch("app.connectors.fivech.feedparser.parse") as parse:
            result = await FiveChConnector().fetch("乃木坂46", "all_info")

        parse.assert_not_called()
        sleep.assert_awaited_once()
        assert dat_attempts == 2
        assert len(result) == 1
        assert result[0].published_at == datetime(2026, 6, 26, 11, 0, 21, tzinfo=timezone.utc)
        assert result[0].raw_payload["subback_published_at"] == "2026-05-10T01:00:00+00:00"
        assert result[0].raw_payload["last_post_at"] == "2026-06-26T11:00:21+00:00"
        assert result[0].raw_payload["date_source"] == "dat_latest_post"

    @pytest.mark.asyncio
    async def test_direct_scan_returns_thread_with_latest_post_date(self):
        subject = "1778433981.dat<>【元乃木坂４６】相楽伊織応援スレ★16【いおり】 (64)\n"
        dat = (
            "君の名は<><>2026/06/24(水) 20:18:37.20 ID:first<> first <>title\n"
            "君の名は<><>2026/06/25(木) 11:26:55.28 ID:last<> latest <>\n"
        )

        async def _side(url, **_kw):
            resp = MagicMock(is_success=True, status_code=200)
            if str(url).endswith("/nogizaka/subject.txt"):
                resp.content = subject.encode("shift_jis")
            elif str(url).endswith("/nogizaka/dat/1778433981.dat"):
                resp.content = dat.encode("shift_jis")
            else:
                resp.content = b""
            return resp

        with patch("app.connectors.fivech.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.fivech.feedparser.parse") as parse:
            result = await FiveChConnector()._fetch_direct("乃木坂46")

        parse.assert_not_called()
        assert len(result) == 1
        assert result[0].platform == "5ch"
        assert result[0].item_id == "2ch.sc:toro.2ch.sc:nogizaka:1778433981"
        assert result[0].url == "http://toro.2ch.sc/test/read.cgi/nogizaka/1778433981/"
        assert result[0].published_at == datetime(2026, 6, 25, 2, 26, 55, tzinfo=timezone.utc)
        assert result[0].raw_payload["source"] == "2ch.sc_subject"
        assert result[0].raw_payload["date_parsed"] is True

    @pytest.mark.asyncio
    async def test_direct_scan_falls_back_to_thread_id_date_when_dat_missing(self):
        subject = "1717200000.dat<>Aiko live thread (12)\n"

        async def _side(url, **_kw):
            if str(url).endswith("/streaming/subject.txt"):
                resp = MagicMock(is_success=True, status_code=200, content=subject.encode("shift_jis"))
                return resp
            if str(url).endswith("/streaming/dat/1717200000.dat"):
                return MagicMock(is_success=False, status_code=404, content=b"")
            return MagicMock(is_success=True, status_code=200, content=b"")

        with patch("app.connectors.fivech.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.fivech.feedparser.parse") as parse:
            result = await FiveChConnector()._fetch_direct("Aiko")

        parse.assert_not_called()
        assert len(result) == 1
        assert result[0].published_at == datetime.fromtimestamp(1717200000, tz=timezone.utc)
        assert result[0].raw_payload["date_parsed"] is False

    @pytest.mark.asyncio
    async def test_direct_scan_uses_worker_proxy_when_subject_and_dat_are_unparseable(self):
        from urllib.parse import parse_qs, urlparse

        subject = "1778433981.dat<>【元乃木坂46】Aiko proxy thread (64)\n"
        dat = "君の名は<><>2026/06/25(木) 11:26:55.28 ID:last<> latest <>\n"

        async def _side(url, **_kw):
            url_text = str(url)
            if url_text.startswith("https://worker.example/fivech-proxy"):
                query = parse_qs(urlparse(url_text).query)
                board_url = query.get("board_url", [""])[0]
                resource = query.get("resource", [""])[0]
                if board_url.endswith("/nogizaka/") and resource == "subject":
                    return MagicMock(is_success=True, status_code=200, content=subject.encode("shift_jis"))
                if board_url.endswith("/nogizaka/") and resource == "dat":
                    return MagicMock(is_success=True, status_code=200, content=dat.encode("shift_jis"))
                return MagicMock(is_success=True, status_code=200, content=b"")
            return MagicMock(is_success=True, status_code=200, content=b"Blocked: Only HTTPS requests are allowed")

        with patch("app.connectors.fivech.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.fivech.settings.admin_api_token", "secret"), \
             patch("app.connectors.fivech.settings.source_5ch_proxy_url", "https://worker.example/fivech-proxy"), \
             patch("app.connectors.fivech._DEFAULT_5CH_PROXY_URL", "https://worker.example/fivech-proxy"), \
             patch("app.connectors.fivech.feedparser.parse") as parse:
            result = await FiveChConnector()._fetch_direct("Aiko")

        parse.assert_not_called()
        assert len(result) == 1
        assert result[0].published_at == datetime(2026, 6, 25, 2, 26, 55, tzinfo=timezone.utc)
        assert result[0].raw_payload["date_parsed"] is True

    @pytest.mark.asyncio
    async def test_filters_keyword_found_only_in_google_news_summary(self):
        entry = _rss_entry(
            link="https://5ch.net/test/read.cgi/news/1",
            title="unrelated thread",
            summary="Aiko appears elsewhere in the Google News cluster",
        )
        with patch("app.connectors.fivech.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.fivech.feedparser.parse", return_value=_FakeFeed([entry])):
            result = await FiveChConnector().fetch("Aiko", "all_info")
        assert result == []

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
        e1 = _rss_entry(link="https://5ch.net/t1", item_id="dup_id", title="Aiko A")
        e2 = _rss_entry(link="https://5ch.net/t2", item_id="dup_id", title="Aiko B")
        fake_feed = _FakeFeed([e1, e2])
        with patch("app.connectors.fivech.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.fivech.feedparser.parse", return_value=fake_feed):
            result = await FiveChConnector().fetch("Aiko", "all_info")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_filters_google_news_items_without_keyword(self):
        fake_feed = _FakeFeed([_rss_entry(link="https://5ch.net/t1", title="unrelated thread")])
        with patch("app.connectors.fivech.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.fivech.feedparser.parse", return_value=fake_feed):
            result = await FiveChConnector().fetch("Aiko", "all_info")
        assert result == []


class TestGirlsChannelFetch:
    # Minimal HTML mimicking a GirlsChannel search results page
    _HTML_WITH_TOPICS = """
    <html><body>
      <li>
        <h3><a href="/topics/12345/">Thread Title アイコ</a></h3>
        <time datetime="2026-06-24T10:30:00+09:00">2026年6月24日</time>
      </li>
      <li>
        <h3><a href="/topics/99999/">Another アイコ thread</a></h3>
      </li>
    </body></html>
    """

    @pytest.mark.asyncio
    async def test_direct_scrape_returns_items(self):
        with patch("app.connectors.girlschannel.httpx.AsyncClient",
                   _http_mock(text=self._HTML_WITH_TOPICS)):
            result = await GirlsChannelConnector().fetch("アイコ", "all_info")
        assert len(result) >= 1
        first = next(r for r in result if r.item_id == "12345")
        assert first.platform == "girlschannel"
        assert "girlschannel.net/topics/12345" in first.url
        assert first.published_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_parsed_date_flagged_true_placeholder_flagged_false(self):
        # Topic 12345 has a <time> element (real date); 99999 has none (placeholder).
        # The scheduler relies on raw_payload["date_parsed"] to avoid re-pinning
        # undated threads to the top of the feed on every poll.
        with patch("app.connectors.girlschannel.httpx.AsyncClient",
                   _http_mock(text=self._HTML_WITH_TOPICS)):
            result = await GirlsChannelConnector().fetch("アイコ", "all_info")
        by_id = {r.item_id: r for r in result}
        assert by_id["12345"].raw_payload["date_parsed"] is True
        assert by_id["99999"].raw_payload["date_parsed"] is False

    @pytest.mark.asyncio
    async def test_direct_scrape_deduplicates_by_topic_id(self):
        html = """<html><body>
          <a href="/topics/100/">Aiko T1</a>
          <a href="/topics/100/">Aiko T1 again</a>
          <a href="/topics/101/">Aiko T2</a>
        </body></html>"""
        with patch("app.connectors.girlschannel.httpx.AsyncClient", _http_mock(text=html)):
            result = await GirlsChannelConnector().fetch("Aiko", "all_info")
        ids = [r.item_id for r in result]
        assert ids.count("100") == 1

    @pytest.mark.asyncio
    async def test_direct_http_error_falls_back_to_gnews(self):
        call_count = [0]
        fail_resp = MagicMock(is_success=False, status_code=403)
        ok_resp = MagicMock(is_success=True, content=b"<rss/>")

        async def _side(url, **kw):
            call_count[0] += 1
            return fail_resp if call_count[0] == 1 else ok_resp

        valid = _rss_entry(link="https://girlschannel.net/t1", title="アイコ")
        fake_feed = _FakeFeed([valid])
        with patch("app.connectors.girlschannel.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.girlschannel.feedparser.parse", return_value=fake_feed):
            result = await GirlsChannelConnector().fetch("アイコ", "all_info")
        assert len(result) == 1
        assert result[0].platform == "girlschannel"

    @pytest.mark.asyncio
    async def test_both_fail_returns_empty(self):
        fail_resp = MagicMock(is_success=False, status_code=500)

        async def _side(url, **kw):
            return fail_resp

        with patch("app.connectors.girlschannel.httpx.AsyncClient", _nico_ctx(side_effect=_side)):
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
    async def test_media_only_returns_empty(self):
        result = await GirlsChannelConnector().fetch("Aiko", "media_only")
        assert result == []

    def test_direct_parser_filters_items_without_keyword(self):
        html = """<html><body>
          <li><h3><a href="/topics/12345/">unrelated thread</a></h3></li>
        </body></html>"""
        result = GirlsChannelConnector()._parse_html(html, "Aiko")
        assert result == []

    def test_direct_parser_filters_stale_dated_items(self):
        html = """<html><body>
          <li>
            <h3><a href="/topics/12345/">Aiko old thread</a></h3>
            <time datetime="2026-04-23T15:00:00+00:00">2026年4月23日</time>
          </li>
        </body></html>"""
        result = GirlsChannelConnector()._parse_html(html, "Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_gnews_filters_keyword_found_only_in_summary(self):
        entry = _rss_entry(
            link="https://girlschannel.net/topics/12345/",
            title="unrelated thread",
            summary="Aiko appears elsewhere in the Google News cluster",
        )
        with patch("app.connectors.girlschannel.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.girlschannel.feedparser.parse", return_value=_FakeFeed([entry])):
            result = await GirlsChannelConnector()._fetch_gnews("Aiko")
        assert result == []


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
        e1 = _rss_entry(link="https://mdpr.jp/a1", item_id="dup", title="Aiko A")
        e2 = _rss_entry(link="https://mdpr.jp/a2", item_id="dup", title="Aiko B")
        fake_feed = _FakeFeed([e1, e2])
        with patch("app.connectors.mdpr.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.mdpr.feedparser.parse", return_value=fake_feed):
            result = await ModelPressConnector().fetch("Aiko", "all_info")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_filters_google_news_items_without_keyword(self):
        fake_feed = _FakeFeed([_rss_entry(link="https://mdpr.jp/a1", title="unrelated - モデルプレス")])
        with patch("app.connectors.mdpr.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.mdpr.feedparser.parse", return_value=fake_feed):
            result = await ModelPressConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_keyword_found_only_in_google_news_summary(self):
        entry = _rss_entry(
            link="https://mdpr.jp/a1",
            title="unrelated - モデルプレス",
            summary="Aiko appears elsewhere in the Google News cluster",
        )
        with patch("app.connectors.mdpr.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.mdpr.feedparser.parse", return_value=_FakeFeed([entry])):
            result = await ModelPressConnector().fetch("Aiko", "all_info")
        assert result == []


class TestOriconFetch:
    @pytest.mark.asyncio
    async def test_returns_items_with_title_cleaned_and_author_set(self):
        valid = _rss_entry(link="https://oricon.co.jp/a1", title="Aiko 受賞 - ORICON NEWS")
        no_link = _FeedEntry(id="nl", link="", title="skip")
        empty_title = _rss_entry(link="https://oricon.co.jp/a2", title="  - ORICON NEWS")
        fake_feed = _FakeFeed([valid, no_link, empty_title])
        with patch("app.connectors.oricon.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.oricon.feedparser.parse", return_value=fake_feed):
            result = await OriconConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].title == "Aiko 受賞"
        assert result[0].author == "ORICON NEWS"

    @pytest.mark.asyncio
    async def test_filters_keyword_found_only_in_google_news_summary(self):
        entry = _rss_entry(
            link="https://oricon.co.jp/a1",
            title="unrelated - ORICON NEWS",
            summary="Aiko appears elsewhere in the Google News cluster",
        )
        with patch("app.connectors.oricon.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.oricon.feedparser.parse", return_value=_FakeFeed([entry])):
            result = await OriconConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_stale_google_news_items(self):
        entry = _rss_entry(link="https://oricon.co.jp/old", title="Aiko old - ORICON NEWS")
        entry.published_parsed = (2023, 8, 4, 7, 0, 0, 4, 216, 0)
        with patch("app.connectors.oricon.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.oricon.feedparser.parse", return_value=_FakeFeed([entry])):
            result = await OriconConnector().fetch("Aiko", "all_info")
        assert result == []

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
        e1 = _rss_entry(link="https://oricon.co.jp/a1", item_id="dup", title="Aiko A")
        e2 = _rss_entry(link="https://oricon.co.jp/a2", item_id="dup", title="Aiko B")
        fake_feed = _FakeFeed([e1, e2])
        with patch("app.connectors.oricon.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.oricon.feedparser.parse", return_value=fake_feed):
            result = await OriconConnector().fetch("Aiko", "all_info")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_filters_google_news_items_without_keyword(self):
        fake_feed = _FakeFeed([_rss_entry(link="https://oricon.co.jp/a1", title="受賞 - ORICON NEWS")])
        with patch("app.connectors.oricon.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.oricon.feedparser.parse", return_value=fake_feed):
            result = await OriconConnector().fetch("Aiko", "all_info")
        assert result == []


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

    @pytest.mark.asyncio
    async def test_filters_items_when_keyword_only_appears_in_summary(self):
        entry = _rss_entry(
            link="https://natalie.mu/1",
            title="unrelated entertainment story",
            summary="Aiko appears in the feed summary",
        )
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.rss.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.rss.feedparser.parse", return_value=fake_feed):
            result = await RSSConnector().fetch("aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_stale_history_proxy_items(self):
        stale = _rss_entry(link="https://example.com/old", title="Aiko old news")
        stale.published_parsed = (2023, 8, 4, 7, 0, 0, 4, 216, 0)
        fake_feed = _FakeFeed([stale])
        with patch("app.connectors.rss.FEEDS", []), \
             patch("app.connectors.rss.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.rss.feedparser.parse", return_value=fake_feed):
            result = await RSSConnector().fetch("Aiko", "all_info")
        assert result == []


class TestNewsSiteFetch:
    def _ameba_html(self, entries):
        state = {"blogEntry": {"blogEntryMap": {str(entry["entryId"]): entry for entry in entries}}}
        return f"<html><script>window.__STATE__={_json_mod.dumps(state, ensure_ascii=False)};</script></html>"

    @pytest.mark.asyncio
    async def test_ameblo_fetches_direct_search_entries(self):
        html = self._ameba_html([
            {
                "entryId": 12969666351,
                "amebaId": "ps-jj-myonsun",
                "entryCreatedDatetime": 1717200000000,
                "blogTitle": "クムルクダ子のブログ",
                "entryTitle": "日本映画『国宝』視聴♪",
                "entryContent": "キャスト:<span>吉沢亮</span>(立花喜久雄)",
                "firstImageUrl": "https://stat.ameba.jp/image.jpg",
            }
        ])
        connector = AmebloConnector()
        with patch("app.connectors.news_sites.httpx.AsyncClient", _http_mock(text=html)), \
             patch.object(connector, "_fetch_gnews", new=AsyncMock()) as gnews:
            result = await connector.fetch("吉沢亮", "all_info")
        assert len(result) == 1
        assert result[0].platform == "ameblo"
        assert result[0].item_id == "12969666351"
        assert result[0].url == "https://ameblo.jp/ps-jj-myonsun/entry-12969666351.html"
        assert "吉沢亮" in result[0].title
        assert result[0].content_text == "キャスト: 吉沢亮 (立花喜久雄)"
        assert result[0].author == "クムルクダ子のブログ"
        assert result[0].thumbnail_url == "https://stat.ameba.jp/image.jpg"
        assert result[0].raw_payload["source"] == "ameba_search"
        gnews.assert_not_called()

    @pytest.mark.asyncio
    async def test_ameblo_uses_updated_entry_timestamp(self):
        html = self._ameba_html([
            {
                "entryId": 12969666351,
                "amebaId": "ps-jj-myonsun",
                "entryCreatedDatetime": 1717200000000,
                "entryUpdatedDatetime": 1717286400000,
                "blogTitle": "クムルクダ子のブログ",
                "entryTitle": "日本映画『国宝』視聴♪",
                "entryContent": "吉沢亮 update",
            }
        ])
        connector = AmebloConnector()
        with patch("app.connectors.news_sites.httpx.AsyncClient", _http_mock(text=html)), \
             patch.object(connector, "_fetch_gnews", new=AsyncMock()) as gnews:
            result = await connector.fetch("吉沢亮", "all_info")

        assert len(result) == 1
        assert result[0].published_at == datetime.fromtimestamp(1717286400, tz=timezone.utc)
        gnews.assert_not_called()

    @pytest.mark.asyncio
    async def test_ameblo_filters_direct_entries_without_keyword(self):
        html = self._ameba_html([
            {
                "entryId": 129,
                "amebaId": "blog",
                "entryCreatedDatetime": 1717200000000,
                "blogTitle": "映画ブログ",
                "entryTitle": "日本映画『国宝』視聴♪",
                "entryContent": "横浜流星の感想",
            }
        ])
        connector = AmebloConnector()
        with patch("app.connectors.news_sites.httpx.AsyncClient", _http_mock(text=html)), \
             patch.object(connector, "_fetch_gnews", new=AsyncMock(return_value=[])):
            result = await connector.fetch("吉沢亮", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_ameblo_falls_back_to_google_news_when_direct_search_empty(self):
        connector = AmebloConnector()
        fallback = [
            SourceItemCreate(
                platform="ameblo",
                item_id="gnews-1",
                url="https://news.google.com/rss/articles/1",
                published_at=datetime.now(timezone.utc),
                media_type="article",
                title="吉沢亮のブログ記事",
            )
        ]
        with patch("app.connectors.news_sites.httpx.AsyncClient", _http_mock(text="<html></html>")), \
             patch.object(connector, "_fetch_gnews", new=AsyncMock(side_effect=[fallback])) as gnews:
            result = await connector.fetch("吉沢亮", "all_info")
        assert result == fallback
        gnews.assert_awaited_once_with("吉沢亮")

    @pytest.mark.asyncio
    async def test_filters_google_news_items_without_keyword(self):
        fake_feed = _FakeFeed([
            _rss_entry(link="https://realsound.jp/a1", title="unrelated entertainment news")
        ])
        with patch("app.connectors.news_sites.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.news_sites.feedparser.parse", return_value=fake_feed):
            result = await RealSoundConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_google_news_items_when_keyword_only_appears_in_summary(self):
        fake_feed = _FakeFeed([
            _rss_entry(
                link="https://realsound.jp/a1",
                title="杉野遥亮、『世にも奇妙な物語』で初主演",
                summary="吉沢亮 appears in a related Google News cluster item",
            )
        ])
        with patch("app.connectors.news_sites.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.news_sites.feedparser.parse", return_value=fake_feed):
            result = await RealSoundConnector().fetch("吉沢亮", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_realsound_uses_direct_search_results(self):
        html = """
        <article class="entry-summary">
          <time datetime="2026-06-25T05:30"></time>
          <h3 class="entry-title">
            <a href="/2026/06/post-1364043.html">吉沢亮がゲスト出演</a>
          </h3>
          <p class="entry-excerpt">番組の出演者が発表された。</p>
          <div class="entry-author">リアルサウンド編集部</div>
          <img src="/wp-content/uploads/example.jpg">
        </article>
        """
        connector = RealSoundConnector()
        with patch("app.connectors.news_sites.httpx.AsyncClient", _http_mock(text=html)), \
             patch.object(connector, "_fetch_gnews", new=AsyncMock()) as gnews:
            result = await connector.fetch("吉沢亮", "all_info")

        assert len(result) == 1
        assert result[0].title == "吉沢亮がゲスト出演"
        assert result[0].url == "https://realsound.jp/2026/06/post-1364043.html"
        assert result[0].thumbnail_url == "https://realsound.jp/wp-content/uploads/example.jpg"
        assert result[0].author == "リアルサウンド編集部"
        assert result[0].published_at == datetime(2026, 6, 24, 20, 30, tzinfo=timezone.utc)
        gnews.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_realsound_direct_search_filters_unrelated_titles(self):
        html = """
        <article class="entry-summary">
          <h3 class="entry-title">
            <a href="/2026/06/post-1.html">杉野遥亮がドラマ初主演</a>
          </h3>
          <p class="entry-excerpt">吉沢亮の関連記事。</p>
        </article>
        """
        connector = RealSoundConnector()
        with patch("app.connectors.news_sites.httpx.AsyncClient", _http_mock(text=html)), \
             patch.object(connector, "_fetch_gnews", new=AsyncMock(return_value=[])) as gnews:
            result = await connector.fetch("吉沢亮", "all_info")

        assert result == []
        assert gnews.await_count == 2

    @pytest.mark.asyncio
    async def test_livedoor_filters_google_news_summary_only_matches(self):
        fake_feed = _FakeFeed([
            _rss_entry(
                link="https://news.livedoor.com/article/detail/1/",
                title="別の芸能ニュース - ライブドアニュース",
                summary="Aiko appears in a related Google News cluster item",
            )
        ])
        with patch("app.connectors.news_sites.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.news_sites.feedparser.parse", return_value=fake_feed):
            result = await LivedoorConnector().fetch("Aiko", "all_info")
        assert result == []


_TOGETTER_HTML = """
<html><body><ul>
  <li>
    <h3>Aikoの人気まとめ</h3>
    <a href="https://togetter.com/li/1234567">Aikoのまとめ</a>
    <time datetime="2026-06-24T12:00:00+00:00">Jun 24</time>
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
        assert result[0].title == "Aikoの人気まとめ"
        assert result[0].thumbnail_url == "https://i.togetter.com/t.jpg"

    @pytest.mark.asyncio
    async def test_merges_newest_title_and_tag_search_results(self):
        direct_html = """
        <html><body><ul>
          <li>
            <h3>吉沢亮 古いまとめ</h3>
            <a href="https://togetter.com/li/100">吉沢亮 old</a>
            <time datetime="2026-06-10T23:57:13+09:00">older</time>
          </li>
        </ul></body></html>
        """
        tag_html = """
        <html><body><ul>
          <li>
            <h3>吉沢亮 新しいタグまとめ</h3>
            <a href="https://togetter.com/li/200">吉沢亮 new</a>
            <time datetime="2026-06-25T07:56:04+09:00">new</time>
          </li>
        </ul></body></html>
        """
        calls = []

        async def _side(_url, params=None, **_kw):
            calls.append(params)
            resp = MagicMock(is_success=True, status_code=200)
            resp.text = tag_html if params and str(params.get("q", "")).startswith("tag:") else direct_html
            return resp

        with patch("app.connectors.togetter.httpx.AsyncClient", _nico_ctx(side_effect=_side)):
            result = await TogetterConnector().fetch("吉沢亮", "all_info")

        assert len(result) == 2
        assert result[0].item_id == "200"
        assert result[0].published_at.isoformat() == "2026-06-25T07:56:04+09:00"
        assert result[0].raw_payload["source"] == "tag_search"
        assert any(call.get("sort") == "created_at" for call in calls)

    @pytest.mark.asyncio
    async def test_filters_stale_html_results(self):
        html = """
        <html><body><ul>
          <li>
            <h3>Aiko staleまとめ</h3>
            <a href="https://togetter.com/li/300">Aiko stale</a>
            <time datetime="2026-03-11T13:17:39+09:00">stale</time>
          </li>
        </ul></body></html>
        """
        with patch("app.connectors.togetter.httpx.AsyncClient",
                   _http_mock(text=html, is_success=True)):
            result = await TogetterConnector().fetch("Aiko", "all_info")
        assert result == []

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
          <li><h3>Aiko Title A</h3>
            <a href="https://togetter.com/li/99999">Aiko TA</a>
          </li>
          <li><h3>Aiko Title B</h3>
            <a href="https://togetter.com/li/99999">Aiko TB</a>
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
            f'<li><h3>Aiko Title {i}</h3><a href="https://togetter.com/li/{i:05d}">Aiko T{i}</a></li>'
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
            <h3>Aiko タイトル</h3>
            <a href="https://togetter.com/li/22222">Aiko T</a>
            <time datetime="not-a-valid-date">text</time>
          </li>
        </ul></body></html>
        """
        with patch("app.connectors.togetter.httpx.AsyncClient",
                   _http_mock(text=html, is_success=True)):
            result = await TogetterConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].item_id == "22222"
        assert result[0].raw_payload["date_parsed"] is False

    @pytest.mark.asyncio
    async def test_filters_items_without_keyword(self):
        html = """
        <html><body><ul>
          <li><h3>unrelated title</h3>
            <a href="https://togetter.com/li/33333">unrelated body</a>
          </li>
        </ul></body></html>
        """
        with patch("app.connectors.togetter.httpx.AsyncClient",
                   _http_mock(text=html, is_success=True)):
            result = await TogetterConnector().fetch("Aiko", "all_info")
        assert result == []


_JINA_MARKDOWN = """
# Yahoo News Search Results

1. [アイコの新曲情報](https://news.yahoo.co.jp/articles/abc123)
2. [アイコの別ニュース](https://news.yahoo.co.jp/articles/xyz789)
3. [重複エントリ](https://news.yahoo.co.jp/articles/abc123)
"""


class TestYahooNewsFetch:
    def test_cleans_google_news_html_summary(self):
        summary = (
            '<a href="https://news.google.com/rss/articles/ABC?oc=5">'
            "アイコの最新情報</a>&nbsp;&nbsp;"
            '<font color="#6f6f6f">Yahoo!ニュース</font>'
        )
        assert _clean_html_summary(summary) == "アイコの最新情報 Yahoo!ニュース"

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
            summary=(
                '<a href="https://news.google.com/rss/articles/abc123">'
                "アイコの最新情報</a> Yahoo!ニュース"
            ),
        )
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.yahoonews.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.yahoonews.feedparser.parse", return_value=fake_feed):
            result = await YahooNewsConnector().fetch("アイコ", "all_info")
        assert len(result) == 1
        assert result[0].platform == "yahoonews"
        assert result[0].content_text == "アイコの最新情報 Yahoo!ニュース"
        assert "https://" not in result[0].content_text

    @pytest.mark.asyncio
    async def test_gnews_filters_keyword_found_only_in_summary(self):
        entry = _FeedEntry(
            id="https://news.yahoo.co.jp/articles/abc123",
            link="https://news.yahoo.co.jp/articles/abc123",
            title="関係のないニュース",
            summary="<p>アイコ appears elsewhere in the Google News cluster</p>",
        )
        with patch("app.connectors.yahoonews.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.yahoonews.feedparser.parse", return_value=_FakeFeed([entry])):
            result = await YahooNewsConnector()._fetch_gnews_rss("アイコ")
        assert result == []

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
            f"{i+1}. [アイコ Title{i}](https://news.yahoo.co.jp/articles/art{i:03d})"
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
            title="アイコ Dup A",
        )
        dup2 = _FeedEntry(
            id="https://news.yahoo.co.jp/articles/dup",
            link="https://news.yahoo.co.jp/articles/dup",
            title="アイコ Dup B",
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

    @pytest.mark.asyncio
    async def test_gnews_rss_filters_items_without_keyword(self):
        fake_feed = _FakeFeed([
            _FeedEntry(
                id="https://news.yahoo.co.jp/articles/abc1",
                link="https://news.yahoo.co.jp/articles/abc1",
                title="unrelated title",
                summary="unrelated summary",
            )
        ])
        with patch("app.connectors.yahoonews.httpx.AsyncClient", _http_mock(content=b"<rss/>")), \
             patch("app.connectors.yahoonews.feedparser.parse", return_value=fake_feed):
            result = await YahooNewsConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_jina_filters_items_without_keyword(self):
        empty_feed = _FakeFeed([])
        markdown = "1. [unrelated title](https://news.yahoo.co.jp/articles/abc123)"
        with patch("app.connectors.yahoonews.httpx.AsyncClient",
                   _http_mock(content=b"<rss/>", text=markdown)), \
             patch("app.connectors.yahoonews.feedparser.parse", return_value=empty_feed):
            result = await YahooNewsConnector().fetch("Aiko", "all_info")
        assert result == []


# ---------------------------------------------------------------------------
# NicoNico connector tests
# ---------------------------------------------------------------------------

def _nico_ctx(side_effect=None, rss_content=b"<rss/>"):
    """Create a shared httpx context mock; supports optional call-count-based side effects."""
    if side_effect is not None:
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=side_effect)
    else:
        resp = MagicMock()
        resp.is_success = True
        resp.content = rss_content
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


class TestNicoNicoFetch:
    @pytest.mark.asyncio
    async def test_gnews_filters_keyword_found_only_in_summary(self):
        entry = _rss_entry(
            link="https://www.nicovideo.jp/watch/sm12345",
            title="unrelated video",
            summary="Aiko appears elsewhere in the Google News cluster",
        )
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx()), \
             patch("app.connectors.niconico.feedparser.parse", return_value=_FakeFeed([entry])):
            result = await NicoNicoConnector()._fetch_gnews("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_rss_returns_items_on_success(self):
        entry = _rss_entry(link="https://www.nicovideo.jp/watch/sm12345", title="Aiko cover")
        entry["media_thumbnail"] = [{"url": "https://cdn.nicovideo.jp/t.jpg"}]
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx()), \
             patch("app.connectors.niconico.feedparser.parse", return_value=fake_feed):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].platform == "niconico"
        assert result[0].item_id == "sm12345"
        assert result[0].thumbnail_url == "https://cdn.nicovideo.jp/t.jpg"
        assert result[0].media_type == "video"

    @pytest.mark.asyncio
    async def test_rss_filters_stale_items(self):
        entry = _rss_entry(link="https://www.nicovideo.jp/watch/sm12345", title="Aiko old cover")
        entry.published_parsed = (2023, 8, 4, 7, 0, 0, 4, 216, 0)
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx()), \
             patch("app.connectors.niconico.feedparser.parse", return_value=fake_feed):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_extracts_video_id_from_watch_url(self):
        entry = _rss_entry(link="https://www.nicovideo.jp/watch/nm99999", title="Test video")
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx()), \
             patch("app.connectors.niconico.feedparser.parse", return_value=fake_feed):
            result = await NicoNicoConnector().fetch("test", "all_info")
        assert result[0].item_id == "nm99999"
        assert "nm99999" in result[0].url

    @pytest.mark.asyncio
    async def test_search_rss_fails_falls_back_to_tag_rss(self):
        call_count = [0]
        fail_resp = MagicMock(is_success=False, status_code=403)
        ok_resp = MagicMock(is_success=True, content=b"<rss/>")

        async def _side(url, **kw):
            call_count[0] += 1
            return fail_resp if call_count[0] == 1 else ok_resp

        entry = _rss_entry(link="https://www.nicovideo.jp/watch/sm42", title="Aiko Tag video")
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.niconico.feedparser.parse", return_value=fake_feed):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].item_id == "sm42"

    @pytest.mark.asyncio
    async def test_both_rss_fail_falls_back_to_gnews(self):
        call_count = [0]
        fail_resp = MagicMock(is_success=False, status_code=403)
        ok_resp = MagicMock(is_success=True, content=b"<rss/>")

        async def _side(url, **kw):
            call_count[0] += 1
            return fail_resp if call_count[0] <= 2 else ok_resp

        gnews_entry = _rss_entry(link="https://www.nicovideo.jp/watch/sm88", title="Aiko GNews video")
        fake_feed = _FakeFeed([gnews_entry])
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(side_effect=_side)), \
             patch("app.connectors.niconico.feedparser.parse", return_value=fake_feed):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_all_sources_fail_returns_empty(self):
        fail_resp = MagicMock(is_success=False, status_code=503)

        async def _side(url, **kw):
            return fail_resp

        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx(side_effect=_side)):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_entries_without_title(self):
        entries = [
            _rss_entry(link="https://www.nicovideo.jp/watch/sm1", title="Aiko Good"),
            _rss_entry(link="https://www.nicovideo.jp/watch/sm2", title=""),
            _FeedEntry(id="nl", link="", title="no link"),
        ]
        fake_feed = _FakeFeed(entries)
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx()), \
             patch("app.connectors.niconico.feedparser.parse", return_value=fake_feed):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].item_id == "sm1"

    @pytest.mark.asyncio
    async def test_deduplicates_by_video_id(self):
        e1 = _rss_entry(link="https://www.nicovideo.jp/watch/sm99", title="Aiko V1")
        e2 = _rss_entry(link="https://www.nicovideo.jp/watch/sm99", title="Aiko V2")
        fake_feed = _FakeFeed([e1, e2])
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx()), \
             patch("app.connectors.niconico.feedparser.parse", return_value=fake_feed):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_feedparser_exception_returns_empty(self):
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx()), \
             patch("app.connectors.niconico.feedparser.parse", side_effect=ValueError("bad")):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_rss_filters_items_without_keyword(self):
        entry = _rss_entry(link="https://www.nicovideo.jp/watch/sm12345", title="unrelated video")
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.niconico.httpx.AsyncClient", _nico_ctx()), \
             patch("app.connectors.niconico.feedparser.parse", return_value=fake_feed):
            result = await NicoNicoConnector().fetch("Aiko", "all_info")
        assert result == []


# ---------------------------------------------------------------------------
# Note connector tests
# ---------------------------------------------------------------------------

class TestNoteFetch:
    @pytest.mark.asyncio
    async def test_empty_keyword_returns_empty(self):
        result = await NoteConnector().fetch("   ", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_rss_returns_items(self):
        rss_entry = _rss_entry(link="https://note.com/u/n/abc1", title="Aiko article")
        fake_feed = _FakeFeed([rss_entry])
        rss_resp = MagicMock()
        rss_resp.is_success = True
        rss_resp.content = b"<rss/>"
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=rss_resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.note.feedparser.parse", return_value=fake_feed):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].platform == "note"

    @pytest.mark.asyncio
    async def test_rss_http_error_returns_empty(self):
        rss_resp = MagicMock()
        rss_resp.is_success = False
        rss_resp.status_code = 404
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=rss_resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_rss_feedparser_exception_returns_empty(self):
        rss_resp = MagicMock()
        rss_resp.is_success = True
        rss_resp.content = b"<rss/>"
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=rss_resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.note.feedparser.parse", side_effect=ValueError("bad")):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_rss_skips_entry_without_link_and_extracts_enclosure_thumb(self):
        no_link = _FeedEntry(id="nl", link="", title="skip")
        with_enclosure = _FeedEntry(
            id="enc1", link="https://note.com/u/n/enc1",
            title="Aiko Article with thumb",
            enclosures=[{"type": "image/jpeg", "href": "https://assets.st-note.com/enc/t.jpg"}],
        )
        fake_feed = _FakeFeed([no_link, with_enclosure])
        rss_resp = MagicMock()
        rss_resp.is_success = True
        rss_resp.content = b"<rss/>"
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=rss_resp)
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
            title="Aiko Article",
            media_thumbnail=[{"url": "https://assets.st-note.com/media/t.jpg"}],
        )
        fake_feed = _FakeFeed([entry])
        rss_resp = MagicMock()
        rss_resp.is_success = True
        rss_resp.content = b"<rss/>"
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=rss_resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.note.feedparser.parse", return_value=fake_feed):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert result[0].thumbnail_url == "https://assets.st-note.com/media/t.jpg"

    @pytest.mark.asyncio
    async def test_rss_filters_items_without_keyword(self):
        entry = _FeedEntry(
            id="nid1",
            link="https://note.com/u/n/nid1",
            title="unrelated article",
            summary="unrelated summary",
        )
        fake_feed = _FakeFeed([entry])
        rss_resp = MagicMock()
        rss_resp.is_success = True
        rss_resp.content = b"<rss/>"
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=rss_resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.note.feedparser.parse", return_value=fake_feed):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_rss_filters_keyword_found_only_in_summary_or_author(self):
        entry = _FeedEntry(
            id="nid1",
            link="https://note.com/u/n/nid1",
            title="unrelated article",
            summary="Aiko appears only in the article summary",
            author="Aiko fan",
        )
        fake_feed = _FakeFeed([entry])
        rss_resp = MagicMock(is_success=True, content=b"<rss/>")
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=rss_resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.note.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.connectors.note.feedparser.parse", return_value=fake_feed):
            result = await NoteConnector().fetch("Aiko", "all_info")
        assert result == []


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
            media=[{"media_key": "mk1", "preview_image_url": "https://pbs.twimg.com/mv/t.jpg", "type": "video"}],
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
    async def test_photo_tweet_uses_image_media_type(self):
        data = _twitter_api_data(
            tweets=[{
                "id": "t_photo",
                "author_id": "u_photo",
                "text": "Aiko photo tweet!",
                "created_at": "2024-01-15T12:00:00Z",
                "attachments": {"media_keys": ["mk_photo"]},
            }],
            users=[{"id": "u_photo", "username": "photofan", "name": "Photo Fan"}],
            media=[{"media_key": "mk_photo", "url": "https://pbs.twimg.com/media/photo.jpg", "type": "photo"}],
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
        assert result[0].media_type == "image"
        assert result[0].thumbnail_url == "https://pbs.twimg.com/media/photo.jpg"

    @pytest.mark.asyncio
    async def test_tweet_without_username_uses_fallback_url(self):
        data = _twitter_api_data(
            tweets=[{
                "id": "t3",
                "author_id": "u_unknown",
                "text": "Aiko Tweet",
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

    @pytest.mark.asyncio
    async def test_http_error_status_returns_empty(self):
        resp = MagicMock()
        resp.is_success = False
        resp.status_code = 429
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.twitter.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await TwitterConnector(bearer_token="tok").fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_network_exception_returns_empty(self):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.twitter.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await TwitterConnector(bearer_token="tok").fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_tweets_without_keyword(self):
        data = _twitter_api_data(
            tweets=[{
                "id": "t1",
                "author_id": "u1",
                "text": "unrelated tweet",
                "created_at": "2024-01-15T10:00:00Z",
                "attachments": {},
            }],
            users=[{"id": "u1", "username": "otherfan", "name": "Other Fan"}],
        )
        resp = MagicMock()
        resp.is_success = True
        resp.json.return_value = data
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.connectors.twitter.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await TwitterConnector(bearer_token="tok").fetch("Aiko", "all_info")
        assert result == []


# ─── _parse_tver_date exception-path tests ────────────────────────────────────

class TestParseTverDateExceptions:
    """Covers exception-handler branches in _parse_tver_date (lines 23-24, 28-29, 54-55)."""

    def test_overflow_timestamp_falls_through_to_none(self):
        # Very large int → fromtimestamp raises OverflowError → pass → no other keys → None
        result = _parse_tver_date({"publishedAt": 99999999999999999})
        assert result is None

    def test_invalid_iso_string_falls_through_to_none(self):
        # String that can't be parsed as ISO → ValueError caught → None
        result = _parse_tver_date({"publishedAt": "not-a-valid-date"})
        assert result is None

    def test_invalid_month_day_raises_valueerror_and_is_caught(self):
        # Feb 30 causes datetime() to raise ValueError (lines 54-55)
        fixed_now = datetime(2024, 2, 15, 12, 0, 0, tzinfo=timezone.utc)
        with patch("app.connectors.tver.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromtimestamp = datetime.fromtimestamp
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _parse_tver_date({"broadcastDateLabel": "2月30日(金)放送分"})
        assert result is None


# ─── TVer helpers ─────────────────────────────────────────────────────────────

def _tver_token_resp(uid="uid1", token="tok1", status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"result": {"platform_uid": uid, "platform_token": token}}
    return resp


def _tver_search_resp(is_success=True, episodes=None, structure="episodes_contents"):
    resp = MagicMock()
    resp.is_success = is_success
    resp.status_code = 200 if is_success else 404
    eps = episodes or []
    if structure == "episodes_contents":
        resp.json.return_value = {"result": {"episodes": {"contents": eps}}}
    elif structure == "series_and_episode":
        resp.json.return_value = {"result": {"seriesAndEpisode": {"episodes": {"contents": eps}}}}
    elif structure == "result_contents":
        resp.json.return_value = {"result": {"contents": eps}}
    elif structure == "result_rows":
        resp.json.return_value = {"result": {"rows": eps}}
    elif structure == "data_level":
        resp.json.return_value = {"result": {}, "contents": eps}
    return resp


def _tver_client_ctx(token_resp, search_resp=None, token_exc=None, search_exc=None):
    client_mock = AsyncMock()
    if token_exc is not None:
        client_mock.post = AsyncMock(side_effect=token_exc)
    else:
        client_mock.post = AsyncMock(return_value=token_resp)
    if search_exc is not None:
        client_mock.get = AsyncMock(side_effect=search_exc)
    elif search_resp is not None:
        client_mock.get = AsyncMock(return_value=search_resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _tver_ep(ep_id="ep001", title="Aiko Drama", ep_type="episode",
             published_at_unix=1781913600, thumb=None, author="NHK"):
    content = {
        "id": ep_id,
        "title": title,
        "publishedAt": published_at_unix,
        "broadcasterName": author,
        "description": "Aiko desc",
    }
    if thumb:
        content["thumbnailUrl"] = thumb
    return {"type": ep_type, "content": content}


class TestTVERFetch:
    @pytest.mark.asyncio
    async def test_no_token_returns_empty(self):
        tr = _tver_token_resp(status_code=500)
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_token_exception_returns_empty(self):
        tr = _tver_token_resp()
        with patch("app.connectors.tver.httpx.AsyncClient",
                   _tver_client_ctx(tr, token_exc=Exception("network err"))):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_api_http_error_returns_empty(self):
        tr = _tver_token_resp()
        sr = _tver_search_resp(is_success=False)
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_api_exception_returns_empty(self):
        tr = _tver_token_resp()
        with patch("app.connectors.tver.httpx.AsyncClient",
                   _tver_client_ctx(tr, search_exc=Exception("conn refused"))):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_json_parse_error_returns_empty(self):
        tr = _tver_token_resp()
        sr = _tver_search_resp()
        sr.json.side_effect = ValueError("bad json")
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_success_episodes_contents_structure(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="ep001", title="アイコのドラマ")
        sr = _tver_search_resp(episodes=[ep])
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].platform == "tver"
        assert result[0].item_id == "ep001"
        assert result[0].title == "アイコのドラマ"
        assert result[0].url == "https://tver.jp/episodes/ep001"
        assert result[0].author == "NHK"
        assert result[0].media_type == "video"

    @pytest.mark.asyncio
    async def test_missing_search_date_uses_episode_detail_view_start(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="epdetail", title="Aiko Detail", published_at_unix=None)
        sr = _tver_search_resp(episodes=[ep])
        detail = MagicMock(is_success=True, status_code=200)
        detail.json.return_value = {"viewStatus": {"startAt": 1782302400, "endAt": 1782831599}}
        client_mock = AsyncMock()
        client_mock.post = AsyncMock(return_value=tr)
        client_mock.get = AsyncMock(side_effect=[sr, detail])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.connectors.tver.httpx.AsyncClient", MagicMock(return_value=ctx)):
            result = await TVERConnector().fetch("Aiko", "all_info")

        assert len(result) == 1
        assert result[0].published_at == datetime.fromtimestamp(1782302400, tz=timezone.utc)
        assert result[0].raw_payload["date_source"] == "episode_detail"

    @pytest.mark.asyncio
    async def test_missing_all_dates_skips_episode(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="epnodate", title="Aiko No Date", published_at_unix=None)
        sr = _tver_search_resp(episodes=[ep])
        detail = MagicMock(is_success=True, status_code=200)
        detail.json.return_value = {}
        client_mock = AsyncMock()
        client_mock.post = AsyncMock(return_value=tr)
        client_mock.get = AsyncMock(side_effect=[sr, detail])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.connectors.tver.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch.object(TVERConnector, "_fetch_indexed_history", new=AsyncMock(return_value=[])):
            result = await TVERConnector().fetch("Aiko", "all_info")

        assert result == []

    @pytest.mark.asyncio
    async def test_stale_search_date_skips_episode(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="epold", title="Aiko Old", published_at_unix=1590969600)
        sr = _tver_search_resp(episodes=[ep])
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)), \
             patch.object(TVERConnector, "_fetch_indexed_history", new=AsyncMock(return_value=[])):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_success_series_and_episode_structure(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="ep002", title="シリーズドラマ", ep_type="series")
        sr = _tver_search_resp(episodes=[ep], structure="series_and_episode")
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].url == "https://tver.jp/series/ep002"

    @pytest.mark.asyncio
    async def test_success_result_contents_structure(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="ep003", title="コンテンツ")
        sr = _tver_search_resp(episodes=[ep], structure="result_contents")
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].item_id == "ep003"

    @pytest.mark.asyncio
    async def test_success_result_rows_structure(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="ep004", title="行データ")
        sr = _tver_search_resp(episodes=[ep], structure="result_rows")
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].item_id == "ep004"

    @pytest.mark.asyncio
    async def test_success_data_level_fallback(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="ep005", title="データレベル")
        sr = _tver_search_resp(episodes=[ep], structure="data_level")
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].item_id == "ep005"

    @pytest.mark.asyncio
    async def test_skips_episode_without_id(self):
        tr = _tver_token_resp()
        ep = {"type": "episode", "content": {"title": "No ID"}}
        sr = _tver_search_resp(episodes=[ep])
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_episode_without_title(self):
        tr = _tver_token_resp()
        ep = {"type": "episode", "content": {"id": "ep999"}}
        sr = _tver_search_resp(episodes=[ep])
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result == []

    @pytest.mark.asyncio
    async def test_url_type_special(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="sp001", title="スペシャル", ep_type="special")
        sr = _tver_search_resp(episodes=[ep])
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result[0].url == "https://tver.jp/specials/sp001"

    @pytest.mark.asyncio
    async def test_thumbnail_http_url_used_directly(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="ep010", title="T", thumb="https://cdn.example.com/thumb.jpg")
        sr = _tver_search_resp(episodes=[ep])
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result[0].thumbnail_url == "https://cdn.example.com/thumb.jpg"

    @pytest.mark.asyncio
    async def test_thumbnail_relative_path_prefixed(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="ep011", title="T", thumb="/img/thumb.jpg")
        sr = _tver_search_resp(episodes=[ep])
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result[0].thumbnail_url == "https://statics.tver.jp/img/thumb.jpg"

    @pytest.mark.asyncio
    async def test_episode_parse_exception_skips_and_continues(self):
        tr = _tver_token_resp()
        bad_ep = None  # None.get() raises AttributeError → caught by except Exception
        good_ep = _tver_ep(ep_id="ep012", title="Good Episode")
        sr = _tver_search_resp(episodes=[bad_ep, good_ep])
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert len(result) == 1
        assert result[0].item_id == "ep012"

    @pytest.mark.asyncio
    async def test_filters_episodes_without_keyword(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="ep013", title="unrelated episode")
        ep["content"]["description"] = "unrelated description"
        ep["content"]["broadcasterName"] = "Other"
        sr = _tver_search_resp(episodes=[ep])
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result == []


# ─── YouTube helpers ──────────────────────────────────────────────────────────

_YT_SCRAPE_DATA = {
    "contents": {
        "twoColumnSearchResultsRenderer": {
            "primaryContents": {
                "sectionListRenderer": {
                    "contents": [
                        {
                            "itemSectionRenderer": {
                                "contents": [
                                    {
                                        "videoRenderer": {
                                            "videoId": "scr001",
                                            "title": {"runs": [{"text": "Aiko Scraped Video"}]},
                                            "ownerText": {"runs": [{"text": "ScrapedChannel"}]},
                                            "detailedMetadataSnippets": [
                                                {"snippetText": {"runs": [{"text": "Aiko desc text"}]}}
                                            ],
                                            "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/scr001/hq.jpg"}]},
                                            "publishedTimeText": {"simpleText": "1 day ago"},
                                        }
                                    },
                                    {"videoRenderer": {}},
                                ]
                            }
                        }
                    ]
                }
            }
        }
    }
}

_YT_SCRAPE_HTML = (
    "<html><script>var ytInitialData = "
    + _json_mod.dumps(_YT_SCRAPE_DATA)
    + ";</script></html>"
)

_YT_OLD_SCRAPE_DATA = {
    "contents": {
        "twoColumnSearchResultsRenderer": {
            "primaryContents": {
                "sectionListRenderer": {
                    "contents": [
                        {
                            "itemSectionRenderer": {
                                "contents": [
                                    {
                                        "videoRenderer": {
                                            "videoId": "oldvid001",
                                            "title": {"runs": [{"text": "Old Video"}]},
                                            "ownerText": {"runs": [{"text": "OldCh"}]},
                                            "publishedTimeText": {"simpleText": "5 months ago"},
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            }
        }
    }
}

_YT_OLD_SCRAPE_HTML = (
    "<html><script>var ytInitialData = "
    + _json_mod.dumps(_YT_OLD_SCRAPE_DATA)
    + ";</script></html>"
)


def _yt_api_ctx(items=None, raise_status_exc=None, get_exc=None):
    resp = MagicMock()
    if raise_status_exc:
        resp.raise_for_status = MagicMock(side_effect=raise_status_exc)
    else:
        resp.raise_for_status = MagicMock()
    resp.json.return_value = {"items": items or []}
    client_mock = AsyncMock()
    if get_exc:
        client_mock.get = AsyncMock(side_effect=get_exc)
    else:
        client_mock.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _yt_scrape_ctx(html="", is_success=True, get_exc=None):
    resp = MagicMock()
    resp.is_success = is_success
    resp.status_code = 200 if is_success else 403
    resp.text = html
    client_mock = AsyncMock()
    if get_exc:
        client_mock.get = AsyncMock(side_effect=get_exc)
    else:
        client_mock.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _yt_gnews_ctx(content=b"", is_success=True, get_exc=None):
    resp = MagicMock()
    resp.is_success = is_success
    resp.status_code = 200 if is_success else 503
    resp.content = content
    client_mock = AsyncMock()
    if get_exc:
        client_mock.get = AsyncMock(side_effect=get_exc)
    else:
        client_mock.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _yt_api_item(vid_id="v001", title="Aiko Test MV", channel="Test Ch",
                 published="2026-06-20T10:00:00Z",
                 thumb="https://i.ytimg.com/vi/v001/thumb.jpg"):
    return {
        "id": {"videoId": vid_id},
        "snippet": {
            "title": title,
            "channelTitle": channel,
            "publishedAt": published,
            "description": "Aiko description",
            "thumbnails": {"medium": {"url": thumb}},
        },
    }


class TestParseYouTubeRelativeYear:
    """Covers year unit and final return None (lines 32-33)."""

    def test_english_year(self):
        from datetime import timedelta
        result = _parse_youtube_relative("1 year ago")
        assert result is not None
        expected = datetime.now(timezone.utc) - timedelta(days=365)
        assert abs((result - expected).total_seconds()) < 10

    def test_japanese_year(self):
        result = _parse_youtube_relative("2年前")
        assert result is not None

    def test_unrecognized_unit_returns_none(self):
        # Has a number but no recognized unit → falls to return None (line 33)
        assert _parse_youtube_relative("3 decades ago") is None


class TestYouTubeApiFetch:
    @pytest.mark.asyncio
    async def test_success_returns_items(self):
        with patch("app.connectors.youtube.httpx.AsyncClient",
                   _yt_api_ctx(items=[_yt_api_item(vid_id="v001")])):
            result = await YouTubeConnector(api_key="key")._fetch_api("Aiko")
        assert len(result) == 1
        assert result[0].item_id == "v001"
        assert result[0].platform == "youtube"
        assert result[0].url == "https://www.youtube.com/watch?v=v001"
        assert result[0].author == "Test Ch"
        assert result[0].media_type == "video"

    @pytest.mark.asyncio
    async def test_skips_item_without_video_id(self):
        no_id = {
            "id": {},
            "snippet": {
                "title": "X",
                "publishedAt": "2024-01-01T00:00:00Z",
                "channelTitle": "C",
                "description": "",
            },
        }
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_api_ctx(items=[no_id])):
            result = await YouTubeConnector(api_key="key")._fetch_api("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        import httpx
        exc = httpx.HTTPStatusError("403", request=MagicMock(), response=MagicMock())
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_api_ctx(raise_status_exc=exc)):
            with pytest.raises(httpx.HTTPStatusError):
                await YouTubeConnector(api_key="key")._fetch_api("Aiko")

    @pytest.mark.asyncio
    async def test_network_exception_propagates(self):
        import httpx
        with patch("app.connectors.youtube.httpx.AsyncClient",
                   _yt_api_ctx(get_exc=httpx.ConnectError("timeout"))):
            with pytest.raises(httpx.ConnectError):
                await YouTubeConnector(api_key="key")._fetch_api("Aiko")

    @pytest.mark.asyncio
    async def test_filters_api_items_without_keyword(self):
        item = _yt_api_item(title="unrelated video", channel="Other Ch")
        item["snippet"]["description"] = "unrelated description"
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_api_ctx(items=[item])):
            result = await YouTubeConnector(api_key="key")._fetch_api("Aiko")
        assert result == []


class TestYouTubeScrapeFetch:
    @pytest.mark.asyncio
    async def test_success_parses_ytInitialData(self):
        with patch("app.connectors.youtube.httpx.AsyncClient",
                   _yt_scrape_ctx(html=_YT_SCRAPE_HTML)):
            result = await YouTubeConnector(api_key="")._fetch_scrape("Aiko")
        assert len(result) == 1
        assert result[0].item_id == "scr001"
        assert result[0].title == "Aiko Scraped Video"
        assert result[0].author == "ScrapedChannel"
        assert result[0].thumbnail_url == "https://i.ytimg.com/vi/scr001/hq.jpg"
        assert result[0].platform == "youtube"
        assert result[0].media_type == "video"

    @pytest.mark.asyncio
    async def test_http_failure_returns_empty(self):
        with patch("app.connectors.youtube.httpx.AsyncClient",
                   _yt_scrape_ctx(is_success=False)):
            result = await YouTubeConnector(api_key="")._fetch_scrape("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_ytInitialData_returns_empty(self):
        with patch("app.connectors.youtube.httpx.AsyncClient",
                   _yt_scrape_ctx(html="<html><body>no data here</body></html>")):
            result = await YouTubeConnector(api_key="")._fetch_scrape("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_json_in_ytInitialData_returns_empty(self):
        html = "var ytInitialData = {not_valid_json:};"
        with patch("app.connectors.youtube.httpx.AsyncClient",
                   _yt_scrape_ctx(html=html)):
            result = await YouTubeConnector(api_key="")._fetch_scrape("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_old_items_beyond_cutoff_filtered_out(self):
        with patch("app.connectors.youtube.httpx.AsyncClient",
                   _yt_scrape_ctx(html=_YT_OLD_SCRAPE_HTML)):
            result = await YouTubeConnector(api_key="")._fetch_scrape("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_scraped_items_without_keyword(self):
        with patch("app.connectors.youtube.httpx.AsyncClient",
                   _yt_scrape_ctx(html=_YT_SCRAPE_HTML)):
            result = await YouTubeConnector(api_key="")._fetch_scrape("Missing")
        assert result == []


class TestYouTubeGnewsFetch:
    @pytest.mark.asyncio
    async def test_filters_keyword_found_only_in_summary(self):
        entry = _FeedEntry(
            link="https://www.youtube.com/watch?v=gnews001",
            id="gnews001",
            title="unrelated video",
            summary="Aiko appears elsewhere in the Google News cluster",
        )
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_gnews_ctx(content=b"<rss/>")), \
             patch("app.connectors.youtube.feedparser.parse", return_value=_FakeFeed([entry])):
            result = await YouTubeConnector(api_key="")._fetch_gnews("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_success_returns_items(self):
        entry = _FeedEntry(
            link="https://www.youtube.com/watch?v=gnews001",
            id="gnews001",
            title="Aiko YouTube",
            summary="desc",
        )
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_gnews_ctx(content=b"<rss/>")), \
             patch("app.connectors.youtube.feedparser.parse", return_value=fake_feed):
            result = await YouTubeConnector(api_key="")._fetch_gnews("Aiko")
        assert len(result) == 1
        assert result[0].platform == "youtube"
        assert result[0].url == "https://www.youtube.com/watch?v=gnews001"
        assert result[0].title == "Aiko YouTube"
        assert result[0].media_type == "video"

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_gnews_ctx(is_success=False)):
            result = await YouTubeConnector(api_key="")._fetch_gnews("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_network_exception_returns_empty(self):
        with patch("app.connectors.youtube.httpx.AsyncClient",
                   _yt_gnews_ctx(get_exc=Exception("connection error"))):
            result = await YouTubeConnector(api_key="")._fetch_gnews("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_feedparser_exception_returns_empty(self):
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_gnews_ctx(content=b"<rss/>")), \
             patch("app.connectors.youtube.feedparser.parse", side_effect=Exception("parse fail")):
            result = await YouTubeConnector(api_key="")._fetch_gnews("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_dedup_prevents_duplicate_items(self):
        entry = _FeedEntry(link="https://youtube.com/watch?v=dup", id="dup", title="Aiko Dup Video")
        fake_feed = _FakeFeed([entry, entry])
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_gnews_ctx(content=b"<rss/>")), \
             patch("app.connectors.youtube.feedparser.parse", return_value=fake_feed):
            result = await YouTubeConnector(api_key="")._fetch_gnews("Aiko")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_skip_entry_without_link(self):
        no_link = _FeedEntry(link="", id="no-link", title="No Link Video")
        fake_feed = _FakeFeed([no_link])
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_gnews_ctx(content=b"<rss/>")), \
             patch("app.connectors.youtube.feedparser.parse", return_value=fake_feed):
            result = await YouTubeConnector(api_key="")._fetch_gnews("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_skip_entry_without_title(self):
        no_title = _FeedEntry(link="https://youtube.com/watch?v=notitle", id="notitle", title="")
        fake_feed = _FakeFeed([no_title])
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_gnews_ctx(content=b"<rss/>")), \
             patch("app.connectors.youtube.feedparser.parse", return_value=fake_feed):
            result = await YouTubeConnector(api_key="")._fetch_gnews("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_gnews_items_without_keyword(self):
        entry = _FeedEntry(link="https://youtube.com/watch?v=other", id="other", title="unrelated video")
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_gnews_ctx(content=b"<rss/>")), \
             patch("app.connectors.youtube.feedparser.parse", return_value=fake_feed):
            result = await YouTubeConnector(api_key="")._fetch_gnews("Aiko")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_stale_gnews_items(self):
        entry = _FeedEntry(
            link="https://youtube.com/watch?v=old",
            id="old",
            title="Aiko old video",
            published_parsed=(2026, 3, 28, 13, 18, 13, 5, 87, 0),
        )
        fake_feed = _FakeFeed([entry])
        with patch("app.connectors.youtube.httpx.AsyncClient", _yt_gnews_ctx(content=b"<rss/>")), \
             patch("app.connectors.youtube.feedparser.parse", return_value=fake_feed):
            result = await YouTubeConnector(api_key="")._fetch_gnews("Aiko")
        assert result == []


class TestYouTubeFetchOrchestration:
    def _item(self, vid_id="v1"):
        return SourceItemCreate(
            platform="youtube", item_id=vid_id,
            url=f"https://www.youtube.com/watch?v={vid_id}",
            published_at=datetime.now(timezone.utc),
            media_type="video",
        )

    @pytest.mark.asyncio
    async def test_api_key_present_uses_fetch_api(self):
        items = [self._item("v1")]
        with patch.object(YouTubeConnector, "_fetch_api", new=AsyncMock(return_value=items)):
            result = await YouTubeConnector(api_key="my-key").fetch("Aiko", "all_info")
        assert result == items

    @pytest.mark.asyncio
    async def test_api_failure_falls_back_to_scrape(self):
        items = [self._item("v2")]
        with patch.object(YouTubeConnector, "_fetch_api", new=AsyncMock(side_effect=Exception("api err"))), \
             patch.object(YouTubeConnector, "_fetch_scrape", new=AsyncMock(return_value=items)):
            result = await YouTubeConnector(api_key="my-key").fetch("Aiko", "all_info")
        assert result == items

    @pytest.mark.asyncio
    async def test_no_api_key_skips_to_scrape(self):
        items = [self._item("v3")]
        with patch.object(YouTubeConnector, "_fetch_scrape", new=AsyncMock(return_value=items)):
            result = await YouTubeConnector(api_key="").fetch("Aiko", "all_info")
        assert result == items

    @pytest.mark.asyncio
    async def test_empty_scrape_falls_to_gnews(self):
        items = [self._item("v4")]
        with patch.object(YouTubeConnector, "_fetch_scrape", new=AsyncMock(return_value=[])), \
             patch.object(YouTubeConnector, "_fetch_gnews", new=AsyncMock(return_value=items)):
            result = await YouTubeConnector(api_key="").fetch("Aiko", "all_info")
        assert result == items

    @pytest.mark.asyncio
    async def test_scrape_exception_falls_to_gnews(self):
        items = [self._item("v5")]
        with patch.object(YouTubeConnector, "_fetch_scrape", new=AsyncMock(side_effect=Exception("scrape err"))), \
             patch.object(YouTubeConnector, "_fetch_gnews", new=AsyncMock(return_value=items)):
            result = await YouTubeConnector(api_key="").fetch("Aiko", "all_info")
        assert result == items

    @pytest.mark.asyncio
    async def test_no_api_key_no_scrape_falls_to_gnews(self):
        items = [self._item("v6")]
        with patch.object(YouTubeConnector, "_fetch_scrape", new=AsyncMock(return_value=[])), \
             patch.object(YouTubeConnector, "_fetch_gnews", new=AsyncMock(return_value=items)):
            result = await YouTubeConnector(api_key="").fetch("Aiko", "all_info")
        assert result == items


class TestTVERThumbnailElseBranch:
    """Covers line 173 — thumb_raw with no http or / prefix is used as-is."""

    @pytest.mark.asyncio
    async def test_thumbnail_bare_value_used_directly(self):
        tr = _tver_token_resp()
        ep = _tver_ep(ep_id="ep020", title="T", thumb="relative-no-slash.jpg")
        sr = _tver_search_resp(episodes=[ep])
        with patch("app.connectors.tver.httpx.AsyncClient", _tver_client_ctx(tr, search_resp=sr)):
            result = await TVERConnector().fetch("Aiko", "all_info")
        assert result[0].thumbnail_url == "relative-no-slash.jpg"
