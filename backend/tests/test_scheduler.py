"""Tests for scheduler pruning logic and search-term building."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import inspect
from unittest.mock import AsyncMock, MagicMock

from app.database import Base
from unittest.mock import patch

from app.connectors import news_sites
from app.ingestion.scheduler import (
    _build_connectors,
    _connector_batches,
    _deactivate_orphaned_duplicate_terms,
    _deactivate_orphaned_silent_terms,
    _disable_orphaned_notification_terms,
    _fetch_one,
    _prune_irrelevant_matches,
    _prune_old_items,
    _poll_term_window,
    _search_terms_for,
)
from app.connectors.youtube import YouTubeConnector
from app.connectors.twitter import TwitterConnector
from app.models import APNSDeviceToken, BackendEvent, CollectionMode, Match, PlatformCredential, SourceItem, WatchTerm


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

    def test_prune_skips_community_platforms(self, db):
        term = WatchTerm(keyword="k3", aliases=[])
        db.add(term)
        db.commit()

        for platform in ("5ch", "girlschannel", "togetter"):
            _add_items(db, term, platform, 250)

        _prune_old_items(db)

        for platform in ("5ch", "girlschannel", "togetter"):
            count = (
                db.query(Match)
                .join(SourceItem, SourceItem.id == Match.source_item_id)
                .filter(SourceItem.platform == platform)
                .count()
            )
            assert count == 250, f"{platform} should not be pruned"

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


class TestFetchOne:
    def _connector(self, side_effect=None, return_value=None):
        c = MagicMock()
        c.PLATFORM = "mock"
        if side_effect is not None:
            c.fetch = AsyncMock(side_effect=side_effect)
        else:
            c.fetch = AsyncMock(return_value=return_value or [])
        return c

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
        assert next_offset == 5

    def test_rotates_from_previous_successful_poll(self, db):
        terms = self._terms(db, 8)
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
            "term-5",
            "term-6",
            "term-7",
            "term-0",
            "term-1",
        ]
        assert offset == 5
        assert next_offset == 2

    def test_zero_limit_processes_all_terms(self, db):
        terms = self._terms(db, 3)

        with patch("app.ingestion.scheduler.settings") as mock_settings:
            mock_settings.poll_terms_per_run = 0
            selected, offset, next_offset = _poll_term_window(db, terms)

        assert selected == terms
        assert offset == 0
        assert next_offset == 0


class TestDisableOrphanedNotificationTerms:
    def test_disables_stale_owner_scoped_terms_without_device(self, db):
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
            disabled = _disable_orphaned_notification_terms(db)

        db.refresh(term)
        assert disabled == 1
        assert term.notify_on_new is False

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
            disabled = _disable_orphaned_notification_terms(db)

        db.refresh(term)
        assert disabled == 0
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
            disabled = _disable_orphaned_notification_terms(db)

        db.refresh(term)
        assert disabled == 0
        assert term.notify_on_new is True


class TestDeactivateOrphanedDuplicateTerms:
    def test_deactivates_stale_duplicate_without_device_when_replacement_can_notify(self, db):
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
            deactivated = _deactivate_orphaned_duplicate_terms(db)

        db.refresh(stale)
        db.refresh(replacement)
        assert deactivated == 1
        assert stale.is_active is False
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
            deactivated = _deactivate_orphaned_duplicate_terms(db)

        db.refresh(term)
        assert deactivated == 0
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
            deactivated = _deactivate_orphaned_duplicate_terms(db)

        db.refresh(term)
        assert deactivated == 0
        assert term.is_active is True


class TestDeactivateOrphanedSilentTerms:
    def test_deactivates_stale_silent_owner_scoped_term_without_device(self, db):
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
            deactivated = _deactivate_orphaned_silent_terms(db)

        db.refresh(term)
        assert deactivated == 1
        assert term.is_active is False
        event = db.query(BackendEvent).filter_by(
            kind="notification_maintenance",
            status="deactivated_orphaned_silent_terms",
        ).one()
        assert event.payload["deactivated_count"] == 1
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
            deactivated = _deactivate_orphaned_silent_terms(db)

        db.refresh(term)
        assert deactivated == 0
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
            deactivated = _deactivate_orphaned_silent_terms(db)

        db.refresh(term)
        assert deactivated == 0
        assert term.is_active is True


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
    def test_all_google_news_site_connectors_are_registered(self, db):
        with patch("app.ingestion.scheduler.settings") as s:
            s.youtube_api_key = ""
            s.twitter_bearer_token = ""
            connectors = _build_connectors(db)

        registered = {type(connector).PLATFORM for connector in connectors}
        expected = {
            cls.PLATFORM
            for _, cls in inspect.getmembers(news_sites, inspect.isclass)
            if issubclass(cls, news_sites._GNewsSiteConnector)
            and cls is not news_sites._GNewsSiteConnector
        }
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
