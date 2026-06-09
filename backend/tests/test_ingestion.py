"""Tests for the scheduler ingestion loop (_poll_once_unlocked)."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.orm import sessionmaker

from app.connectors.base import SourceItemCreate
import asyncio

from app.ingestion.scheduler import _poll_lock, _poll_once_unlocked, poll_once, _prune_old_items
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


class TestIngestionConnectorErrorIsolation:
    @pytest.mark.asyncio
    async def test_failing_connector_does_not_block_others(self, db_engine, db_session):
        """A connector that raises must not prevent other connectors from running."""
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        bad_connector = MagicMock()
        bad_connector.PLATFORM = "bad"
        bad_connector.fetch = AsyncMock(side_effect=RuntimeError("network failed"))

        good_item = _make_item(platform="youtube", item_id="ok1")
        good_connector = _mock_connector("youtube", [good_item])

        await _run_poll(db_engine, [bad_connector, good_connector])

        db_session.expire_all()
        source = db_session.get(SourceItem, "youtube:ok1")
        assert source is not None, "Good connector's item should still be ingested"

    @pytest.mark.asyncio
    async def test_all_connectors_fail_leaves_db_empty(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        bad = MagicMock()
        bad.PLATFORM = "bad"
        bad.fetch = AsyncMock(side_effect=RuntimeError("timeout"))

        await _run_poll(db_engine, [bad])

        db_session.expire_all()
        assert db_session.query(SourceItem).count() == 0

    @pytest.mark.asyncio
    async def test_db_write_error_is_caught_and_rolled_back(self, db_engine, db_session):
        """The inner try/except in the poll loop must catch DB errors and rollback without crashing."""
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        good_item = _make_item(platform="youtube", item_id="err1")
        connector = _mock_connector("youtube", [good_item])

        # Patch db.commit to raise — it's called after item + match are flushed.
        # This triggers the except block (lines 211-220) which must rollback and not propagate.
        TestSession = sessionmaker(bind=db_engine)
        from sqlalchemy.orm import Session as _Session
        original_commit = _Session.commit
        commit_calls = {"count": 0}

        def raising_commit(self):
            commit_calls["count"] += 1
            if commit_calls["count"] == 1:
                raise RuntimeError("simulated commit error")
            return original_commit(self)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=AsyncMock()), \
             patch.object(_Session, "commit", raising_commit):
            # Must NOT raise — exception handler swallows the error and calls rollback.
            await _poll_once_unlocked()

        db_session.expire_all()
        # The failed commit should have rolled back — no match is committed.
        assert db_session.query(Match).count() == 0, "Rolled-back match must not be committed"


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


class TestIngestionCollectionModeNullFallback:
    @pytest.mark.asyncio
    async def test_null_collection_mode_defaults_to_all_info(self, db_engine, db_session):
        """WatchTerm with collection_mode=None must not crash; falls back to all_info."""
        term = WatchTerm(keyword="Aiko")
        term.collection_mode = None  # Simulate a legacy/corrupt DB row
        db_session.add(term)
        db_session.commit()

        connector = _mock_connector("youtube", [])
        await _run_poll(db_engine, [connector])

        connector.fetch.assert_called_once()
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

    @pytest.mark.asyncio
    async def test_does_not_heal_when_dates_are_similar(self, db_engine, db_session):
        """If new date is within 5 min of stored date, don't update (not a real correction)."""
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        original_date = datetime.now(timezone.utc) - timedelta(hours=2)
        item = _make_item(item_id="vid2", published_at=original_date)
        connector = _mock_connector("youtube", [item])
        await _run_poll(db_engine, [connector])

        # Second poll: date differs by only 2 minutes — should NOT trigger healing
        slightly_different = original_date + timedelta(minutes=2)
        item2 = _make_item(item_id="vid2", published_at=slightly_different)
        connector2 = _mock_connector("youtube", [item2])
        await _run_poll(db_engine, [connector2])

        db_session.expire_all()
        stored = db_session.get(SourceItem, "youtube:vid2")
        stored_dt = stored.published_at
        if stored_dt.tzinfo is None:
            stored_dt = stored_dt.replace(tzinfo=timezone.utc)
        diff = abs((stored_dt - original_date).total_seconds())
        assert diff < 60, "Date within 5-min threshold should not be healed"

    @pytest.mark.asyncio
    async def test_does_not_heal_recent_date(self, db_engine, db_session):
        """A new date that is itself very recent (< 5 min old) should not be used to heal."""
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        old_stored = datetime.now(timezone.utc) - timedelta(hours=1)
        item = _make_item(item_id="vid3", published_at=old_stored)
        connector = _mock_connector("youtube", [item])
        await _run_poll(db_engine, [connector])

        # Second poll: connector returns a date that's only 1 minute old — looks like a fallback
        fresh_fallback = datetime.now(timezone.utc) - timedelta(minutes=1)
        item2 = _make_item(item_id="vid3", published_at=fresh_fallback)
        connector2 = _mock_connector("youtube", [item2])
        await _run_poll(db_engine, [connector2])

        db_session.expire_all()
        stored = db_session.get(SourceItem, "youtube:vid3")
        stored_dt = stored.published_at
        if stored_dt.tzinfo is None:
            stored_dt = stored_dt.replace(tzinfo=timezone.utc)
        diff = abs((stored_dt - old_stored).total_seconds())
        assert diff < 60, "Recent new date (< 5 min old) should not overwrite a valid stored date"


class TestIngestionPruning:
    def _seed_items(self, db_session, term, platform, count, prefix="item"):
        """Insert `count` SourceItems + Matches, oldest last (highest index = oldest date)."""
        base = datetime.now(timezone.utc)
        sids = []
        for i in range(count):
            item_id = f"{prefix}{i:04d}"
            sid = f"{platform}:{item_id}"
            sids.append(sid)
            db_session.add(SourceItem(
                id=sid,
                platform=platform,
                item_id=item_id,
                url=f"https://{platform}.example.com/{item_id}",
                published_at=base - timedelta(hours=i),
                media_type="video",
                title=f"Item {i}",
            ))
        db_session.flush()  # ensure source_items exist before FK-constrained matches
        for sid in sids:
            db_session.add(Match(watch_term_id=term.id, source_item_id=sid))
        db_session.commit()

    def test_prunes_oldest_when_over_limit(self, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        self._seed_items(db_session, term, "youtube", 205)
        _prune_old_items(db_session)

        db_session.expire_all()
        assert db_session.query(Match).count() == 200
        assert db_session.query(SourceItem).count() == 200

    def test_does_not_prune_at_or_below_limit(self, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        self._seed_items(db_session, term, "youtube", 200)
        _prune_old_items(db_session)

        db_session.expire_all()
        assert db_session.query(Match).count() == 200

    def test_skip_platforms_never_pruned(self, db_session):
        """5ch, girlschannel, togetter must be skipped regardless of count."""
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        for platform in ("5ch", "girlschannel", "togetter"):
            self._seed_items(db_session, term, platform, 205)

        total_before = db_session.query(Match).count()
        _prune_old_items(db_session)

        db_session.expire_all()
        assert db_session.query(Match).count() == total_before

    def test_oldest_items_are_removed_not_newest(self, db_session):
        """After pruning, the 5 oldest items must be gone; the 200 newest must survive."""
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        self._seed_items(db_session, term, "youtube", 205)
        _prune_old_items(db_session)

        db_session.expire_all()
        # item0000 is the newest (base - 0h), item0204 is the oldest (base - 204h)
        assert db_session.get(SourceItem, "youtube:item0000") is not None
        assert db_session.get(SourceItem, "youtube:item0204") is None

    def test_pruning_two_terms_independently(self, db_session):
        """Each (platform, watch_term) pair is pruned independently."""
        term1 = WatchTerm(keyword="Aiko")
        term2 = WatchTerm(keyword="Haruka")
        db_session.add_all([term1, term2])
        db_session.commit()

        self._seed_items(db_session, term1, "youtube", 205, prefix="t1item")
        self._seed_items(db_session, term2, "youtube", 205, prefix="t2item")
        _prune_old_items(db_session)

        db_session.expire_all()
        term1_count = (
            db_session.query(Match)
            .filter(Match.watch_term_id == term1.id)
            .count()
        )
        term2_count = (
            db_session.query(Match)
            .filter(Match.watch_term_id == term2.id)
            .count()
        )
        assert term1_count == 200
        assert term2_count == 200


class TestPollOnceLocking:
    @pytest.mark.asyncio
    async def test_poll_once_skips_when_lock_is_held(self):
        """If _poll_lock is already locked, poll_once must return without running the poll."""
        async with _poll_lock:
            # Lock is now held; poll_once should detect this and return early.
            with patch("app.ingestion.scheduler._poll_once_unlocked", new=AsyncMock()) as mock_poll:
                await poll_once()
            mock_poll.assert_not_called()
