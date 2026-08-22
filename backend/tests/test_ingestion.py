"""Tests for the scheduler ingestion loop (_poll_once_unlocked)."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import sessionmaker

from app.connectors.base import SourceItemCreate
from app.config import settings
import asyncio

from app.ingestion.scheduler import (
    _deliver_pending_notification,
    _poll_lock,
    _poll_once_unlocked,
    poll_once,
    _prune_old_items,
)
from app.models import (
    APNSDeviceToken,
    BackendEvent,
    DeviceEntitlement,
    Match,
    PendingNotification,
    SourceItem,
    WatchTerm,
)


def _make_item(platform="youtube", item_id="vid1", **kwargs) -> SourceItemCreate:
    defaults = dict(
        url=f"https://{platform}.example.com/{item_id}",
        published_at=datetime.now(timezone.utc),
        media_type="video",
        title="Aiko Haruka Test Item",
        content_text="Aiko Haruka",
        raw_payload={"date_parsed": True},
    )
    defaults.update(kwargs)
    return SourceItemCreate(platform=platform, item_id=item_id, **defaults)


def _mock_connector(platform: str, items: list) -> MagicMock:
    c = MagicMock()
    c.PLATFORM = platform
    c.MIN_FETCH_TIMEOUT_SECONDS = None
    c.fetch = AsyncMock(return_value=items)
    return c


def _active_push_entitlement(owner_device_secret: str) -> DeviceEntitlement:
    return DeviceEntitlement(
        owner_device_secret=owner_device_secret,
        product_id="configured.push.product",
        original_transaction_id=f"original-{owner_device_secret}",
        latest_transaction_id=f"latest-{owner_device_secret}",
        purchase_date=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        push_term_limit=10,
    )


async def _run_poll(db_engine, connectors):
    """Run ingestion against the test DB without production refresh cadence."""
    TestSession = sessionmaker(bind=db_engine)
    with patch("app.ingestion.scheduler._build_connectors", return_value=connectors), \
         patch("app.ingestion.scheduler.SessionLocal", TestSession), \
         patch("app.ingestion.scheduler.send_new_match_notifications", new=AsyncMock()), \
         patch("app.ingestion.scheduler._term_is_due", return_value=True), \
         patch.object(settings, "poll_terms_per_run", 0):
        await _poll_once_unlocked()


class TestIngestionNewItems:
    @pytest.mark.asyncio
    async def test_persists_last_polled_at_and_skips_immediate_second_poll(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()
        term_id = term.id

        connector = _mock_connector("youtube", [])
        TestSession = sessionmaker(bind=db_engine)
        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch.object(settings, "poll_terms_per_run", 0):
            await _poll_once_unlocked()
            await _poll_once_unlocked()

        persisted = db_session.get(WatchTerm, term_id)
        db_session.refresh(persisted)
        assert persisted.last_polled_at is not None
        connector.fetch.assert_awaited_once()

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
        assert source.title == "Aiko Haruka Test Item"

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

    @pytest.mark.asyncio
    async def test_duplicate_search_terms_share_connector_fetch(self, db_engine, db_session):
        term1 = WatchTerm(keyword="Aiko")
        term2 = WatchTerm(keyword="Aiko")
        db_session.add_all([term1, term2])
        db_session.commit()
        term_ids = {term1.id, term2.id}

        connector = _mock_connector("youtube", [_make_item(item_id="shared-cache")])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        connector.fetch.assert_awaited_once()
        matches = (
            db_session.query(Match)
            .filter(Match.source_item_id == "youtube:shared-cache")
            .all()
        )
        assert {match.watch_term_id for match in matches} == term_ids

    @pytest.mark.asyncio
    async def test_duplicate_search_terms_share_empty_connector_fetch(self, db_engine, db_session):
        term1 = WatchTerm(keyword="Aiko")
        term2 = WatchTerm(keyword="Aiko")
        db_session.add_all([term1, term2])
        db_session.commit()

        connector = _mock_connector("youtube", [])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        connector.fetch.assert_awaited_once()
        assert db_session.query(Match).count() == 0

    @pytest.mark.asyncio
    async def test_duplicate_search_terms_retry_after_failed_fetch(self, db_engine, db_session):
        term1 = WatchTerm(keyword="Aiko")
        term2 = WatchTerm(keyword="Aiko")
        db_session.add_all([term1, term2])
        db_session.commit()

        connector = _mock_connector("youtube", [])
        connector.fetch = AsyncMock(side_effect=RuntimeError("network failed"))
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        assert connector.fetch.await_count == 2
        assert db_session.query(Match).count() == 0

    @pytest.mark.asyncio
    async def test_item_without_search_term_is_not_ingested(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        item = _make_item(item_id="miss", content_text="unrelated", title="Other Item")
        connector = _mock_connector("youtube", [item])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        assert db_session.get(SourceItem, "youtube:miss") is None
        assert db_session.query(Match).count() == 0

    @pytest.mark.asyncio
    async def test_item_matching_alias_is_ingested_for_watch_term(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko", aliases=["Haruka"])
        db_session.add(term)
        db_session.commit()

        item = _make_item(item_id="alias-hit", title="Haruka interview")
        connector = _mock_connector("youtube", [item])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        source = db_session.get(SourceItem, "youtube:alias-hit")
        match = db_session.query(Match).filter(Match.watch_term_id == term.id).first()
        assert source is not None
        assert match is not None
        assert match.source_item_id == "youtube:alias-hit"


class TestIngestionNotifications:
    @pytest.mark.asyncio
    async def test_notify_called_when_notify_on_new_true(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()
        term_id = term.id

        connector = _mock_connector("youtube", [_make_item(item_id="new1")])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)
        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify), \
             patch.object(settings, "admin_api_token", "redirect-test-secret"):
            await _poll_once_unlocked()

        mock_notify.assert_called_once()
        _, called_term, called_count, preview_item = mock_notify.call_args.args
        assert sa_inspect(called_term).identity == (term_id,)
        assert called_count == 1
        assert preview_item["title"] == "Aiko Haruka Test Item"
        assert preview_item["match_id"]
        assert preview_item["media_type"] == "video"
        assert preview_item["published_at"]
        assert f"/api/feed/matches/{preview_item['match_id']}/redirect?" in preview_item["redirect_url"]
        assert "signature=" in preview_item["redirect_url"]

    @pytest.mark.asyncio
    async def test_multiple_new_items_each_send_their_own_notification(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()

        connector = _mock_connector(
            "youtube",
            [
                _make_item(item_id="new1", title="Aiko first item"),
                _make_item(item_id="new2", title="Aiko second item"),
            ],
        )
        mock_notify = AsyncMock(return_value=True)
        TestSession = sessionmaker(bind=db_engine)
        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        assert mock_notify.call_count == 2
        sent_ids = set()
        for call in mock_notify.call_args_list:
            _, _, called_count, preview_item = call.args
            assert called_count == 1
            sent_ids.add(preview_item["id"])
        assert sent_ids == {"youtube:new1", "youtube:new2"}

    @pytest.mark.asyncio
    async def test_same_keyword_owner_duplicate_notified_from_global_poll_slot(
        self,
        db_engine,
        db_session,
    ):
        global_term = WatchTerm(keyword="Aiko", notify_on_new=True)
        owner_term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            owner_device_secret="owner-secret",
        )
        db_session.add_all([
            global_term,
            owner_term,
            _active_push_entitlement("owner-secret"),
            APNSDeviceToken(
                token="owner-token",
                environment="production",
                device_secret="owner-secret",
                is_verified=True,
            ),
        ])
        db_session.commit()
        global_term_id = global_term.id
        owner_term_id = owner_term.id

        connector = _mock_connector("youtube", [_make_item(item_id="shared-fresh")])
        notified_term_ids: list[int] = []

        async def fake_notify(_db, notified_term, _count, _preview_item, **_kwargs):
            notified_term_ids.append(notified_term.id)
            return True

        TestSession = sessionmaker(bind=db_engine)
        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=fake_notify), \
             patch.object(settings, "poll_terms_per_run", 1):
            await _poll_once_unlocked()

        assert set(notified_term_ids) == {global_term_id, owner_term_id}

        db_session.expire_all()
        owner_match = (
            db_session.query(Match)
            .filter(
                Match.watch_term_id == owner_term_id,
                Match.source_item_id == "youtube:shared-fresh",
            )
            .first()
        )
        assert owner_match is not None

    @pytest.mark.asyncio
    async def test_notification_excludes_stale_backlog_and_previews_fresh_item(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db_session.add(term)
        db_session.commit()
        term_id = term.id

        newest = datetime.now(timezone.utc)
        older = newest - timedelta(days=3)
        fresh_connector = _mock_connector(
            "youtube",
            [_make_item(platform="youtube", item_id="fresh", published_at=newest, title="Aiko fresh")],
        )
        old_connector = _mock_connector(
            "news",
            [_make_item(platform="news", item_id="old", published_at=older, title="Aiko old")],
        )
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch(
            "app.ingestion.scheduler._build_connectors",
            return_value=[fresh_connector, old_connector],
        ), patch(
            "app.ingestion.scheduler.SessionLocal",
            TestSession,
        ), patch(
            "app.ingestion.scheduler.send_new_match_notifications",
            new=mock_notify,
        ):
            await _poll_once_unlocked()

        mock_notify.assert_called_once()
        _, called_term, called_count, preview_item = mock_notify.call_args.args
        assert sa_inspect(called_term).identity == (term_id,)
        assert called_count == 1
        assert preview_item["id"] == "youtube:fresh"
        assert preview_item["title"] == "Aiko fresh"

    @pytest.mark.asyncio
    async def test_match_older_than_watch_term_is_added_without_notification(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()
        term_id = term.id

        historical = _make_item(
            item_id="historical",
            published_at=datetime.now(timezone.utc) - timedelta(hours=2),
            title="Aiko historical post",
        )
        connector = _mock_connector("youtube", [historical])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        db_session.expire_all()
        match = db_session.query(Match).filter(Match.watch_term_id == term_id).one()
        assert match.source_item_id == "youtube:historical"
        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_twenty_five_hour_old_discovery_is_added_without_notification(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
        db_session.add(term)
        db_session.commit()
        term_id = term.id

        stale_discovery = _make_item(
            item_id="twenty-five-hours-old",
            published_at=datetime.now(timezone.utc) - timedelta(hours=25),
            title="Aiko stale discovery",
        )
        connector = _mock_connector("youtube", [stale_discovery])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        db_session.expire_all()
        match = db_session.query(Match).filter(Match.watch_term_id == term_id).one()
        assert match.source_item_id == "youtube:twenty-five-hours-old"
        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_recent_late_discovery_for_established_term_notifies(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        existing = SourceItem(
            id="youtube:existing",
            platform="youtube",
            item_id="existing",
            url="https://youtube.example.com/existing",
            published_at=datetime.now(timezone.utc) - timedelta(days=2),
            media_type="video",
            title="Aiko existing post",
            content_text="Aiko existing post",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, existing])
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=existing.id))
        db_session.commit()

        late_new_discovery = _make_item(
            platform="youtube",
            item_id="same-day-but-new-to-feed",
            published_at=datetime.now(timezone.utc) - timedelta(minutes=90),
            title="Aiko same-day but newly discovered",
            content_text="Aiko same-day but newly discovered",
            raw_payload={"date_parsed": True},
        )
        connector = _mock_connector("youtube", [late_new_discovery])
        mock_notify = AsyncMock(return_value=True)
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        db_session.expire_all()
        match = (
            db_session.query(Match)
            .filter(Match.watch_term_id == term.id)
            .filter(Match.source_item_id == "youtube:same-day-but-new-to-feed")
            .one()
        )
        assert match is not None
        mock_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recent_unnotified_existing_match_gets_catchup_notification(
        self,
        db_engine,
        db_session,
    ):
        now = datetime.now(timezone.utc)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=now - timedelta(days=14),
        )
        item = SourceItem(
            id="yahoonews:late-existing",
            platform="yahoonews",
            item_id="late-existing",
            url="https://news.example.com/late-existing",
            published_at=now - timedelta(minutes=90),
            media_type="article",
            title="Aiko late existing result",
            content_text="Aiko late existing result",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, item])
        db_session.flush()
        db_session.add(Match(
            watch_term_id=term.id,
            source_item_id=item.id,
            created_at=now - timedelta(hours=1),
        ))
        db_session.commit()
        mock_notify = AsyncMock(return_value=True)
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify), \
             patch.object(settings, "poll_terms_per_run", 0):
            await _poll_once_unlocked()

        mock_notify.assert_awaited_once()
        preview = mock_notify.await_args.args[3]
        assert preview["id"] == "yahoonews:late-existing"

    @pytest.mark.asyncio
    async def test_recent_existing_match_with_delivered_apns_is_not_caught_up_again(
        self,
        db_engine,
        db_session,
    ):
        now = datetime.now(timezone.utc)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=now - timedelta(days=14),
        )
        item = SourceItem(
            id="yahoonews:already-delivered",
            platform="yahoonews",
            item_id="already-delivered",
            url="https://news.example.com/already-delivered",
            published_at=now - timedelta(hours=12),
            media_type="article",
            title="Aiko already delivered result",
            content_text="Aiko already delivered result",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, item])
        db_session.flush()
        db_session.add_all([
            Match(
                watch_term_id=term.id,
                source_item_id=item.id,
                created_at=now - timedelta(hours=1),
            ),
            BackendEvent(
                kind="apns",
                status="attempted",
                payload={
                    "term_id": term.id,
                    "keyword": term.keyword,
                    "preview_item_id": item.id,
                    "delivered_count": 1,
                },
            ),
        ])
        db_session.commit()
        mock_notify = AsyncMock(return_value=True)
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify), \
             patch.object(settings, "poll_terms_per_run", 0):
            await _poll_once_unlocked()

        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_recent_existing_grouped_match_with_delivered_apns_is_not_caught_up_again(
        self,
        db_engine,
        db_session,
    ):
        now = datetime.now(timezone.utc)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=now - timedelta(days=14),
        )
        preview = SourceItem(
            id="yahoonews:grouped-preview",
            platform="yahoonews",
            item_id="grouped-preview",
            url="https://news.example.com/grouped-preview",
            published_at=now - timedelta(hours=11),
            media_type="article",
            title="Aiko grouped preview result",
            content_text="Aiko grouped preview result",
            raw_payload={"date_parsed": True},
        )
        second = SourceItem(
            id="yahoonews:grouped-second",
            platform="yahoonews",
            item_id="grouped-second",
            url="https://news.example.com/grouped-second",
            published_at=now - timedelta(hours=12),
            media_type="article",
            title="Aiko grouped second result",
            content_text="Aiko grouped second result",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, preview, second])
        db_session.flush()
        db_session.add_all([
            Match(
                watch_term_id=term.id,
                source_item_id=preview.id,
                created_at=now - timedelta(hours=1),
            ),
            Match(
                watch_term_id=term.id,
                source_item_id=second.id,
                created_at=now - timedelta(hours=1),
            ),
            BackendEvent(
                kind="apns",
                status="attempted",
                payload={
                    "term_id": term.id,
                    "keyword": term.keyword,
                    "preview_item_id": preview.id,
                    "notification_item_ids": [preview.id, second.id],
                    "delivered_count": 1,
                },
            ),
        ])
        db_session.commit()
        mock_notify = AsyncMock(return_value=True)
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify), \
             patch.object(settings, "poll_terms_per_run", 0):
            await _poll_once_unlocked()

        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_recent_existing_grouped_match_for_other_term_is_still_caught_up(
        self,
        db_engine,
        db_session,
    ):
        now = datetime.now(timezone.utc)
        notified_term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=now - timedelta(days=14),
        )
        catchup_term = WatchTerm(
            keyword="Haruka",
            notify_on_new=True,
            created_at=now - timedelta(days=14),
        )
        shared = SourceItem(
            id="yahoonews:shared-grouped",
            platform="yahoonews",
            item_id="shared-grouped",
            url="https://news.example.com/shared-grouped",
            published_at=now - timedelta(minutes=90),
            media_type="article",
            title="Aiko Haruka shared result",
            content_text="Aiko Haruka shared result",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([notified_term, catchup_term, shared])
        db_session.flush()
        db_session.add_all([
            Match(
                watch_term_id=catchup_term.id,
                source_item_id=shared.id,
                created_at=now - timedelta(hours=1),
            ),
            BackendEvent(
                kind="apns",
                status="attempted",
                payload={
                    "term_id": notified_term.id,
                    "keyword": notified_term.keyword,
                    "preview_item_id": shared.id,
                    "notification_item_ids": [shared.id],
                    "delivered_count": 1,
                },
            ),
        ])
        db_session.commit()
        catchup_term_id = catchup_term.id
        mock_notify = AsyncMock(return_value=True)
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify), \
             patch.object(settings, "poll_terms_per_run", 0):
            await _poll_once_unlocked()

        mock_notify.assert_awaited_once()
        _, called_term, called_count, preview_item = mock_notify.await_args.args
        assert sa_inspect(called_term).identity == (catchup_term_id,)
        assert called_count == 1
        assert preview_item["id"] == shared.id

    @pytest.mark.asyncio
    async def test_catchup_sends_a_notification_per_item_oldest_first(
        self,
        db_engine,
        db_session,
    ):
        now = datetime.now(timezone.utc)
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=now - timedelta(days=14),
        )
        discussion = SourceItem(
            id="5ch:recent-thread",
            platform="5ch",
            item_id="recent-thread",
            url="https://5ch.example.com/recent-thread",
            published_at=now - timedelta(hours=1),
            media_type="article",
            title="Aiko recent thread",
            content_text="Aiko recent thread",
            raw_payload={"date_parsed": True},
        )
        article = SourceItem(
            id="yahoonews:recent-article",
            platform="yahoonews",
            item_id="recent-article",
            url="https://news.example.com/recent-article",
            published_at=now - timedelta(minutes=90),
            media_type="article",
            title="Aiko recent article",
            content_text="Aiko recent article",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, discussion, article])
        db_session.flush()
        db_session.add_all([
            Match(
                watch_term_id=term.id,
                source_item_id=discussion.id,
                created_at=now - timedelta(hours=1),
            ),
            Match(
                watch_term_id=term.id,
                source_item_id=article.id,
                created_at=now - timedelta(minutes=50),
            ),
        ])
        db_session.commit()
        mock_notify = AsyncMock(return_value=True)
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify), \
             patch.object(settings, "poll_terms_per_run", 0):
            await _poll_once_unlocked()

        assert mock_notify.call_count == 2
        # Oldest published item first, regardless of platform.
        first_call, second_call = mock_notify.call_args_list
        assert first_call.args[2] == 1
        assert first_call.args[3]["id"] == "yahoonews:recent-article"
        assert second_call.args[2] == 1
        assert second_call.args[3]["id"] == "5ch:recent-thread"

    @pytest.mark.asyncio
    async def test_older_dated_discovery_for_established_term_is_added_without_notification(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        existing = SourceItem(
            id="youtube:existing",
            platform="youtube",
            item_id="existing",
            url="https://youtube.example.com/existing",
            published_at=datetime.now(timezone.utc) - timedelta(days=2),
            media_type="video",
            title="Aiko existing post",
            content_text="Aiko existing post",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, existing])
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=existing.id))
        db_session.commit()

        old_new_discovery = _make_item(
            platform="youtube",
            item_id="old-but-new-to-feed",
            published_at=datetime.now(timezone.utc) - timedelta(hours=25),
            title="Aiko old but newly discovered",
            content_text="Aiko old but newly discovered",
            raw_payload={"date_parsed": True},
        )
        connector = _mock_connector("youtube", [old_new_discovery])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        db_session.expire_all()
        match = (
            db_session.query(Match)
            .filter(Match.watch_term_id == term.id)
            .filter(Match.source_item_id == "youtube:old-but-new-to-feed")
            .one()
        )
        assert match is not None
        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_dated_discovery_notifies_established_term(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        existing = SourceItem(
            id="youtube:existing",
            platform="youtube",
            item_id="existing",
            url="https://youtube.example.com/existing",
            published_at=datetime.now(timezone.utc) - timedelta(days=2),
            media_type="video",
            title="Aiko existing post",
            content_text="Aiko existing post",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, existing])
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=existing.id))
        db_session.commit()
        term_id = term.id

        fresh_new_discovery = _make_item(
            platform="youtube",
            item_id="fresh-established",
            published_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            title="Aiko fresh established discovery",
            content_text="Aiko fresh established discovery",
            raw_payload={"date_parsed": True},
        )
        connector = _mock_connector("youtube", [fresh_new_discovery])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        mock_notify.assert_called_once()
        _, called_term, called_count, preview_item = mock_notify.call_args.args
        assert sa_inspect(called_term).identity == (term_id,)
        assert called_count == 1
        assert preview_item["id"] == "youtube:fresh-established"

    @pytest.mark.asyncio
    async def test_existing_discussion_reply_update_is_notified(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        existing = SourceItem(
            id="5ch:old-thread",
            platform="5ch",
            item_id="old-thread",
            url="https://itest.5ch.io/news4vip/test/read.cgi/example/1780000000",
            published_at=datetime.now(timezone.utc) - timedelta(days=7),
            media_type="article",
            title="Aiko old discussion",
            content_text="Aiko initial discussion",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, existing])
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=existing.id))
        db_session.commit()
        term_id = term.id

        reply_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        updated_thread = _make_item(
            platform="5ch",
            item_id="old-thread",
            published_at=reply_at,
            title="Aiko old discussion",
            content_text="Aiko new reply",
            raw_payload={"date_parsed": True},
        )
        connector = _mock_connector("5ch", [updated_thread])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        db_session.expire_all()
        assert db_session.query(Match).filter(Match.watch_term_id == term.id).count() == 1
        refreshed = db_session.get(SourceItem, "5ch:old-thread")
        assert refreshed is not None
        assert refreshed.published_at.replace(tzinfo=timezone.utc) == reply_at
        assert refreshed.content_text == "Aiko new reply"
        mock_notify.assert_called_once()
        _, called_term, called_count, preview_item = mock_notify.call_args.args
        assert sa_inspect(called_term).identity == (term_id,)
        assert called_count == 1
        assert preview_item["id"] == "5ch:old-thread"
        assert preview_item["content_text"] == "Aiko new reply"

    @pytest.mark.asyncio
    async def test_estimated_discussion_reply_heal_is_notified(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        existing = SourceItem(
            id="girlschannel:estimated-thread",
            platform="girlschannel",
            item_id="estimated-thread",
            url="https://girlschannel.example.com/topics/estimated-thread/",
            published_at=datetime.now(timezone.utc) - timedelta(hours=3),
            media_type="text",
            title="Aiko estimated discussion",
            content_text="Aiko initial discussion",
            raw_payload={"date_parsed": False, "source": "direct"},
        )
        db_session.add_all([term, existing])
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=existing.id))
        db_session.commit()
        term_id = term.id

        reply_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        updated_thread = _make_item(
            platform="girlschannel",
            item_id="estimated-thread",
            published_at=reply_at,
            title="Aiko estimated discussion",
            content_text="Aiko new reply",
            raw_payload={"date_parsed": True, "source": "direct"},
        )
        connector = _mock_connector("girlschannel", [updated_thread])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        db_session.expire_all()
        refreshed = db_session.get(SourceItem, "girlschannel:estimated-thread")
        assert refreshed is not None
        assert refreshed.published_at.replace(tzinfo=timezone.utc) == reply_at
        assert refreshed.content_text == "Aiko new reply"
        assert refreshed.raw_payload["date_parsed"] is True
        mock_notify.assert_called_once()
        _, called_term, called_count, preview_item = mock_notify.call_args.args
        assert sa_inspect(called_term).identity == (term_id,)
        assert called_count == 1
        assert preview_item["id"] == "girlschannel:estimated-thread"
        assert preview_item["content_text"] == "Aiko new reply"

    @pytest.mark.asyncio
    async def test_empty_payload_discussion_reply_heal_is_notified(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        existing = SourceItem(
            id="girlschannel:empty-payload-thread",
            platform="girlschannel",
            item_id="empty-payload-thread",
            url="https://girlschannel.example.com/topics/empty-payload-thread/",
            published_at=datetime.now(timezone.utc) - timedelta(hours=3),
            media_type="text",
            title="Aiko empty payload discussion",
            content_text="Aiko initial discussion",
            raw_payload={"date_parsed": False, "source": "direct"},
        )
        db_session.add_all([term, existing])
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=existing.id))
        db_session.commit()
        term_id = term.id

        reply_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        updated_thread = _make_item(
            platform="girlschannel",
            item_id="empty-payload-thread",
            published_at=reply_at,
            title="Aiko empty payload discussion",
            content_text="Aiko new reply",
            raw_payload={},
        )
        connector = _mock_connector("girlschannel", [updated_thread])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        db_session.expire_all()
        refreshed = db_session.get(SourceItem, "girlschannel:empty-payload-thread")
        assert refreshed is not None
        assert refreshed.published_at.replace(tzinfo=timezone.utc) == reply_at
        assert refreshed.content_text == "Aiko new reply"
        assert refreshed.raw_payload["date_parsed"] is True
        mock_notify.assert_called_once()
        _, called_term, called_count, preview_item = mock_notify.call_args.args
        assert sa_inspect(called_term).identity == (term_id,)
        assert called_count == 1
        assert preview_item["id"] == "girlschannel:empty-payload-thread"
        assert preview_item["content_text"] == "Aiko new reply"

    @pytest.mark.asyncio
    async def test_new_item_and_existing_discussion_reply_update_are_both_notified(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        existing = SourceItem(
            id="5ch:old-thread",
            platform="5ch",
            item_id="old-thread",
            url="https://itest.5ch.io/news4vip/test/read.cgi/example/1780000000",
            published_at=datetime.now(timezone.utc) - timedelta(days=7),
            media_type="article",
            title="Aiko old discussion",
            content_text="Aiko initial discussion",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, existing])
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=existing.id))
        db_session.commit()
        term_id = term.id

        reply_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        new_item_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        updated_thread = _make_item(
            platform="5ch",
            item_id="old-thread",
            published_at=reply_at,
            title="Aiko old discussion",
            content_text="Aiko new reply",
            raw_payload={"date_parsed": True},
        )
        new_video = _make_item(
            platform="youtube",
            item_id="fresh-video",
            published_at=new_item_at,
            title="Aiko fresh video",
            content_text="Aiko fresh video",
            raw_payload={"date_parsed": True, "source": "youtube_api"},
        )
        mock_notify = AsyncMock(return_value=True)
        TestSession = sessionmaker(bind=db_engine)

        with patch(
            "app.ingestion.scheduler._build_connectors",
            return_value=[
                _mock_connector("5ch", [updated_thread]),
                _mock_connector("youtube", [new_video]),
            ],
        ), patch(
            "app.ingestion.scheduler.SessionLocal",
            TestSession,
        ), patch(
            "app.ingestion.scheduler.send_new_match_notifications",
            new=mock_notify,
        ):
            await _poll_once_unlocked()

        assert mock_notify.call_count == 2
        for call in mock_notify.call_args_list:
            _, called_term, _, _ = call.args
            assert sa_inspect(called_term).identity == (term_id,)
        # Oldest published item first: the fresh video (30 min ago) precedes
        # the discussion reply update (5 min ago).
        first_call, second_call = mock_notify.call_args_list
        assert first_call.args[2] == 1
        assert first_call.args[3]["id"] == "youtube:fresh-video"
        assert first_call.args[3]["source"] == "youtube_api"
        assert second_call.args[2] == 1
        assert second_call.args[3]["id"] == "5ch:old-thread"

    @pytest.mark.asyncio
    async def test_duplicate_term_existing_discussion_reply_update_is_notified(
        self,
        db_engine,
        db_session,
    ):
        global_term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        owner_term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            owner_device_secret="owner-secret",
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        existing = SourceItem(
            id="5ch:shared-old-thread",
            platform="5ch",
            item_id="shared-old-thread",
            url="https://itest.5ch.io/news4vip/test/read.cgi/example/1780000001",
            published_at=datetime.now(timezone.utc) - timedelta(days=7),
            media_type="article",
            title="Aiko shared old discussion",
            content_text="Aiko initial discussion",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([
            global_term,
            owner_term,
            existing,
            _active_push_entitlement("owner-secret"),
            APNSDeviceToken(
                token="owner-token",
                environment="production",
                device_secret="owner-secret",
                is_verified=True,
            ),
        ])
        db_session.flush()
        db_session.add_all(
            [
                Match(watch_term_id=global_term.id, source_item_id=existing.id),
                Match(watch_term_id=owner_term.id, source_item_id=existing.id),
            ]
        )
        db_session.commit()
        global_term_id = global_term.id
        owner_term_id = owner_term.id

        updated_thread = _make_item(
            platform="5ch",
            item_id="shared-old-thread",
            published_at=datetime.now(timezone.utc) - timedelta(minutes=20),
            title="Aiko shared old discussion",
            content_text="Aiko new reply",
            raw_payload={"date_parsed": True},
        )
        connector = _mock_connector("5ch", [updated_thread])
        notified_term_ids: list[int] = []

        async def fake_notify(_db, notified_term, _count, _preview_item, **_kwargs):
            notified_term_ids.append(notified_term.id)
            return True

        TestSession = sessionmaker(bind=db_engine)
        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=fake_notify), \
             patch.object(settings, "poll_terms_per_run", 1):
            await _poll_once_unlocked()

        assert set(notified_term_ids) == {global_term_id, owner_term_id}
        db_session.expire_all()
        assert db_session.query(Match).count() == 2

    @pytest.mark.asyncio
    async def test_estimated_publication_date_is_not_notified(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()

        estimated = _make_item(
            item_id="estimated",
            raw_payload={"date_parsed": False},
        )
        connector = _mock_connector("youtube", [estimated])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        assert db_session.query(Match).count() == 1
        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_estimated_publication_date_does_not_notify_established_term(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        existing = SourceItem(
            id="youtube:existing",
            platform="youtube",
            item_id="existing",
            url="https://youtube.example.com/existing",
            published_at=datetime.now(timezone.utc) - timedelta(hours=3),
            media_type="video",
            title="Aiko existing post",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, existing])
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=existing.id))
        db_session.commit()

        estimated = _make_item(
            item_id="estimated-established",
            title="Aiko estimated established",
            raw_payload={"date_parsed": False},
        )
        connector = _mock_connector("youtube", [estimated])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_notification_sends_each_item_and_includes_parsed_feed_sort_date(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db_session.add(term)
        db_session.commit()

        reliable = datetime.now(timezone.utc) - timedelta(minutes=30)
        parsed_connector = _mock_connector(
            "youtube",
            [
                _make_item(
                    platform="youtube",
                    item_id="parsed",
                    published_at=reliable,
                    title="Aiko parsed",
                    raw_payload={"date_parsed": True, "source": "youtube_api"},
                )
            ],
        )
        estimated_connector = _mock_connector(
            "yahoonews",
            [
                _make_item(
                    platform="yahoonews",
                    item_id="estimated",
                    published_at=datetime.now(timezone.utc),
                    title="Aiko estimated",
                    raw_payload={"date_parsed": False},
                )
            ],
        )
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch(
            "app.ingestion.scheduler._build_connectors",
            return_value=[estimated_connector, parsed_connector],
        ), patch(
            "app.ingestion.scheduler.SessionLocal",
            TestSession,
        ), patch(
            "app.ingestion.scheduler.send_new_match_notifications",
            new=mock_notify,
        ):
            await _poll_once_unlocked()

        mock_notify.assert_called_once()
        _, _, called_count, preview_item = mock_notify.call_args.args
        assert called_count == 1
        assert preview_item["id"] == "youtube:parsed"
        assert preview_item["source"] == "youtube_api"
        assert datetime.fromisoformat(preview_item["published_at"]).replace(tzinfo=timezone.utc) == reliable

    @pytest.mark.asyncio
    async def test_notification_preview_uses_stored_feed_item_date_for_existing_source(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        old_published_at = datetime.now(timezone.utc) - timedelta(days=365)
        db_session.add_all(
            [
                term,
                SourceItem(
                    id="youtube:existing",
                    platform="youtube",
                    item_id="existing",
                    url="https://youtube.example.com/existing",
                    published_at=old_published_at,
                    media_type="video",
                    title="Aiko old stored item",
                    content_text="Aiko Haruka",
                    raw_payload={"date_parsed": True},
                ),
            ]
        )
        db_session.commit()

        existing_connector_item = _make_item(
            platform="youtube",
            item_id="existing",
            published_at=datetime.now(timezone.utc),
            title="Aiko existing returned as new",
            raw_payload={"date_parsed": False},
        )
        fresher_feed_item = _make_item(
            platform="news",
            item_id="fresher",
            published_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            title="Aiko fresher in feed",
            raw_payload={"date_parsed": True},
        )
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)

        with patch(
            "app.ingestion.scheduler._build_connectors",
            return_value=[
                _mock_connector("youtube", [existing_connector_item]),
                _mock_connector("news", [fresher_feed_item]),
            ],
        ), patch(
            "app.ingestion.scheduler.SessionLocal",
            TestSession,
        ), patch(
            "app.ingestion.scheduler.send_new_match_notifications",
            new=mock_notify,
        ):
            await _poll_once_unlocked()

        mock_notify.assert_called_once()
        _, _, called_count, preview_item = mock_notify.call_args.args
        assert called_count == 1
        assert preview_item["id"] == "news:fresher"

    @pytest.mark.asyncio
    async def test_notification_failure_is_isolated_and_retried_from_outbox(
        self,
        db_engine,
        db_session,
    ):
        first = WatchTerm(keyword="Aiko", notify_on_new=True)
        second = WatchTerm(keyword="Haruka", notify_on_new=True)
        db_session.add_all([first, second])
        db_session.commit()
        first_id = first.id
        second_id = second.id

        async def fetch(search_term, _mode):
            return [
                _make_item(
                    item_id=search_term.lower(),
                    title=f"{search_term} item",
                    content_text=search_term,
                )
            ]

        connector = MagicMock()
        connector.PLATFORM = "youtube"
        connector.MIN_FETCH_TIMEOUT_SECONDS = None
        connector.fetch = fetch
        mock_notify = AsyncMock(side_effect=[RuntimeError("bad APNs key"), None, None])
        TestSession = sessionmaker(bind=db_engine)

        with patch(
            "app.ingestion.scheduler._build_connectors",
            return_value=[connector],
        ), patch(
            "app.ingestion.scheduler.SessionLocal",
            TestSession,
        ), patch(
            "app.ingestion.scheduler.send_new_match_notifications",
            new=mock_notify,
        ), patch.object(
            settings,
            "poll_terms_per_run",
            0,
        ):
            await _poll_once_unlocked()
            await _poll_once_unlocked()

        assert mock_notify.call_count == 3
        assert sa_inspect(mock_notify.call_args_list[0].args[1]).identity == (first_id,)
        assert sa_inspect(mock_notify.call_args_list[1].args[1]).identity == (second_id,)
        assert sa_inspect(mock_notify.call_args_list[2].args[1]).identity == (first_id,)
        retry_db = TestSession()
        try:
            assert retry_db.query(PendingNotification).count() == 0
        finally:
            retry_db.close()

    @pytest.mark.asyncio
    async def test_multi_item_pending_notification_sends_one_alert_per_item(
        self,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        published_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        db_session.add(term)
        db_session.flush()
        db_session.add_all([
            SourceItem(
                id=f"youtube:{item_id}",
                platform="youtube",
                item_id=item_id,
                url=f"https://example.com/{item_id}",
                published_at=published_at,
                media_type="video",
                raw_payload={"date_parsed": True},
            )
            for item_id in ("first", "second", "third")
        ])
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=3,
                preview_item={
                    "items": [
                        {
                            "id": "youtube:first",
                            "url": "https://example.com/first",
                            "published_at": published_at.isoformat(),
                        },
                        {
                            "id": "youtube:second",
                            "url": "https://example.com/second",
                            "published_at": published_at.isoformat(),
                        },
                        {
                            "id": "youtube:third",
                            "url": "https://example.com/third",
                            "published_at": published_at.isoformat(),
                        },
                    ]
                },
            )
        )
        db_session.commit()

        mock_notify = AsyncMock(return_value=True)
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        assert mock_notify.call_count == 3
        sent_ids = []
        for call in mock_notify.call_args_list:
            _, _, called_count, preview_item = call.args
            assert called_count == 1
            assert "_notification_count" not in preview_item
            sent_ids.append(preview_item["id"])
            assert call.kwargs["notification_item_ids"] == [preview_item["id"]]
        # Items share a published_at, so queue order (first, second, third) is preserved.
        assert sent_ids == ["youtube:first", "youtube:second", "youtube:third"]
        db_session.expire_all()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_per_item_notification_failure_keeps_full_outbox(
        self,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        published_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        db_session.add(term)
        db_session.flush()
        db_session.add_all([
            SourceItem(
                id=f"youtube:{item_id}",
                platform="youtube",
                item_id=item_id,
                url=f"https://example.com/{item_id}",
                published_at=published_at,
                media_type="video",
                raw_payload={"date_parsed": True},
            )
            for item_id in ("first", "second")
        ])
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=2,
                preview_item={
                    "items": [
                        {
                            "id": "youtube:first",
                            "url": "https://example.com/first",
                            "published_at": published_at.isoformat(),
                        },
                        {
                            "id": "youtube:second",
                            "url": "https://example.com/second",
                            "published_at": published_at.isoformat(),
                        },
                    ]
                },
            )
        )
        db_session.commit()

        mock_notify = AsyncMock(return_value=False)
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is False
        assert mock_notify.call_count == 2
        for call in mock_notify.call_args_list:
            assert call.args[2] == 1
        db_session.expire_all()
        pending = db_session.get(PendingNotification, term.id)
        assert pending is not None
        assert pending.new_count == 2
        assert [item["id"] for item in pending.preview_item["items"]] == [
            "youtube:first",
            "youtube:second",
        ]

    @pytest.mark.asyncio
    async def test_legacy_pending_notification_keeps_grouped_count_when_new_item_is_queued(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db_session.add(term)
        db_session.flush()
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=2,
                preview_item={
                    "id": "youtube:legacy",
                    "url": "https://example.com/legacy",
                    "title": "Aiko legacy grouped alert",
                    "published_at": (
                        datetime.now(timezone.utc) - timedelta(minutes=20)
                    ).isoformat(),
                },
            )
        )
        db_session.commit()

        connector = _mock_connector(
            "youtube",
            [
                _make_item(
                    item_id="new-after-legacy",
                    title="Aiko new after legacy",
                    published_at=datetime.now(timezone.utc) - timedelta(minutes=10),
                )
            ],
        )
        mock_notify = AsyncMock(return_value=True)
        TestSession = sessionmaker(bind=db_engine)
        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        mock_notify.assert_called_once()
        _, _, called_count, preview_item = mock_notify.call_args.args
        assert called_count == 1
        assert preview_item["id"] == "youtube:new-after-legacy"
        assert "_notification_count" not in preview_item
        db_session.expire_all()
        pending = db_session.get(PendingNotification, term.id)
        assert pending is None

    @pytest.mark.asyncio
    async def test_cancelled_poll_keeps_committed_notification_in_outbox(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(keyword="Aiko", aliases=["Haruka"], notify_on_new=True)
        db_session.add(term)
        db_session.commit()

        async def fetch(search_term, _mode):
            if search_term == "Aiko":
                return [_make_item(item_id="committed-before-cancel")]
            raise asyncio.CancelledError()

        connector = MagicMock()
        connector.PLATFORM = "youtube"
        connector.MIN_FETCH_TIMEOUT_SECONDS = None
        connector.fetch = fetch
        TestSession = sessionmaker(bind=db_engine)

        with patch(
            "app.ingestion.scheduler._build_connectors",
            return_value=[connector],
        ), patch(
            "app.ingestion.scheduler.SessionLocal",
            TestSession,
        ), patch(
            "app.ingestion.scheduler.send_new_match_notifications",
            new=AsyncMock(),
        ):
            with pytest.raises(asyncio.CancelledError):
                await _poll_once_unlocked()

        retry_db = TestSession()
        try:
            pending = retry_db.get(PendingNotification, term.id)
            assert pending is not None
            assert pending.new_count == 1
        finally:
            retry_db.close()

    @pytest.mark.asyncio
    async def test_pending_notification_is_retried_before_next_connector_fetch(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        item = SourceItem(
            id="youtube:pending",
            platform="youtube",
            item_id="pending",
            url="https://example.com/pending",
            published_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            media_type="video",
            title="Aiko pending",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, item])
        db_session.flush()
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "items": [
                        {"id": "youtube:pending", "url": "https://example.com/pending"}
                    ]
                },
            )
        )
        db_session.commit()
        term_id = term.id

        async def fetch(_search_term, _mode):
            raise asyncio.CancelledError()

        connector = MagicMock()
        connector.PLATFORM = "youtube"
        connector.MIN_FETCH_TIMEOUT_SECONDS = None
        connector.fetch = fetch
        mock_notify = AsyncMock(return_value=True)
        TestSession = sessionmaker(bind=db_engine)

        with patch(
            "app.ingestion.scheduler._build_connectors",
            return_value=[connector],
        ), patch(
            "app.ingestion.scheduler.SessionLocal",
            TestSession,
        ), patch(
            "app.ingestion.scheduler.send_new_match_notifications",
            new=mock_notify,
        ):
            with pytest.raises(asyncio.CancelledError):
                await _poll_once_unlocked()

        mock_notify.assert_called_once()
        _, called_term, called_count, preview_item = mock_notify.call_args.args
        assert called_term.id == term_id
        assert called_count == 1
        assert preview_item["id"] == "youtube:pending"

        retry_db = TestSession()
        try:
            assert retry_db.get(PendingNotification, term_id) is None
        finally:
            retry_db.close()

    @pytest.mark.asyncio
    async def test_pending_notification_for_inactive_term_is_cleared_without_sending(
        self,
        db_session,
    ):
        term = WatchTerm(keyword="Aiko", is_active=False, notify_on_new=True)
        db_session.add(term)
        db_session.flush()
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "items": [
                        {"id": "youtube:pending", "url": "https://example.com/pending"}
                    ]
                },
            )
        )
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns._send_one", new=AsyncMock()) as mock_send:
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        mock_send.assert_not_called()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_stale_reliable_pending_notification_is_cleared_without_sending(
        self,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        old_item = SourceItem(
            id="youtube:old-pending",
            platform="youtube",
            item_id="old-pending",
            url="https://youtube.example.com/old-pending",
            published_at=datetime.now(timezone.utc) - timedelta(days=2),
            media_type="video",
            title="Aiko old pending",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, old_item])
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=old_item.id))
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "items": [
                        {
                            "id": old_item.id,
                            "url": old_item.url,
                            "published_at": old_item.published_at.isoformat(),
                        }
                    ]
                },
            )
        )
        db_session.commit()

        mock_notify = AsyncMock()
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        mock_notify.assert_not_called()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_stale_missing_source_pending_notification_is_cleared_without_sending(
        self,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db_session.add(term)
        db_session.flush()
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "items": [
                        {
                            "id": "youtube:missing-pending",
                            "url": "https://youtube.example.com/missing-pending",
                            "published_at": (
                                datetime.now(timezone.utc) - timedelta(days=2)
                            ).isoformat(),
                        }
                    ]
                },
            )
        )
        db_session.commit()

        mock_notify = AsyncMock()
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        mock_notify.assert_not_called()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_legacy_stale_pending_notification_is_cleared_without_sending(
        self,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db_session.add(term)
        db_session.flush()
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "id": "youtube:legacy-stale",
                    "url": "https://youtube.example.com/legacy-stale",
                    "published_at": (
                        datetime.now(timezone.utc) - timedelta(days=2)
                    ).isoformat(),
                },
            )
        )
        db_session.commit()

        mock_notify = AsyncMock()
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        mock_notify.assert_not_called()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_malformed_pending_notification_is_cleared_without_sending(
        self,
        db_session,
    ):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.flush()
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "items": [
                        {
                            "id": "youtube:malformed-pending",
                            "url": "https://youtube.example.com/malformed-pending",
                        }
                    ]
                },
            )
        )
        db_session.commit()

        mock_notify = AsyncMock()
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        mock_notify.assert_not_called()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_previewless_pending_notification_is_cleared_without_sending(
        self,
        db_session,
    ):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.flush()
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=3,
                preview_item=None,
            )
        )
        db_session.commit()

        mock_notify = AsyncMock()
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        mock_notify.assert_not_called()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_stale_discussion_reply_pending_notification_is_cleared_without_sending(
        self,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        old_item = SourceItem(
            id="5ch:old-pending",
            platform="5ch",
            item_id="old-pending",
            url="https://5ch.example.com/old-pending",
            published_at=datetime.now(timezone.utc) - timedelta(days=2),
            media_type="text",
            title="Aiko old pending",
            raw_payload={"date_parsed": True},
        )
        db_session.add_all([term, old_item])
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=old_item.id))
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "items": [
                        {
                            "id": old_item.id,
                            "url": old_item.url,
                            "published_at": old_item.published_at.isoformat(),
                            "notification_preview_source": "discussion_reply_update",
                        }
                    ]
                },
            )
        )
        db_session.commit()

        mock_notify = AsyncMock()
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        mock_notify.assert_not_called()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_missing_source_discussion_reply_pending_notification_is_cleared_without_sending(
        self,
        db_session,
    ):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.flush()
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "items": [
                        {
                            "id": "5ch:missing-reply",
                            "url": "https://5ch.example.com/missing-reply",
                            "published_at": (
                                datetime.now(timezone.utc) - timedelta(days=2)
                            ).isoformat(),
                            "notification_preview_source": "discussion_reply_update",
                        }
                    ]
                },
            )
        )
        db_session.commit()

        mock_notify = AsyncMock()
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        mock_notify.assert_not_called()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_fresh_missing_source_discussion_reply_pending_notification_is_cleared_without_sending(
        self,
        db_session,
    ):
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db_session.add(term)
        db_session.flush()
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "items": [
                        {
                            "id": "5ch:missing-fresh-reply",
                            "url": "https://5ch.example.com/missing-fresh-reply",
                            "published_at": (
                                datetime.now(timezone.utc) - timedelta(minutes=10)
                            ).isoformat(),
                            "notification_preview_source": "discussion_reply_update",
                        }
                    ]
                },
            )
        )
        db_session.commit()

        mock_notify = AsyncMock()
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        mock_notify.assert_not_called()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_malformed_discussion_reply_pending_notification_is_cleared_without_sending(
        self,
        db_session,
    ):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.flush()
        db_session.add(
            PendingNotification(
                watch_term_id=term.id,
                new_count=1,
                preview_item={
                    "items": [
                        {
                            "url": "https://5ch.example.com/malformed-reply",
                            "notification_preview_source": "discussion_reply_update",
                        }
                    ]
                },
            )
        )
        db_session.commit()

        mock_notify = AsyncMock()
        with patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            delivered = await _deliver_pending_notification(db_session, term)

        assert delivered is True
        mock_notify.assert_not_called()
        assert db_session.get(PendingNotification, term.id) is None

    @pytest.mark.asyncio
    async def test_failed_connector_commit_is_not_included_in_notification(
        self,
        db_engine,
        db_session,
    ):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()

        connector = _mock_connector(
            "youtube",
            [_make_item(platform="youtube", item_id="rolled-back", title="Aiko rollback")],
        )
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)
        from sqlalchemy.orm import Session as _Session
        original_commit = _Session.commit
        commit_calls = {"count": 0}

        def fail_first_commit(session):
            commit_calls["count"] += 1
            if commit_calls["count"] == 1:
                raise RuntimeError("simulated commit failure")
            return original_commit(session)

        with patch(
            "app.ingestion.scheduler._build_connectors",
            return_value=[connector],
        ), patch(
            "app.ingestion.scheduler.SessionLocal",
            TestSession,
        ), patch(
            "app.ingestion.scheduler.send_new_match_notifications",
            new=mock_notify,
        ), patch.object(
            _Session,
            "commit",
            fail_first_commit,
        ):
            await _poll_once_unlocked()

        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_notify_not_called_for_muted_terms(self, db_engine, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=False)
        db_session.add(term)
        db_session.commit()
        term_id = term.id

        connector = _mock_connector("youtube", [_make_item(item_id="new2")])
        mock_notify = AsyncMock()
        TestSession = sessionmaker(bind=db_engine)
        with patch("app.ingestion.scheduler._build_connectors", return_value=[connector]), \
             patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=mock_notify):
            await _poll_once_unlocked()

        mock_notify.assert_not_called()
        db_session.expire_all()
        assert db_session.get(SourceItem, "youtube:new2") is not None
        assert db_session.query(Match).filter_by(watch_term_id=term_id, source_item_id="youtube:new2").count() == 1
        assert db_session.get(PendingNotification, term_id) is None

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
        bad_connector.MIN_FETCH_TIMEOUT_SECONDS = None
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
        bad.MIN_FETCH_TIMEOUT_SECONDS = None
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
        item_bad = _make_item(
            item_id="vid1",
            published_at=bad_date,
            title="Aiko original title",
            content_text="Aiko original body",
        )
        connector = _mock_connector("youtube", [item_bad])
        await _run_poll(db_engine, [connector])

        db_session.expire_all()
        stored_before = db_session.get(SourceItem, "youtube:vid1")
        assert stored_before is not None

        # Second poll: connector now returns a real date >5min old and >5min different
        real_date = datetime.now(timezone.utc) - timedelta(hours=3)
        item_real = _make_item(
            item_id="vid1",
            published_at=real_date,
            title="Aiko replacement title",
            content_text="Aiko replacement body",
        )
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
        assert stored_after.title == "Aiko original title"
        assert stored_after.content_text == "Aiko original body"

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


class TestIngestionDiscussionHealing:
    @pytest.mark.asyncio
    async def test_discussion_heals_toward_newer_parsed_date(self, db_engine, db_session):
        """girlschannel thread with a real (parsed) newer date should bubble up."""
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        old = datetime.now(timezone.utc) - timedelta(hours=5)
        item = _make_item(platform="girlschannel", item_id="111",
                          published_at=old, raw_payload={"date_parsed": True})
        await _run_poll(db_engine, [_mock_connector("girlschannel", [item])])

        newer = datetime.now(timezone.utc) - timedelta(minutes=10)
        item2 = _make_item(platform="girlschannel", item_id="111",
                           published_at=newer, raw_payload={"date_parsed": True})
        await _run_poll(db_engine, [_mock_connector("girlschannel", [item2])])

        db_session.expire_all()
        stored = db_session.get(SourceItem, "girlschannel:111")
        stored_dt = stored.published_at
        if stored_dt.tzinfo is None:
            stored_dt = stored_dt.replace(tzinfo=timezone.utc)
        assert abs((stored_dt - newer).total_seconds()) < 10, "parsed newer date should heal"

    @pytest.mark.asyncio
    async def test_discussion_does_not_heal_placeholder_date(self, db_engine, db_session):
        """A placeholder date (date_parsed=False) must NOT re-pin the thread to now()."""
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        original = datetime.now(timezone.utc) - timedelta(hours=3)
        item = _make_item(platform="girlschannel", item_id="222",
                          published_at=original, raw_payload={"date_parsed": False})
        await _run_poll(db_engine, [_mock_connector("girlschannel", [item])])

        # Second poll: connector returns a fresh now() placeholder (date_parsed=False).
        placeholder = datetime.now(timezone.utc)
        item2 = _make_item(platform="girlschannel", item_id="222",
                           published_at=placeholder, raw_payload={"date_parsed": False})
        await _run_poll(db_engine, [_mock_connector("girlschannel", [item2])])

        db_session.expire_all()
        stored = db_session.get(SourceItem, "girlschannel:222")
        stored_dt = stored.published_at
        if stored_dt.tzinfo is None:
            stored_dt = stored_dt.replace(tzinfo=timezone.utc)
        assert abs((stored_dt - original).total_seconds()) < 60, \
            "placeholder date must not overwrite the stored date every poll"


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

        self._seed_items(db_session, term, "youtube", 105)
        _prune_old_items(db_session)

        db_session.expire_all()
        assert db_session.query(Match).count() == 100
        assert db_session.query(SourceItem).count() == 100

    def test_does_not_prune_at_or_below_limit(self, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        self._seed_items(db_session, term, "youtube", 100)
        _prune_old_items(db_session)

        db_session.expire_all()
        assert db_session.query(Match).count() == 100

    def test_skip_platforms_never_pruned(self, db_session):
        """5ch and girlschannel must be skipped regardless of count."""
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        for platform in ("5ch", "girlschannel"):
            self._seed_items(db_session, term, platform, 205)

        total_before = db_session.query(Match).count()
        _prune_old_items(db_session)

        db_session.expire_all()
        assert db_session.query(Match).count() == total_before

    def test_oldest_items_are_removed_not_newest(self, db_session):
        """After pruning, the 5 oldest items must be gone; the 100 newest must survive."""
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        self._seed_items(db_session, term, "youtube", 105)
        _prune_old_items(db_session)

        db_session.expire_all()
        # item0000 is the newest (base - 0h), item0104 is the oldest (base - 104h)
        assert db_session.get(SourceItem, "youtube:item0000") is not None
        assert db_session.get(SourceItem, "youtube:item0104") is None

    def test_pruning_two_terms_independently(self, db_session):
        """Each (platform, watch_term) pair is pruned independently."""
        term1 = WatchTerm(keyword="Aiko")
        term2 = WatchTerm(keyword="Haruka")
        db_session.add_all([term1, term2])
        db_session.commit()

        self._seed_items(db_session, term1, "youtube", 105, prefix="t1item")
        self._seed_items(db_session, term2, "youtube", 105, prefix="t2item")
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
        assert term1_count == 100
        assert term2_count == 100


class TestPollOnceLocking:
    @pytest.mark.asyncio
    async def test_poll_once_skips_when_lock_is_held(self):
        """If _poll_lock is already locked, poll_once must return without running the poll."""
        async with _poll_lock:
            # Lock is now held; poll_once should detect this and return early.
            with patch("app.ingestion.scheduler._poll_once_unlocked", new=AsyncMock()) as mock_poll:
                await poll_once()
            mock_poll.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_once_skips_connector_work_without_active_terms(self, db_engine, db_session):
        TestSession = sessionmaker(bind=db_engine)
        inactive = WatchTerm(keyword="Aiko", is_active=False, notify_on_new=True)
        db_session.add(inactive)
        db_session.commit()

        with patch("app.ingestion.scheduler.SessionLocal", TestSession), \
             patch("app.ingestion.scheduler._build_connectors") as mock_build_connectors, \
             patch("app.ingestion.scheduler.revalidate_unverified_devices", new=AsyncMock()), \
             patch("app.ingestion.scheduler.send_new_match_notifications", new=AsyncMock()):
            await _poll_once_unlocked()

        mock_build_connectors.assert_not_called()
        event = (
            db_session.query(BackendEvent)
            .filter(BackendEvent.kind == "poll")
            .order_by(BackendEvent.id.desc())
            .first()
        )
        assert event is not None
        assert event.status == "skipped"
        assert event.payload["total_terms"] == 0
        assert event.payload["connectors"] == 0
