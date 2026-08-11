"""Tests for scheduler pruning logic and search-term building."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import inspect
from unittest.mock import AsyncMock, MagicMock

import app.ingestion.scheduler as scheduler
from app.database import Base
from unittest.mock import patch

from app.connectors import news_sites
from app.config import settings as app_settings
from app.ingestion.scheduler import (
    _build_connectors,
    _cache_successful_fetch,
    _connector_batches,
    _report_orphaned_duplicate_terms,
    _report_orphaned_silent_terms,
    _deliver_pending_notification,
    _report_orphaned_notification_terms,
    _fetch_one,
    _fetch_one_result,
    _is_notification_eligible,
    _notification_freshness_window,
    _prune_irrelevant_matches,
    _prune_old_items,
    _prune_old_items_with_limit,
    _poll_term_window,
    _queue_duplicate_term_notifications,
    _queue_pending_notification,
    _search_terms_for,
)
from app.connectors.base import SourceItemCreate
from app.connectors.youtube import YouTubeConnector
from app.connectors.twitter import TwitterConnector
from app.models import (
    APNSDeviceToken,
    BackendEvent,
    CollectionMode,
    Match,
    MutedFeedItem,
    PendingNotification,
    PlatformCredential,
    SourceItem,
    WatchTerm,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _add_items(db, term, platform, count, start_days_ago=200):
    """Insert `count` items and matches for a given platform."""
    for i in range(count):
        pub = datetime.now(timezone.utc) - timedelta(days=start_days_ago - i)
        item = SourceItem(
            id=f"{platform}:item{i}",
            platform=platform,
            item_id=f"item{i}",
            url=f"https://example.com/{platform}/{i}",
            published_at=pub,
            media_type="article",
        )
        db.add(item)
        db.flush()
        db.add(Match(watch_term_id=term.id, source_item_id=item.id))
    db.commit()


class TestPruneOldItems:
    def test_prune_removes_excess_matches(self, db):
        term = WatchTerm(keyword="k1", aliases=[])
        db.add(term)
        db.commit()

        _add_items(db, term, "youtube", 250)

        _prune_old_items(db)

        remaining = db.query(Match).filter(Match.watch_term_id == term.id).count()
        assert remaining == 200

    def test_prune_deletes_orphan_source_items(self, db):
        term = WatchTerm(keyword="k2", aliases=[])
        db.add(term)
        db.commit()

        _add_items(db, term, "news", 250)

        _prune_old_items(db)

        orphans = (
            db.query(SourceItem)
            .filter(
                ~SourceItem.id.in_(
                    db.query(Match.source_item_id)
                )
            )
            .count()
        )
        assert orphans == 0

    def test_prune_preserves_muted_source_items(self, db):
        term = WatchTerm(keyword="muted", aliases=[])
        item = SourceItem(
            id="news:muted",
            platform="news",
            item_id="muted",
            url="https://example.com/muted",
            published_at=datetime.now(timezone.utc) - timedelta(days=5),
            media_type="article",
        )
        db.add_all([term, item])
        db.commit()
        db.add(MutedFeedItem(watch_term_id=term.id, source_item_id=item.id))
        db.commit()

        _prune_old_items(db)

        assert db.get(SourceItem, item.id) is not None

    def test_prune_caps_muted_source_items_per_term(self, db):
        term = WatchTerm(keyword="muted", aliases=[])
        db.add(term)
        db.commit()

        now = datetime.now(timezone.utc)
        for index in range(4):
            item = SourceItem(
                id=f"news:muted{index}",
                platform="news",
                item_id=f"muted{index}",
                url=f"https://example.com/muted/{index}",
                published_at=now - timedelta(minutes=4 - index),
                media_type="article",
            )
            db.add(item)
            db.flush()
            db.add(MutedFeedItem(
                watch_term_id=term.id,
                source_item_id=item.id,
                created_at=now - timedelta(minutes=4 - index),
            ))
        db.commit()

        _prune_old_items_with_limit(db, muted_per_term_limit=2)

        remaining_source_ids = {
            mute.source_item_id
            for mute in db.query(MutedFeedItem).order_by(MutedFeedItem.created_at.desc()).all()
        }
        assert remaining_source_ids == {"news:muted2", "news:muted3"}
        assert db.get(SourceItem, "news:muted0") is None
        assert db.get(SourceItem, "news:muted1") is None
        assert db.get(SourceItem, "news:muted2") is not None
        assert db.get(SourceItem, "news:muted3") is not None

    def test_prune_skips_community_platforms(self, db):
        term = WatchTerm(keyword="k3", aliases=[])
        db.add(term)
        db.commit()

        for platform in ("5ch", "girlschannel"):
            _add_items(db, term, platform, 250)

        _prune_old_items(db)

        for platform in ("5ch", "girlschannel"):
            count = (
                db.query(Match)
                .join(SourceItem, SourceItem.id == Match.source_item_id)
                .filter(SourceItem.platform == platform)
                .count()
            )
            assert count == 250, f"{platform} should not be pruned"

    def test_prune_no_longer_preserves_removed_togetter_source(self, db):
        term = WatchTerm(keyword="k3", aliases=[])
        db.add(term)
        db.commit()

        _add_items(db, term, "togetter", 250)

        _prune_old_items(db)

        count = (
            db.query(Match)
            .join(SourceItem, SourceItem.id == Match.source_item_id)
            .filter(SourceItem.platform == "togetter")
            .count()
        )
        assert count == 200

    def test_prune_is_idempotent(self, db):
        term = WatchTerm(keyword="k4", aliases=[])
        db.add(term)
        db.commit()

        _add_items(db, term, "niconico", 210)

        _prune_old_items(db)
        count_after_first = db.query(Match).filter(Match.watch_term_id == term.id).count()

        _prune_old_items(db)
        count_after_second = db.query(Match).filter(Match.watch_term_id == term.id).count()

        assert count_after_first == count_after_second == 200

    def test_prune_keeps_newest_items(self, db):
        term = WatchTerm(keyword="k5", aliases=[])
        db.add(term)
        db.commit()

        _add_items(db, term, "news", 210, start_days_ago=210)

        _prune_old_items(db)

        kept_items = (
            db.query(SourceItem)
            .join(Match, Match.source_item_id == SourceItem.id)
            .filter(SourceItem.platform == "news")
            .order_by(SourceItem.published_at.desc())
            .all()
        )
        assert len(kept_items) == 200
        # The most recent 200 should have item_ids 209 down to 10
        newest_id = kept_items[0].item_id
        oldest_id = kept_items[-1].item_id
        assert int(newest_id.replace("item", "")) > int(oldest_id.replace("item", ""))


class TestSearchTermsFor:
    def _term(self, keyword: str, aliases: list[str]) -> WatchTerm:
        return WatchTerm(keyword=keyword, aliases=aliases)

    def test_returns_primary_keyword(self):
        term = self._term("Aiko", [])
        result = _search_terms_for(term)
        assert result == ["Aiko"]

    def test_includes_aliases(self):
        term = self._term("Aiko", ["相川愛子", "Aiko Chan"])
        result = _search_terms_for(term)
        assert result == ["Aiko", "相川愛子", "Aiko Chan"]

    def test_deduplicates_case_insensitive(self):
        # "aiko" and "Aiko" are the same after casefold — only the first survives
        term = self._term("Aiko", ["aiko", "AIKO", "相川愛子"])
        result = _search_terms_for(term)
        assert result == ["Aiko", "相川愛子"]

    def test_strips_whitespace_from_aliases(self):
        term = self._term("Miku", ["  Miku Hatsune  "])
        result = _search_terms_for(term)
        assert "Miku Hatsune" in result

    def test_ignores_empty_aliases(self):
        term = self._term("Test", ["", "  ", "Valid"])
        result = _search_terms_for(term)
        assert "" not in result
        assert "   " not in result
        assert "Valid" in result

    def test_primary_not_duplicated_when_present_as_alias(self):
        term = self._term("Oshi", ["Oshi", "oshi", "Other"])
        result = _search_terms_for(term)
        assert result.count("Oshi") == 1

    def test_none_aliases_treated_as_empty(self):
        term = WatchTerm(keyword="Miku")
        term.aliases = None  # Simulate a None aliases value (e.g. from a legacy DB row)
        result = _search_terms_for(term)
        assert result == ["Miku"]


class TestFetchCache:
    def test_does_not_cache_failed_fetch_results(self):
        cache = {}
        key = ("youtube", "Aiko", "all_info")

        _cache_successful_fetch(cache, key, [], fetch_succeeded=False)

        assert cache == {}

    def test_caches_successful_empty_fetch_results(self):
        cache = {}
        key = ("youtube", "Aiko", "all_info")

        _cache_successful_fetch(cache, key, [], fetch_succeeded=True)

        assert cache[key] == []

    def test_caches_non_empty_fetch_results(self):
        cache = {}
        key = ("youtube", "Aiko", "all_info")
        items = [object()]

        _cache_successful_fetch(cache, key, items, fetch_succeeded=True)

        assert cache[key] is items


class TestFetchOne:
    def _connector(self, side_effect=None, return_value=None, reports_status_to_client=True):
        c = MagicMock()
        c.PLATFORM = "mock"
        c.SUPPORTS_MEDIA_FILTER = True
        c.REPORTS_STATUS_TO_CLIENT = reports_status_to_client
        if side_effect is not None:
            c.fetch = AsyncMock(side_effect=side_effect)
        else:
            c.fetch = AsyncMock(return_value=return_value or [])
        return c

    @pytest.mark.asyncio
    async def test_connector_opted_out_of_status_reporting_skips_record_on_failure(self):
        connector = self._connector(
            side_effect=RuntimeError("boom"),
            reports_status_to_client=False,
        )
        with patch("app.ingestion.scheduler.record_fetch_result") as mock_record:
            items, succeeded = await _fetch_one_result(connector, "Aiko", CollectionMode.ALL_INFO)
        assert succeeded is False
        assert items == []
        mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_connector_opted_out_of_status_reporting_skips_record_on_success(self):
        from app.connectors.base import SourceItemCreate
        item = SourceItemCreate(
            platform="mock", item_id="x1",
            url="https://example.com/x1",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="Aiko news",
        )
        connector = self._connector(return_value=[item], reports_status_to_client=False)
        with patch("app.ingestion.scheduler.record_fetch_result") as mock_record:
            items, succeeded = await _fetch_one_result(connector, "Aiko", CollectionMode.ALL_INFO)
        assert succeeded is True
        assert len(items) == 1
        mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_error_records_exception_type_not_message(self):
        # str(exc) often embeds the search term (e.g. a request URL or a
        # connector's own error message); /api/source-health is public and
        # cross-user, so only the exception type name should be recorded.
        connector = self._connector(
            side_effect=RuntimeError("feed unavailable for keyword 'a-secret-watched-keyword'")
        )
        with patch("app.ingestion.scheduler.record_fetch_result") as mock_record:
            items, succeeded = await _fetch_one_result(connector, "a-secret-watched-keyword", CollectionMode.ALL_INFO)
        assert succeeded is False
        assert items == []
        mock_record.assert_called_once_with("mock", succeeded=False, error="RuntimeError")

    @pytest.mark.asyncio
    async def test_returns_connector_results_on_success(self):
        from app.connectors.base import SourceItemCreate
        from datetime import datetime, timezone
        item = SourceItemCreate(
            platform="mock", item_id="x1",
            url="https://example.com/x1",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="Aiko news",
        )
        connector = self._connector(return_value=[item])
        result = await _fetch_one(connector, "Aiko", CollectionMode.ALL_INFO)
        assert len(result) == 1
        assert result[0].item_id == "x1"

    @pytest.mark.asyncio
    async def test_filters_results_without_search_term(self):
        from app.connectors.base import SourceItemCreate
        from datetime import datetime, timezone
        item = SourceItemCreate(
            platform="mock", item_id="x1",
            url="https://example.com/x1",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="unrelated news",
        )
        connector = self._connector(return_value=[item])
        result = await _fetch_one(connector, "Aiko", CollectionMode.ALL_INFO)
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_keyword_found_only_in_description_or_author(self):
        from app.connectors.base import SourceItemCreate
        item = SourceItemCreate(
            platform="mock",
            item_id="x1",
            url="https://example.com/x1",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="unrelated news",
            content_text="Aiko appears only in the description",
            author="Aiko fan",
        )
        connector = self._connector(return_value=[item])
        result = await _fetch_one(connector, "Aiko", CollectionMode.ALL_INFO)
        assert result == []

    @pytest.mark.asyncio
    async def test_keeps_note_hashtag_match_without_visible_keyword(self):
        from app.connectors.base import SourceItemCreate
        item = SourceItemCreate(
            platform="note",
            item_id="x1",
            url="https://note.com/u/n/x1",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="hashtagged post with a different title",
            content_text="no visible match here",
            raw_payload={"matched_hashtag": "Aiko"},
        )
        connector = self._connector(return_value=[item])
        connector.PLATFORM = "note"
        result = await _fetch_one(connector, "Aiko", CollectionMode.ALL_INFO)
        assert result == [item]

    @pytest.mark.asyncio
    async def test_text_only_item_matches_post_body(self):
        from app.connectors.base import SourceItemCreate
        item = SourceItemCreate(
            platform="mock",
            item_id="x1",
            url="https://example.com/x1",
            published_at=datetime.now(timezone.utc),
            media_type="text",
            title=None,
            content_text="Aiko posted an update",
        )
        connector = self._connector(return_value=[item])
        result = await _fetch_one(connector, "Aiko", CollectionMode.ALL_INFO)
        assert result == [item]

    @pytest.mark.asyncio
    async def test_video_item_can_match_description(self):
        from app.connectors.base import SourceItemCreate
        item = SourceItemCreate(
            platform="tver",
            item_id="x1",
            url="https://example.com/x1",
            published_at=datetime.now(timezone.utc),
            media_type="video",
            title="Tonight's drama",
            content_text="Aiko appears as a guest",
            author="TVer",
        )
        connector = self._connector(return_value=[item])
        result = await _fetch_one(connector, "Aiko", CollectionMode.ALL_INFO)
        assert result == [item]

    @pytest.mark.asyncio
    async def test_logs_filtered_result_count(self, caplog):
        from app.connectors.base import SourceItemCreate
        from datetime import datetime, timezone
        item = SourceItemCreate(
            platform="mock", item_id="x1",
            url="https://example.com/x1",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="unrelated news",
        )
        connector = self._connector(return_value=[item])

        with caplog.at_level("INFO", logger="app.ingestion.scheduler"):
            result = await _fetch_one(connector, "Aiko", CollectionMode.ALL_INFO)

        assert result == []
        assert "filtered non-matching items connector=mock" in caplog.text

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_exception(self):
        connector = self._connector(side_effect=RuntimeError("network error"))
        result = await _fetch_one(connector, "Aiko", CollectionMode.ALL_INFO)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_timeout(self, caplog):
        connector = MagicMock()
        connector.PLATFORM = "mock"

        async def slow_fetch(_search_term, _mode):
            await asyncio.sleep(1)
            return []

        connector.fetch = slow_fetch
        with patch("app.ingestion.scheduler.settings") as s:
            s.connector_fetch_timeout_seconds = 0.01
            with caplog.at_level("WARNING", logger="app.ingestion.scheduler"):
                result = await _fetch_one(connector, "Aiko", CollectionMode.ALL_INFO)

        assert result == []
        assert "fetch timeout connector=mock" in caplog.text

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_http_error(self):
        import httpx
        connector = self._connector(side_effect=httpx.ConnectError("timeout"))
        result = await _fetch_one(connector, "Miku", CollectionMode.MEDIA_ONLY)
        assert result == []

    @pytest.mark.asyncio
    async def test_passes_mode_to_connector(self):
        connector = self._connector(return_value=[])
        await _fetch_one(connector, "Test", CollectionMode.MEDIA_ONLY)
        connector.fetch.assert_awaited_once_with("Test", CollectionMode.MEDIA_ONLY)

    @pytest.mark.asyncio
    async def test_skips_network_for_connector_without_media_filter_support(self):
        connector = self._connector(return_value=[])
        connector.SUPPORTS_MEDIA_FILTER = False

        result = await _fetch_one(connector, "Test", CollectionMode.MEDIA_ONLY)

        assert result == []
        connector.fetch.assert_not_awaited()


class TestConnectorBatches:
    def test_limits_peak_connector_concurrency(self):
        connectors = [MagicMock() for _ in range(10)]

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.connector_concurrency = 4
            batches = _connector_batches(connectors)

        assert [len(batch) for batch in batches] == [4, 4, 2]
        assert [connector for batch in batches for connector in batch] == connectors

    def test_never_uses_zero_sized_batches(self):
        connectors = [MagicMock() for _ in range(3)]

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.connector_concurrency = 0
            batches = _connector_batches(connectors)

        assert [len(batch) for batch in batches] == [1, 1, 1]


class TestMutedFeedItems:
    def _source_item(self, db, item_id: str = "thread1") -> SourceItem:
        item = SourceItem(
            id=f"5ch:{item_id}",
            platform="5ch",
            item_id=item_id,
            url=f"https://example.com/{item_id}",
            published_at=datetime.now(timezone.utc) - timedelta(hours=1),
            media_type="article",
            title="Aiko discussion",
        )
        db.add(item)
        db.commit()
        return item

    def _raw_item(self, item: SourceItem) -> SourceItemCreate:
        return SourceItemCreate(
            platform=item.platform,
            item_id=item.item_id,
            url=item.url,
            published_at=datetime.now(timezone.utc),
            media_type=item.media_type,
            title=item.title,
            raw_payload={"date_parsed": True},
        )

    def test_duplicate_notifications_do_not_recreate_muted_match(self, db):
        primary = WatchTerm(keyword="Aiko", aliases=[], notify_on_new=True, is_active=True)
        duplicate = WatchTerm(keyword="Aiko", aliases=[], notify_on_new=True, is_active=True)
        item = self._source_item(db)
        db.add_all([primary, duplicate])
        db.commit()
        db.add(MutedFeedItem(watch_term_id=duplicate.id, source_item_id=item.id))
        db.commit()

        _queue_duplicate_term_notifications(
            db,
            primary,
            [self._raw_item(item)],
            datetime.now(timezone.utc),
        )

        assert db.query(Match).filter_by(watch_term_id=duplicate.id, source_item_id=item.id).count() == 0
        assert db.get(PendingNotification, duplicate.id) is None

    def test_duplicate_notifications_ignore_muted_reply_update_match(self, db):
        primary = WatchTerm(keyword="Aiko", aliases=[], notify_on_new=True, is_active=True)
        duplicate = WatchTerm(keyword="Aiko", aliases=[], notify_on_new=True, is_active=True)
        item = self._source_item(db)
        db.add_all([primary, duplicate])
        db.commit()
        db.add_all([
            Match(watch_term_id=duplicate.id, source_item_id=item.id),
            MutedFeedItem(watch_term_id=duplicate.id, source_item_id=item.id),
        ])
        db.commit()

        _queue_duplicate_term_notifications(
            db,
            primary,
            [self._raw_item(item)],
            datetime.now(timezone.utc),
            discussion_reply_source_ids={item.id},
        )

        assert db.get(PendingNotification, duplicate.id) is None


class TestDuplicateTermFiltering:
    def _source_item(self, db, platform: str = "twitter", media_type: str = "text") -> SourceItem:
        item = SourceItem(
            id=f"{platform}:thread1",
            platform=platform,
            item_id="thread1",
            url=f"https://example.com/{platform}/thread1",
            published_at=datetime.now(timezone.utc) - timedelta(hours=1),
            media_type=media_type,
            title="Aiko update",
        )
        db.add(item)
        db.commit()
        return item

    def _raw_item(self, item: SourceItem) -> SourceItemCreate:
        return SourceItemCreate(
            platform=item.platform,
            item_id=item.item_id,
            url=item.url,
            published_at=datetime.now(timezone.utc),
            media_type=item.media_type,
            title=item.title,
            raw_payload={"date_parsed": True},
        )

    def test_duplicate_term_scoped_to_other_platform_is_not_notified(self, db):
        primary = WatchTerm(keyword="Aiko", aliases=[], notify_on_new=True, is_active=True)
        duplicate = WatchTerm(
            keyword="Aiko",
            aliases=[],
            notify_on_new=True,
            is_active=True,
            source_mode="selected",
            selected_platforms=["youtube"],
        )
        item = self._source_item(db, platform="twitter")
        db.add_all([primary, duplicate])
        db.commit()

        _queue_duplicate_term_notifications(
            db,
            primary,
            [self._raw_item(item)],
            datetime.now(timezone.utc),
        )

        assert db.query(Match).filter_by(watch_term_id=duplicate.id, source_item_id=item.id).count() == 0
        assert db.get(PendingNotification, duplicate.id) is None

    def test_duplicate_term_scoped_to_matching_platform_is_notified(self, db):
        primary = WatchTerm(keyword="Aiko", aliases=[], notify_on_new=True, is_active=True)
        duplicate = WatchTerm(
            keyword="Aiko",
            aliases=[],
            notify_on_new=True,
            is_active=True,
            source_mode="selected",
            selected_platforms=["twitter"],
        )
        item = self._source_item(db, platform="twitter")
        db.add_all([primary, duplicate])
        db.commit()

        _queue_duplicate_term_notifications(
            db,
            primary,
            [self._raw_item(item)],
            datetime.now(timezone.utc),
        )

        assert db.query(Match).filter_by(watch_term_id=duplicate.id, source_item_id=item.id).count() == 1

    def test_duplicate_term_scoped_to_media_only_ignores_text_item(self, db):
        primary = WatchTerm(keyword="Aiko", aliases=[], notify_on_new=True, is_active=True)
        duplicate = WatchTerm(
            keyword="Aiko",
            aliases=[],
            notify_on_new=True,
            is_active=True,
            collection_mode="media_only",
        )
        item = self._source_item(db, platform="twitter", media_type="text")
        db.add_all([primary, duplicate])
        db.commit()

        _queue_duplicate_term_notifications(
            db,
            primary,
            [self._raw_item(item)],
            datetime.now(timezone.utc),
        )

        assert db.query(Match).filter_by(watch_term_id=duplicate.id, source_item_id=item.id).count() == 0
        assert db.get(PendingNotification, duplicate.id) is None

    def test_duplicate_term_scoped_to_media_only_allows_image_item(self, db):
        primary = WatchTerm(keyword="Aiko", aliases=[], notify_on_new=True, is_active=True)
        duplicate = WatchTerm(
            keyword="Aiko",
            aliases=[],
            notify_on_new=True,
            is_active=True,
            collection_mode="media_only",
        )
        item = self._source_item(db, platform="twitter", media_type="image")
        db.add_all([primary, duplicate])
        db.commit()

        _queue_duplicate_term_notifications(
            db,
            primary,
            [self._raw_item(item)],
            datetime.now(timezone.utc),
        )

        assert db.query(Match).filter_by(watch_term_id=duplicate.id, source_item_id=item.id).count() == 1


class TestPollTermWindow:
    def _terms(self, db, count: int) -> list[WatchTerm]:
        terms = [WatchTerm(keyword=f"term-{index}", aliases=[]) for index in range(count)]
        db.add_all(terms)
        db.commit()
        return db.query(WatchTerm).order_by(WatchTerm.id).all()

    def test_limits_terms_per_run(self, db):
        terms = self._terms(db, 8)

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.poll_terms_per_run = 5
            selected, offset, next_offset = _poll_term_window(db, terms)

        assert [term.keyword for term in selected] == [f"term-{index}" for index in range(5)]
        assert offset == 0
        assert next_offset == 0

    def test_selects_oldest_due_terms_even_when_previous_offset_would_skip(self, db):
        terms = self._terms(db, 8)
        now = datetime.now(timezone.utc)
        terms[0].last_polled_at = now - timedelta(hours=1)
        terms[1].last_polled_at = now - timedelta(hours=2)
        terms[2].last_polled_at = now - timedelta(days=5)
        terms[3].last_polled_at = now - timedelta(days=4)
        terms[4].last_polled_at = now - timedelta(days=3)
        terms[5].last_polled_at = now - timedelta(days=2)
        terms[6].last_polled_at = now - timedelta(hours=3)
        terms[7].last_polled_at = now - timedelta(hours=4)
        db.add(BackendEvent(
            kind="poll",
            status="completed",
            payload={"next_term_offset": 5},
        ))
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.poll_terms_per_run = 5
            selected, offset, next_offset = _poll_term_window(db, terms)

        assert [term.keyword for term in selected] == [
            "term-2",
            "term-3",
            "term-4",
            "term-5",
            "term-7",
        ]
        assert offset == 0
        assert next_offset == 0

    def test_zero_limit_processes_all_terms(self, db):
        terms = self._terms(db, 3)

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.poll_terms_per_run = 0
            selected, offset, next_offset = _poll_term_window(db, terms)

        assert selected == terms
        assert offset == 0
        assert next_offset == 0


class TestNotificationFreshnessWindow:
    def test_default_freshness_window_stays_short(self):
        assert app_settings.notification_freshness_window_minutes == 120
        assert _notification_freshness_window() == timedelta(hours=2)

    def test_uses_configured_freshness_window(self):
        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.notification_freshness_window_minutes = 120

            assert _notification_freshness_window() == timedelta(hours=2)

    def test_treats_recent_late_discovered_article_as_notification_eligible(self, db):
        observed_at = datetime(2026, 7, 9, 15, 0, tzinfo=timezone.utc)
        term = WatchTerm(
            keyword="Aiko",
            created_at=observed_at - timedelta(days=3),
        )
        source_item = SourceItem(
            id="oricon:fresh-rotation-item",
            platform="oricon",
            item_id="fresh-rotation-item",
            url="https://example.com/fresh",
            published_at=observed_at - timedelta(minutes=90),
            media_type="article",
            title="Aiko fresh article",
            raw_payload={"date_parsed": True},
        )
        db.add_all([term, source_item])
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.notification_freshness_window_minutes = 120

            assert _is_notification_eligible(
                term=term,
                source_item=source_item,
                observed_at=observed_at,
            ) is True

    def test_rejects_articles_outside_configured_freshness_window(self, db):
        observed_at = datetime(2026, 7, 9, 15, 0, tzinfo=timezone.utc)
        term = WatchTerm(
            keyword="Aiko",
            created_at=observed_at - timedelta(days=3),
        )
        source_item = SourceItem(
            id="oricon:stale-item",
            platform="oricon",
            item_id="stale-item",
            url="https://example.com/stale",
            published_at=observed_at - timedelta(hours=3),
            media_type="article",
            title="Aiko stale article",
            raw_payload={"date_parsed": True},
        )
        db.add_all([term, source_item])
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.notification_freshness_window_minutes = 120

            assert _is_notification_eligible(
                term=term,
                source_item=source_item,
                observed_at=observed_at,
            ) is False

    def test_rejects_articles_far_beyond_observed_clock_skew(self, db):
        observed_at = datetime(2026, 7, 9, 15, 0, tzinfo=timezone.utc)
        term = WatchTerm(
            keyword="Aiko",
            created_at=observed_at - timedelta(days=3),
        )
        source_item = SourceItem(
            id="oricon:future-item",
            platform="oricon",
            item_id="future-item",
            url="https://example.com/future",
            published_at=observed_at + timedelta(hours=6),
            media_type="article",
            title="Aiko future-dated article",
            raw_payload={"date_parsed": True},
        )
        db.add_all([term, source_item])
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.notification_freshness_window_minutes = 120

            assert _is_notification_eligible(
                term=term,
                source_item=source_item,
                observed_at=observed_at,
            ) is False

    def test_allows_articles_with_small_future_clock_skew(self, db):
        observed_at = datetime(2026, 7, 9, 15, 0, tzinfo=timezone.utc)
        term = WatchTerm(
            keyword="Aiko",
            created_at=observed_at - timedelta(days=3),
        )
        source_item = SourceItem(
            id="oricon:clock-skew-item",
            platform="oricon",
            item_id="clock-skew-item",
            url="https://example.com/clock-skew",
            published_at=observed_at + timedelta(minutes=3),
            media_type="article",
            title="Aiko near-future article",
            raw_payload={"date_parsed": True},
        )
        db.add_all([term, source_item])
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.notification_freshness_window_minutes = 120

            assert _is_notification_eligible(
                term=term,
                source_item=source_item,
                observed_at=observed_at,
            ) is True


class TestQueuePendingNotification:
    def test_does_not_queue_for_muted_term(self, db):
        term = WatchTerm(keyword="Aiko", notify_on_new=False, is_active=True)
        db.add(term)
        db.commit()

        _queue_pending_notification(
            db,
            term,
            [(False, datetime.now(timezone.utc), {"id": "note:new", "title": "Aiko note"})],
        )
        db.flush()

        assert db.get(PendingNotification, term.id) is None

    def test_clears_stale_pending_for_muted_term(self, db):
        term = WatchTerm(keyword="Aiko", notify_on_new=False, is_active=True)
        db.add(term)
        db.commit()
        db.add(PendingNotification(watch_term_id=term.id, new_count=1))
        db.commit()

        _queue_pending_notification(
            db,
            term,
            [(False, datetime.now(timezone.utc), {"id": "note:new", "title": "Aiko note"})],
        )
        db.flush()

        assert db.get(PendingNotification, term.id) is None


class TestDeliverPendingNotification:
    @pytest.mark.asyncio
    async def test_reloads_muted_term_before_delivery(self, db):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db.add(term)
        db.flush()
        db.add(PendingNotification(
            watch_term_id=term.id,
            new_count=1,
            preview_item={"title": "Aiko note"},
        ))
        db.commit()
        term_id = term.id
        assert term.notify_on_new is True

        ExternalSession = sessionmaker(bind=db.get_bind())
        external_db = ExternalSession()
        try:
            external_term = external_db.get(WatchTerm, term_id)
            external_term.notify_on_new = False
            external_db.commit()
        finally:
            external_db.close()

        with patch("app.ingestion.scheduler.send_new_match_notifications", new=AsyncMock()) as mock_send:
            delivered = await _deliver_pending_notification(db, term)

        assert delivered is True
        assert db.get(PendingNotification, term_id) is None
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_drops_stale_pending_notification_without_sending(self, db):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        stale_item = SourceItem(
            id="news:stale",
            platform="news",
            item_id="stale",
            url="https://example.com/stale",
            published_at=datetime.now(timezone.utc) - timedelta(hours=3),
            media_type="article",
            title="Aiko stale article",
            raw_payload={"date_parsed": True},
        )
        db.add_all([term, stale_item])
        db.flush()
        db.add_all([
            Match(watch_term_id=term.id, source_item_id=stale_item.id),
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "id": stale_item.id,
                    "title": stale_item.title,
                    "published_at": stale_item.published_at.isoformat(),
                },
            ),
        ])
        db.commit()
        term_id = term.id

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.notification_freshness_window_minutes = 120
            with patch("app.ingestion.scheduler.send_new_match_notifications", new=AsyncMock()) as mock_send:
                delivered = await _deliver_pending_notification(db, term)

        assert delivered is True
        assert db.get(PendingNotification, term_id) is None
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_drops_future_dated_pending_notification_without_sending(self, db):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        future_item = SourceItem(
            id="news:future",
            platform="news",
            item_id="future",
            url="https://example.com/future",
            published_at=datetime.now(timezone.utc) + timedelta(hours=6),
            media_type="article",
            title="Aiko future-dated article",
            raw_payload={"date_parsed": True},
        )
        db.add_all([term, future_item])
        db.flush()
        db.add_all([
            Match(watch_term_id=term.id, source_item_id=future_item.id),
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "id": future_item.id,
                    "title": future_item.title,
                    "published_at": future_item.published_at.isoformat(),
                },
            ),
        ])
        db.commit()
        term_id = term.id

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.notification_freshness_window_minutes = 120
            with patch("app.ingestion.scheduler.send_new_match_notifications", new=AsyncMock()) as mock_send:
                delivered = await _deliver_pending_notification(db, term)

        assert delivered is True
        assert db.get(PendingNotification, term_id) is None
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_fresh_item_appended_to_a_stale_pending_row(self, db):
        # Reproduces: an earlier item on this term was deferred (e.g. APNs
        # transiently unavailable), leaving a PendingNotification row with an
        # old created_at. A new discussion reply then gets appended to that
        # same row. Without a per-item queued_at, eligibility at delivery
        # time would be checked against the row's stale created_at instead
        # of when the reply actually arrived, and the fresh reply's
        # published_at would look implausibly "too new" and get dropped.
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=5),
        )
        now = datetime.now(timezone.utc)
        fresh_reply = SourceItem(
            id="girlschannel:reply1",
            platform="girlschannel",
            item_id="reply1",
            url="https://girlschannel.net/topics/reply1/",
            published_at=now - timedelta(minutes=1),
            media_type="text",
            title="Aiko discussion",
            raw_payload={"date_parsed": True},
        )
        db.add_all([term, fresh_reply])
        db.flush()
        db.add_all([
            Match(watch_term_id=term.id, source_item_id=fresh_reply.id),
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                # Row created hours ago for an earlier, unrelated deferred item.
                created_at=now - timedelta(hours=3),
                preview_item={
                    "items": [
                        {
                            "id": fresh_reply.id,
                            "url": fresh_reply.url,
                            "published_at": fresh_reply.published_at.isoformat(),
                            "notification_preview_source": "discussion_reply_update",
                            "queued_at": (now - timedelta(minutes=1)).isoformat(),
                        },
                    ],
                },
            ),
        ])
        db.commit()
        term_id = term.id

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.notification_freshness_window_minutes = 120
            mock_send = AsyncMock(return_value=True)
            with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_send):
                delivered = await _deliver_pending_notification(db, term)

        assert delivered is True
        mock_send.assert_called_once()
        assert db.get(PendingNotification, term_id) is None

    @pytest.mark.asyncio
    async def test_reloads_deactivated_term_before_delivery(self, db):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db.add(term)
        db.flush()
        db.add(PendingNotification(
            watch_term_id=term.id,
            new_count=1,
            preview_item={"title": "Aiko note"},
        ))
        db.commit()
        term_id = term.id
        assert term.is_active is True

        ExternalSession = sessionmaker(bind=db.get_bind())
        external_db = ExternalSession()
        try:
            external_term = external_db.get(WatchTerm, term_id)
            external_term.is_active = False
            external_db.commit()
        finally:
            external_db.close()

        with patch("app.ingestion.scheduler.send_new_match_notifications", new=AsyncMock()) as mock_send:
            delivered = await _deliver_pending_notification(db, term)

        assert delivered is True
        assert db.get(PendingNotification, term_id) is None
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_grouped_multi_item_delivery_sends_once_before_term_change(self, db):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        first = SourceItem(
            id="youtube:first",
            platform="youtube",
            item_id="first",
            url="https://example.com/first",
            published_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            media_type="video",
            title="Aiko first",
            raw_payload={"date_parsed": True},
        )
        second = SourceItem(
            id="youtube:second",
            platform="youtube",
            item_id="second",
            url="https://example.com/second",
            published_at=datetime.now(timezone.utc) - timedelta(minutes=9),
            media_type="video",
            title="Aiko second",
            raw_payload={"date_parsed": True},
        )
        db.add_all([term, first, second])
        db.flush()
        db.add_all([
            Match(watch_term_id=term.id, source_item_id=first.id),
            Match(watch_term_id=term.id, source_item_id=second.id),
            PendingNotification(
                watch_term_id=term.id,
                new_count=2,
                preview_item={
                    "items": [
                        {"id": first.id, "url": first.url, "published_at": first.published_at.isoformat()},
                        {"id": second.id, "url": second.url, "published_at": second.published_at.isoformat()},
                    ],
                },
            ),
        ])
        db.commit()
        term_id = term.id

        ExternalSession = sessionmaker(bind=db.get_bind())

        async def mute_after_first_send(*_args, **_kwargs):
            external_db = ExternalSession()
            try:
                external_term = external_db.get(WatchTerm, term_id)
                external_term.notify_on_new = False
                external_db.commit()
            finally:
                external_db.close()
            return True

        mock_send = AsyncMock(side_effect=mute_after_first_send)
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_send):
            delivered = await _deliver_pending_notification(db, term)

        assert delivered is True
        assert mock_send.call_count == 1
        assert mock_send.call_args.args[2] == 2
        assert mock_send.call_args.args[3]["id"] == second.id
        assert mock_send.call_args.kwargs["notification_item_ids"] == [first.id, second.id]
        assert db.get(PendingNotification, term_id) is None


class TestReportOrphanedNotificationTerms:
    def test_preserves_stale_owner_scoped_terms_without_device(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="missing-secret",
            created_at=old,
        )
        db.add(term)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            orphaned = _report_orphaned_notification_terms(db)

        db.refresh(term)
        assert orphaned == {term.id}
        assert term.notify_on_new is True

    def test_keeps_recent_owner_scoped_terms_during_grace_period(self, db):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="missing-secret",
            created_at=datetime.now(timezone.utc),
        )
        db.add(term)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            orphaned = _report_orphaned_notification_terms(db)

        db.refresh(term)
        assert orphaned == set()
        assert term.notify_on_new is True

    def test_keeps_terms_with_registered_owner_device(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        db.add(APNSDeviceToken(
            token="a" * 64,
            environment="production",
            device_secret="owner-secret",
            is_verified=True,
        ))
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="owner-secret",
            created_at=old,
        )
        db.add(term)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            mock_settings.apns_use_sandbox = False
            orphaned = _report_orphaned_notification_terms(db)

        db.refresh(term)
        assert orphaned == set()
        assert term.notify_on_new is True

    def test_keeps_term_with_verified_owner_device_from_another_environment(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        db.add(APNSDeviceToken(
            token="s" * 64,
            environment="sandbox",
            device_secret="owner-secret",
            is_verified=True,
        ))
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="owner-secret",
            created_at=old,
        )
        db.add(term)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            mock_settings.apns_use_sandbox = False
            orphaned = _report_orphaned_notification_terms(db)

        db.refresh(term)
        assert orphaned == set()
        assert term.notify_on_new is True

    def test_reports_term_with_unverified_owner_device(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        db.add(APNSDeviceToken(
            token="u" * 64,
            environment="production",
            device_secret="owner-secret",
            is_verified=False,
        ))
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="owner-secret",
            created_at=old,
        )
        db.add(term)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            orphaned = _report_orphaned_notification_terms(db)

        db.refresh(term)
        assert orphaned == {term.id}
        assert term.notify_on_new is True

    def test_reports_term_with_missing_created_at(self, db):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="missing-secret",
            created_at=None,
        )
        db.add(term)
        db.commit()
        db.execute(text("UPDATE watch_terms SET created_at = NULL WHERE id = :id"), {"id": term.id})
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            orphaned = _report_orphaned_notification_terms(db)

        db.refresh(term)
        assert orphaned == {term.id}
        assert term.notify_on_new is True


class TestReportOrphanedDuplicateTerms:
    def test_preserves_stale_duplicate_without_device_when_replacement_can_notify(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        stale = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        replacement = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="current-secret",
            created_at=old,
        )
        db.add_all([
            stale,
            replacement,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                device_secret="current-secret",
                is_verified=True,
            ),
        ])
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            mock_settings.apns_use_sandbox = False
            orphaned = _report_orphaned_duplicate_terms(db)

        db.refresh(stale)
        db.refresh(replacement)
        assert orphaned == {stale.id}
        assert stale.is_active is True
        assert replacement.is_active is True

    def test_keeps_unique_non_notifying_term_without_replacement(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        db.add(term)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            orphaned = _report_orphaned_duplicate_terms(db)

        db.refresh(term)
        assert orphaned == set()
        assert term.is_active is True

    def test_keeps_duplicate_when_owner_still_has_a_device(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        replacement = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="current-secret",
            created_at=old,
        )
        db.add_all([
            term,
            replacement,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                device_secret="old-secret",
                is_verified=True,
            ),
            APNSDeviceToken(
                token="b" * 64,
                environment="production",
                device_secret="current-secret",
                is_verified=True,
            ),
        ])
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            mock_settings.apns_use_sandbox = False
            orphaned = _report_orphaned_duplicate_terms(db)

        db.refresh(term)
        assert orphaned == set()
        assert term.is_active is True

    def test_reports_duplicate_when_replacement_has_verified_device_from_another_environment(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        stale = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        replacement = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="current-secret",
            created_at=old,
        )
        db.add_all([
            stale,
            replacement,
            APNSDeviceToken(
                token="s" * 64,
                environment="sandbox",
                device_secret="current-secret",
                is_verified=True,
            ),
        ])
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            mock_settings.apns_use_sandbox = False
            orphaned = _report_orphaned_duplicate_terms(db)

        db.refresh(stale)
        db.refresh(replacement)
        assert orphaned == {stale.id}
        assert stale.is_active is True
        assert replacement.is_active is True


class TestReportOrphanedSilentTerms:
    def test_preserves_stale_silent_owner_scoped_term_without_device(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        db.add(term)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            orphaned = _report_orphaned_silent_terms(db)

        db.refresh(term)
        assert orphaned == {term.id}
        assert term.is_active is True
        event = db.query(BackendEvent).filter_by(
            kind="notification_maintenance",
            status="orphaned_silent_terms_detected",
        ).one()
        assert event.payload["detected_count"] == 1
        assert event.payload["keywords"] == ["Aiko"]

    def test_keeps_recent_silent_owner_scoped_term_during_grace_period(self, db):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=datetime.now(timezone.utc),
        )
        db.add(term)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            orphaned = _report_orphaned_silent_terms(db)

        db.refresh(term)
        assert orphaned == set()
        assert term.is_active is True

    def test_keeps_silent_term_when_owner_still_has_a_device(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        db.add_all([
            term,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                device_secret="old-secret",
                is_verified=True,
            ),
        ])
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            orphaned = _report_orphaned_silent_terms(db)

        db.refresh(term)
        assert orphaned == set()
        assert term.is_active is True

    def test_reports_silent_term_with_unverified_owner_device(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        db.add(APNSDeviceToken(
            token="u" * 64,
            environment="production",
            device_secret="old-secret",
            is_verified=False,
        ))
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        db.add(term)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            orphaned = _report_orphaned_silent_terms(db)

        db.refresh(term)
        assert orphaned == {term.id}
        assert term.is_active is True

    def test_keeps_silent_term_with_verified_owner_device_from_another_environment(self, db):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        db.add(APNSDeviceToken(
            token="s" * 64,
            environment="sandbox",
            device_secret="old-secret",
            is_verified=True,
        ))
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        db.add(term)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            mock_settings.apns_use_sandbox = False
            orphaned = _report_orphaned_silent_terms(db)

        db.refresh(term)
        assert orphaned == set()
        assert term.is_active is True

    def test_reports_silent_term_with_missing_created_at(self, db):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=None,
        )
        db.add(term)
        db.commit()
        db.execute(text("UPDATE watch_terms SET created_at = NULL WHERE id = :id"), {"id": term.id})
        db.commit()

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.orphaned_notification_grace_minutes = 60
            orphaned = _report_orphaned_silent_terms(db)

        db.refresh(term)
        assert orphaned == {term.id}
        assert term.is_active is True

    def test_poll_skips_silent_term_with_unverified_owner_device(self, db, monkeypatch):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        db.add_all([
            term,
            APNSDeviceToken(
                token="u" * 64,
                environment="production",
                device_secret="old-secret",
                is_verified=False,
            ),
        ])
        db.commit()
        term_id = term.id

        async def _noop_revalidate(_db):
            return None

        def _unexpected_build_connectors(_db):
            raise AssertionError("orphaned term should be filtered before connector fetch")

        monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
        monkeypatch.setattr(scheduler, "revalidate_unverified_devices", _noop_revalidate)
        monkeypatch.setattr(scheduler, "_build_connectors", _unexpected_build_connectors)
        monkeypatch.setattr(scheduler.settings, "orphaned_notification_grace_minutes", 60)

        asyncio.run(scheduler._poll_once_unlocked())

        reloaded = db.get(WatchTerm, term_id)
        assert reloaded.is_active is True
        event = db.query(BackendEvent).filter_by(kind="poll", status="skipped").one()
        assert event.payload["terms"] == 0

    def test_poll_does_not_flush_pending_for_orphaned_term(self, db, monkeypatch):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        db.add_all([
            term,
            APNSDeviceToken(
                token="u" * 64,
                environment="production",
                device_secret="old-secret",
                is_verified=False,
            ),
        ])
        db.commit()
        db.add(PendingNotification(
            watch_term_id=term.id,
            new_count=1,
            preview_item={"id": "news:1", "title": "Aiko"},
        ))
        db.commit()
        term_id = term.id

        async def _noop_revalidate(_db):
            return None

        async def _unexpected_send(*_args, **_kwargs):
            raise AssertionError("orphaned pending notification should not be flushed")

        monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
        monkeypatch.setattr(scheduler, "revalidate_unverified_devices", _noop_revalidate)
        monkeypatch.setattr(scheduler, "_build_connectors", lambda _db: [])
        monkeypatch.setattr(scheduler, "send_new_match_notifications", _unexpected_send)
        monkeypatch.setattr(scheduler.settings, "orphaned_notification_grace_minutes", 60)

        asyncio.run(scheduler._poll_once_unlocked())

        assert db.get(PendingNotification, term_id) is not None
        event = db.query(BackendEvent).filter_by(kind="poll", status="skipped").one()
        assert event.payload["flushed_pending_terms"] == 0

    def test_normal_poll_does_not_final_flush_pending_for_orphaned_term(self, db, monkeypatch):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        orphaned = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=old,
        )
        active = WatchTerm(
            keyword="Miku",
            notify_on_new=False,
            is_active=True,
            created_at=old,
        )
        source_item = SourceItem(
            id="news:1",
            platform="news",
            item_id="1",
            url="https://example.com/1",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="Aiko update",
            raw_payload={"date_parsed": True},
        )
        db.add_all([
            orphaned,
            active,
            source_item,
            APNSDeviceToken(
                token="u" * 64,
                environment="production",
                device_secret="old-secret",
                is_verified=False,
            ),
        ])
        db.commit()
        db.add(PendingNotification(
            watch_term_id=orphaned.id,
            new_count=1,
            preview_item={"id": source_item.id, "title": source_item.title},
        ))
        db.commit()
        orphaned_id = orphaned.id
        send = AsyncMock(return_value=True)

        async def _noop_revalidate(_db):
            return None

        monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
        monkeypatch.setattr(scheduler, "revalidate_unverified_devices", _noop_revalidate)
        monkeypatch.setattr(scheduler, "_build_connectors", lambda _db: [])
        monkeypatch.setattr(scheduler, "send_new_match_notifications", send)
        monkeypatch.setattr(scheduler.settings, "orphaned_notification_grace_minutes", 60)

        asyncio.run(scheduler._poll_once_unlocked())

        assert db.get(PendingNotification, orphaned_id) is not None
        send.assert_not_awaited()
        event = db.query(BackendEvent).filter_by(kind="poll", status="completed").one()
        assert event.payload["terms"] == 1

    def test_poll_skips_silent_term_with_missing_created_at(self, db, monkeypatch):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret="old-secret",
            created_at=None,
        )
        db.add(term)
        db.commit()
        db.execute(text("UPDATE watch_terms SET created_at = NULL WHERE id = :id"), {"id": term.id})
        db.commit()
        term_id = term.id

        async def _noop_revalidate(_db):
            return None

        def _unexpected_build_connectors(_db):
            raise AssertionError("orphaned term should be filtered before connector fetch")

        monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
        monkeypatch.setattr(scheduler, "revalidate_unverified_devices", _noop_revalidate)
        monkeypatch.setattr(scheduler, "_build_connectors", _unexpected_build_connectors)
        monkeypatch.setattr(scheduler.settings, "orphaned_notification_grace_minutes", 60)

        asyncio.run(scheduler._poll_once_unlocked())

        reloaded = db.get(WatchTerm, term_id)
        assert reloaded.is_active is True
        event = db.query(BackendEvent).filter_by(kind="poll", status="skipped").one()
        assert event.payload["terms"] == 0


class TestPruneIrrelevantMatches:
    def test_removes_stale_match_and_orphan_item(self, db):
        term = WatchTerm(keyword="Aiko", aliases=[])
        db.add(term)
        db.flush()
        item = SourceItem(
            id="news:stale",
            platform="news",
            item_id="stale",
            url="https://example.com/stale",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="unrelated news",
            content_text="Aiko appears only in the old summary",
        )
        db.add(item)
        db.flush()
        db.add(Match(watch_term_id=term.id, source_item_id=item.id))
        db.commit()

        _prune_irrelevant_matches(db, [term])

        assert db.query(Match).count() == 0
        assert db.query(SourceItem).count() == 0

    def test_keeps_alias_and_text_only_matches(self, db):
        term = WatchTerm(keyword="Aiko", aliases=["相川愛子"])
        db.add(term)
        db.flush()
        alias_item = SourceItem(
            id="news:alias",
            platform="news",
            item_id="alias",
            url="https://example.com/alias",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="相川愛子の最新ニュース",
        )
        text_item = SourceItem(
            id="twitter:text",
            platform="twitter",
            item_id="text",
            url="https://example.com/text",
            published_at=datetime.now(timezone.utc),
            media_type="text",
            title=None,
            content_text="Aiko posted an update",
        )
        db.add_all([alias_item, text_item])
        db.flush()
        db.add_all([
            Match(watch_term_id=term.id, source_item_id=alias_item.id),
            Match(watch_term_id=term.id, source_item_id=text_item.id),
        ])
        db.commit()

        _prune_irrelevant_matches(db, [term])

        assert db.query(Match).count() == 2
        assert db.query(SourceItem).count() == 2


class TestPruneExceptionHandling:
    def test_prune_does_not_raise_on_db_error(self, db):
        """_prune_old_items must catch exceptions internally and rollback, not propagate."""
        # Provide a mock session whose execute() raises to trigger the except branch.
        broken_db = MagicMock()
        broken_db.execute.side_effect = RuntimeError("simulated DB failure")
        broken_db.rollback = MagicMock()

        # Should NOT raise — the exception is caught and logged internally.
        _prune_old_items(broken_db)

        broken_db.rollback.assert_called_once()


class TestBuildConnectors:
    def test_removed_togetter_source_is_not_registered(self, db):
        with patch("app.ingestion.scheduler.settings") as s:
            s.youtube_api_key = ""
            s.twitter_bearer_token = ""
            connectors = _build_connectors(db)

        registered = {connector.PLATFORM for connector in connectors}
        assert "togetter" not in registered

    def test_all_google_news_site_connectors_are_registered(self, db):
        with patch("app.ingestion.scheduler.settings") as s:
            s.youtube_api_key = ""
            s.twitter_bearer_token = ""
            connectors = _build_connectors(db)

        registered = {type(connector).PLATFORM for connector in connectors}
        connector_classes = {
            cls
            for name, cls in inspect.getmembers(news_sites, inspect.isclass)
            if issubclass(cls, news_sites._GNewsSiteConnector)
            and cls is not news_sites._GNewsSiteConnector
            and not name.startswith("_")
        }
        missing_platform = [cls.__name__ for cls in connector_classes if not cls.PLATFORM]
        assert not missing_platform

        expected = {cls.PLATFORM for cls in connector_classes}
        assert expected <= registered

    def test_uses_youtube_api_key_from_db_when_env_is_empty(self, db):
        cred = PlatformCredential(platform="youtube", api_key="db-yt-key")
        db.add(cred)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as s:
            s.youtube_api_key = ""
            s.twitter_bearer_token = ""
            connectors = _build_connectors(db)

        yt = next((c for c in connectors if isinstance(c, YouTubeConnector)), None)
        assert yt is not None
        assert yt.api_key == "db-yt-key"

    def test_uses_youtube_api_key_from_db_when_env_is_whitespace(self, db):
        cred = PlatformCredential(platform="youtube", api_key="db-yt-key")
        db.add(cred)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as s:
            s.youtube_api_key = "   "
            s.twitter_bearer_token = ""
            connectors = _build_connectors(db)

        yt = next((c for c in connectors if isinstance(c, YouTubeConnector)), None)
        assert yt is not None
        assert yt.api_key == "db-yt-key"

    def test_ignores_whitespace_youtube_api_key_from_db(self, db):
        cred = PlatformCredential(platform="youtube", api_key="\n  ")
        db.add(cred)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as s:
            s.youtube_api_key = ""
            s.twitter_bearer_token = ""
            connectors = _build_connectors(db)

        yt = next((c for c in connectors if isinstance(c, YouTubeConnector)), None)
        assert yt is not None
        assert yt.api_key == ""

    def test_uses_twitter_bearer_from_db_when_env_is_empty(self, db):
        cred = PlatformCredential(platform="twitter", bearer_token="db-tw-token")
        db.add(cred)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as s:
            s.youtube_api_key = ""
            s.twitter_bearer_token = ""
            connectors = _build_connectors(db)

        tw = next((c for c in connectors if isinstance(c, TwitterConnector)), None)
        assert tw is not None
        assert tw.bearer_token == "db-tw-token"

    def test_uses_twitter_bearer_from_db_when_env_is_whitespace(self, db):
        cred = PlatformCredential(platform="twitter", bearer_token="db-tw-token")
        db.add(cred)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as s:
            s.youtube_api_key = ""
            s.twitter_bearer_token = "   "
            connectors = _build_connectors(db)

        tw = next((c for c in connectors if isinstance(c, TwitterConnector)), None)
        assert tw is not None
        assert tw.bearer_token == "db-tw-token"

    def test_env_key_takes_precedence_over_db_for_youtube(self, db):
        cred = PlatformCredential(platform="youtube", api_key="db-yt-key")
        db.add(cred)
        db.commit()

        with patch("app.ingestion.scheduler.settings") as s:
            s.youtube_api_key = "env-yt-key"
            s.twitter_bearer_token = ""
            connectors = _build_connectors(db)

        yt = next((c for c in connectors if isinstance(c, YouTubeConnector)), None)
        assert yt is not None
        assert yt.api_key == "env-yt-key"

    def test_youtube_connector_always_present_without_key(self, db):
        with patch("app.ingestion.scheduler.settings") as s:
            s.youtube_api_key = ""
            s.twitter_bearer_token = ""
            connectors = _build_connectors(db)

        yt = next((c for c in connectors if isinstance(c, YouTubeConnector)), None)
        assert yt is not None
        assert yt.api_key == ""
