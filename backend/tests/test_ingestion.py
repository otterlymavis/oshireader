"""Tests for the scheduler ingestion loop (_poll_once_unlocked)."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.orm import sessionmaker

from app.connectors.base import SourceItemCreate
from app.ingestion.scheduler import _poll_once_unlocked
from app.models import Match, SourceItem, WatchTerm


def _make_item(platform="youtube", item_id="vid1", **kwargs) -> SourceItemCreate:
    defaults = dict(
        url=f"https://{platform}.example.com/{item_id}",
        published_at=datetime.now(timezone.utc),
        media_type="video",
        title="Test Item",
    )
    defaults.update(kwargs)
    return SourceItemCreate(platform=platform, item_id=item_id, **defaults)


def _mock_connector(platform: str, items: list) -> MagicMock:
    c = MagicMock()
    c.PLATFORM = platform
    c.fetch = AsyncMock(return_value=items)
    return c


async def _run_poll(db_engine, connectors):
    """Run _poll_once_unlocked patched to use the test DB engine."""
    TestSession = sessionmaker(bind=db_engine)
    with patch("app.ingestion.scheduler._build_connectors", return_value=connectors), \
         patch("app.ingestion.scheduler.SessionLocal", TestSession), \
         patch("app.ingestion.scheduler.send_new_match_notifications", new=AsyncMock()):
        await _poll_once_unlocked()


class TestIngestionNewItems:
    @pytest.mark.asyncio
    async def test_new_item_creates_source_item(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        connector = _mock_connector("youtube", [_make_item(item_id="abc")])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        source = db_session.get(SourceItem, "youtube:abc")
        assert source is not None
        assert source.platform == "youtube"
        assert source.title == "Test Item"

    @pytest.mark.asyncio
    async def test_new_item_creates_match(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()
        term_id = term.id

        connector = _mock_connector("youtube", [_make_item(item_id="abc")])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        match = db_session.query(Match).filter(Match.watch_term_id == term_id).first()
        assert match is not None
        assert match.source_item_id == "youtube:abc"

    @pytest.mark.asyncio
    async def test_no_terms_nothing_inserted(self, db_engine, db_session):
        connector = _mock_connector("youtube", [_make_item()])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        assert db_session.query(SourceItem).count() == 0
        assert db_session.query(Match).count() == 0

    @pytest.mark.asyncio
    async def test_inactive_term_skipped(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko", is_active=False)
        db_session.add(term)
        db_session.commit()

        connector = _mock_connector("youtube", [_make_item()])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        assert db_session.query(SourceItem).count() == 0

    @pytest.mark.asyncio
    async def test_multiple_terms_each_get_matches(self, db_engine, db_session):
        term1 = WatchTerm(keyword="Aiko")
        term2 = WatchTerm(keyword="Haruka")
        db_session.add_all([term1, term2])
        db_session.commit()

        item = _make_item(item_id="shared")
        connector = _mock_connector("youtube", [item])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        matches = db_session.query(Match).all()
        assert len(matches) == 2


class TestIngestionNotifications:
    @pytest.mark.asyncio
    async def test_notify_called_when_notify_on_new_true(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()

        connector = _mock_connector("youtube", [_make_item(item_id="new1")])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)
        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        mock_notify.assert_called_once()
        _, called_term, called_count = mock_notify.call_args.args
        assert called_term.keyword == "Aiko"
        assert called_count == 1

    @pytest.mark.asyncio
    async def test_notify_called_for_any_new_items(self, db_engine, db_session):
        # Scheduler always delegates to send_new_match_notifications — the
        # notify_on_new guard lives inside that function, tested separately.
        term = WatchTerm(keyword="Aiko", notify_on_new=False)
        db_session.add(term)
        db_session.commit()

        connector = _mock_connector("youtube", [_make_item(item_id="new2")])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)
        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_notify_not_called_on_second_poll_same_items(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()

        item = _make_item(item_id="same1")
        connector = _mock_connector("youtube", [item])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()
            await _poll_once_unlocked()  # second poll — same item, no new matches

        assert mock_notify.call_count == 1  # only the first poll triggers notify


class TestIngestionIdempotency:
    @pytest.mark.asyncio
    async def test_second_poll_no_duplicate_source_items(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        item = _make_item(item_id="abc")
        connector = _mock_connector("youtube", [item])

        await _run_poll(db_engine, [connector])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        assert db_session.query(SourceItem).count() == 1

    @pytest.mark.asyncio
    async def test_second_poll_no_duplicate_matches(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        item = _make_item(item_id="abc")
        connector = _mock_connector("youtube", [item])

        await _run_poll(db_engine, [connector])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        assert db_session.query(Match).count() == 1


class TestIngestionCollectionMode:
    @pytest.mark.asyncio
    async def test_collection_mode_forwarded_to_connector(self, db_engine, db_session):
        """Poll must pass WatchTerm.collection_mode to every connector fetch call."""
        term = WatchTerm(keyword="Aiko", collection_mode="media_only")
        db_session.add(term)
        db_session.commit()

        connector = _mock_connector("youtube", [])
        await _run_poll(db_engine, [connector])

        connector.fetch.assert_called_once()
        _, called_mode = connector.fetch.call_args.args
        assert called_mode == "media_only"

    @pytest.mark.asyncio
    async def test_all_info_mode_forwarded_to_connector(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko", collection_mode="all_info")
        db_session.add(term)
        db_session.commit()

        connector = _mock_connector("youtube", [])
        await _run_poll(db_engine, [connector])

        _, called_mode = connector.fetch.call_args.args
        assert called_mode == "all_info"


class TestIngestionDateHealing:
    @pytest.mark.asyncio
    async def test_heals_stale_published_at(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        # First poll: item has a "now-ish" placeholder date (bad)
        bad_date = datetime.now(timezone.utc)
        item_bad = _make_item(item_id="vid1", published_at=bad_date)
        connector = _mock_connector("youtube", [item_bad])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        stored_before = db_session.get(SourceItem, "youtube:vid1")
        assert stored_before is not None

        # Second poll: connector now returns a real date >5min old and >5min different
        real_date = datetime.now(timezone.utc) - timedelta(hours=3)
        item_real = _make_item(item_id="vid1", published_at=real_date)
        connector2 = _mock_connector("youtube", [item_real])
        await _run_poll(db_engine, [connector2])

        db_session.expire_all()
        stored_after = db_session.get(SourceItem, "youtube:vid1")
        assert stored_after is not None
        stored_dt = stored_after.published_at
        if stored_dt.tzinfo is None:
            stored_dt = stored_dt.replace(tzinfo=timezone.utc)
        # Should have been updated to the real date (within a few seconds)
        diff = abs((stored_dt - real_date).total_seconds())
        assert diff < 10, f"published_at not healed: stored={stored_dt}, expected≈{real_date}"
