import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.database import get_db
from app.main import (
    _initialize_backend_services,
    _mark_abandoned_poll_events,
    _mark_abandoned_poll_events_after_grace,
    _schedule_poll_recovery_after_grace,
    _startup_status,
    database_operational_error_handler,
)
from app.models import APNSDeviceToken, BackendEvent, Match, MutedFeedItem, PendingNotification, SourceItem, WatchTerm


class TestGetDb:
    def test_yields_session_and_closes(self):
        gen = get_db()
        session = next(gen)
        assert session is not None
        try:
            next(gen)
        except StopIteration:
            pass  # generator closed normally after yielding


class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert set(data["startup"]) == {"schema_migration", "scheduler", "error"}

    def test_health_accepts_head_checks(self, client):
        r = client.head("/api/health")
        assert r.status_code == 200
        assert r.content == b""


class TestDatabaseOperationalErrorHandler:
    def test_returns_service_unavailable_response(self):
        request = MagicMock()
        request.url.path = "/api/watch-terms/"
        exc = OperationalError("SELECT 1", {}, Exception("quota exceeded"))

        response = asyncio.run(database_operational_error_handler(request, exc))

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["detail"] == "Database unavailable"
        assert set(body["startup"]) == {"schema_migration", "scheduler", "error"}


class TestStartupPollRecovery:
    def test_marks_stale_in_progress_poll_events_as_interrupted(self, db_engine, db_session):
        stale_created_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        started = BackendEvent(
            kind="poll",
            status="started",
            message="Scheduled/backend poll started",
            payload={"terms": 1},
            created_at=stale_created_at,
        )
        timeout = BackendEvent(
            kind="poll",
            status="running_past_request_timeout",
            message="Scheduled/backend poll exceeded the request budget",
            payload={"timeout_seconds": 210},
            created_at=stale_created_at,
        )
        fresh = BackendEvent(
            kind="poll",
            status="started",
            message="Scheduled/backend poll started",
            payload={"terms": 1},
        )
        completed = BackendEvent(
            kind="poll",
            status="completed",
            message="Scheduled/backend poll completed",
            payload={},
        )
        db_session.add_all([started, timeout, fresh, completed])
        db_session.commit()

        TestSession = sessionmaker(bind=db_engine)
        with patch("app.main.SessionLocal", TestSession):
            interrupted_count = _mark_abandoned_poll_events()

        db_session.expire_all()
        assert interrupted_count == 2
        assert started.status == "interrupted"
        assert timeout.status == "interrupted"
        assert fresh.status == "started"
        assert completed.status == "completed"
        assert started.message == "Poll interrupted by backend restart or deploy"
        assert "interrupted_at" in started.payload
        assert timeout.payload["timeout_seconds"] == 210

    @pytest.mark.asyncio
    async def test_delayed_recovery_marks_poll_events_after_grace(self, db_engine, db_session):
        stale_created_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        started = BackendEvent(
            kind="poll",
            status="started",
            message="Scheduled/backend poll started",
            payload={"terms": 1},
            created_at=stale_created_at,
        )
        db_session.add(started)
        db_session.commit()

        TestSession = sessionmaker(bind=db_engine)
        with patch("app.main.SessionLocal", TestSession), \
             patch("app.main.asyncio.sleep", new=AsyncMock()):
            await _mark_abandoned_poll_events_after_grace()

        db_session.expire_all()
        assert started.status == "interrupted"

    @pytest.mark.asyncio
    async def test_recovery_schedule_reuses_pending_task(self):
        pending = asyncio.Future()
        with patch("app.main._poll_recovery_task", pending), \
             patch("app.main.asyncio.create_task") as mock_create_task:
            _schedule_poll_recovery_after_grace()

        mock_create_task.assert_not_called()


class TestStartupScheduler:
    @pytest.mark.asyncio
    async def test_startup_leaves_internal_scheduler_disabled_by_default(self):
        with patch("app.main.apply_startup_migrations"), \
             patch("app.main._mark_abandoned_poll_events"), \
             patch("app.main._schedule_poll_recovery_after_grace") as mock_schedule_recovery, \
             patch("app.main.start_scheduler") as mock_start_scheduler, \
             patch.object(settings, "internal_scheduler_enabled", False):
            await _initialize_backend_services()

        assert _startup_status["schema_migration"] == "completed"
        assert _startup_status["scheduler"] == "disabled"
        mock_schedule_recovery.assert_called_once()
        mock_start_scheduler.assert_not_called()

    @pytest.mark.asyncio
    async def test_startup_can_enable_internal_scheduler(self):
        with patch("app.main.apply_startup_migrations"), \
             patch("app.main._mark_abandoned_poll_events"), \
             patch("app.main._schedule_poll_recovery_after_grace") as mock_schedule_recovery, \
             patch("app.main.start_scheduler") as mock_start_scheduler, \
             patch.object(settings, "internal_scheduler_enabled", True):
            await _initialize_backend_services()

        assert _startup_status["schema_migration"] == "completed"
        assert _startup_status["scheduler"] == "running"
        mock_schedule_recovery.assert_called_once()
        mock_start_scheduler.assert_called_once()


class TestNotificationPreviewImage:
    def test_returns_cacheable_png(self, client):
        r = client.get("/api/notification-preview.png")

        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.headers["cache-control"] == "public, max-age=86400"
        assert r.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(r.content) > 1_000

    def test_png_is_not_gzipped(self, client):
        r = client.get(
            "/api/notification-preview.png",
            headers={"Accept-Encoding": "gzip"},
        )

        assert r.status_code == 200
        assert "content-encoding" not in r.headers
        assert r.content.startswith(b"\x89PNG\r\n\x1a\n")


class TestClientDiagnostics:
    def test_client_diagnostics_accepts_refresh_report(self, client, db_session, caplog):
        payload = {
            "reason": "feed_refresh_no_results_after_fallbacks",
            "environment": "Debug",
            "api_base": "http://127.0.0.1:8000",
            "app_version": "1.0",
            "build": "1",
            "active_terms_count": 1,
            "subscribed_platforms": ["youtube", "news"],
            "cached_feed_count": 0,
            "events": [
                {
                    "strategy": "backend_feed_days_90",
                    "status": "empty",
                    "item_count": 0,
                    "added_count": 0,
                    "detail": None,
                }
            ],
        }

        r = client.post("/api/client-diagnostics", json=payload)

        assert r.status_code == 200
        assert r.json() == {"status": "received"}
        assert "client diagnostic reason=feed_refresh_no_results_after_fallbacks" in caplog.text
        event = db_session.query(BackendEvent).filter_by(kind="client_diagnostic").one()
        assert event.status == "reported"
        assert event.payload["reason"] == "feed_refresh_no_results_after_fallbacks"
        assert event.payload["environment"] == "Debug"
        assert event.payload["events"][0]["strategy"] == "backend_feed_days_90"


class TestAdminStats:
    def test_stats_empty_db(self, client):
        r = client.get("/api/admin/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["items_total"] == 0
        assert data["matches_total"] == 0
        assert data["watch_terms"] == []
        assert data["items_by_platform"] == {}
        assert data["apns"]["backend_public_url"]
        assert data["apns_registration"] == {
            "window_hours": 24,
            "count": 0,
            "reasons": {},
            "latest": None,
        }
        assert data["notification_health"] == {
            "healthy": True,
            "active_notify_terms": 0,
            "active_silent_orphan_terms": 0,
            "active_notify_terms_without_verified_devices": 0,
            "orphaned_notification_grace_minutes": 60,
            "active_silent_orphan_term_ids": [],
            "active_notify_term_ids_without_verified_devices": [],
        }
        assert data["latest_relevant_apns"] is None
        assert data["recent_events"] == []

    def test_poller_health_uses_compact_diagnostics_contract(self, client):
        r = client.get("/api/admin/poller-health")

        assert r.status_code == 200
        data = r.json()
        assert data["watch_terms"] == []
        assert data["latest_poll"] is None
        assert data["latest_successful_poll"] is None
        assert data["pending_notifications"] == []
        assert data["notification_health"]["healthy"] is True
        assert data["apns"]["configured"] is False
        assert data["apns_registration"]["count"] == 0

    def test_stats_counts_reflect_db_content(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.flush()

        item = SourceItem(
            id="youtube:abc",
            platform="youtube",
            item_id="abc",
            url="https://youtube.com/watch?v=abc",
            published_at=datetime.now(timezone.utc),
        )
        db_session.add(item)
        db_session.flush()

        match = Match(watch_term_id=term.id, source_item_id=item.id)
        db_session.add(match)
        db_session.commit()

        r = client.get("/api/admin/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["items_total"] == 1
        assert data["matches_total"] == 1
        assert len(data["watch_terms"]) == 1
        assert data["watch_terms"][0]["keyword"] == "Aiko"
        assert data["items_by_platform"] == {"youtube": 1}
        assert data["matches_by_term_platform"] == [
            {
                "term_id": term.id,
                "keyword": "Aiko",
                "platform": "youtube",
                "match_count": 1,
                "latest_published_at": item.published_at.isoformat().replace("+00:00", "Z"),
                "latest_matched_at": match.created_at.isoformat().replace("+00:00", "Z"),
            }
        ]

    def test_stats_groups_items_by_platform(self, client, db_session):
        now = datetime.now(timezone.utc)
        for i, platform in enumerate(["youtube", "youtube", "twitter"]):
            db_session.add(SourceItem(
                id=f"{platform}:{i}",
                platform=platform,
                item_id=str(i),
                url=f"https://example.com/{i}",
                published_at=now,
            ))
        db_session.commit()

        r = client.get("/api/admin/stats")
        data = r.json()
        assert data["items_by_platform"]["youtube"] == 2
        assert data["items_by_platform"]["twitter"] == 1

    def test_stats_includes_apns_verification_counts(self, client, db_session):
        owner_secret = "owner-secret"
        db_session.add_all([
            APNSDeviceToken(
                token="a" * 64,
                environment="sandbox",
                device_secret=owner_secret,
                is_verified=True,
            ),
            APNSDeviceToken(
                token="b" * 64,
                environment="sandbox",
                device_secret=owner_secret,
                is_verified=False,
            ),
            APNSDeviceToken(token="c" * 64, environment="production", is_verified=True),
            WatchTerm(keyword="Owned", owner_device_secret=owner_secret),
            WatchTerm(keyword="Global"),
        ])
        db_session.commit()

        r = client.get("/api/admin/stats")

        assert r.status_code == 200
        apns = r.json()["apns"]
        assert apns["device_tokens_by_environment"] == {
            "production": 1,
            "sandbox": 2,
        }
        assert apns["device_tokens_by_environment_and_verification"] == {
            "production": {"verified": 1, "unverified": 0},
            "sandbox": {"verified": 1, "unverified": 1},
        }
        terms = {term["keyword"]: term for term in r.json()["watch_terms"]}
        assert terms["Owned"]["owner_scoped"] is True
        assert terms["Owned"]["notification_devices"] == 2
        assert terms["Owned"]["notification_verified_devices"] == 1
        assert terms["Global"]["owner_scoped"] is False
        assert terms["Global"]["notification_devices"] == 3
        assert terms["Global"]["notification_verified_devices"] == 2

    def test_stats_flags_notification_health_risks(self, client, db_session):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        db_session.add_all([
            WatchTerm(
                keyword="Silent",
                is_active=True,
                notify_on_new=False,
                owner_device_secret="silent-orphan",
                created_at=old,
            ),
            WatchTerm(
                keyword="Notify",
                is_active=True,
                notify_on_new=True,
                owner_device_secret="notify-without-device",
                created_at=old,
            ),
            WatchTerm(
                keyword="Safe",
                is_active=True,
                notify_on_new=True,
                owner_device_secret="safe-owner",
            ),
            APNSDeviceToken(
                token="s" * 64,
                environment="production",
                device_secret="silent-orphan",
                is_verified=False,
            ),
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                device_secret="safe-owner",
                is_verified=True,
            ),
        ])
        db_session.commit()

        r = client.get("/api/admin/stats")

        assert r.status_code == 200
        terms = {term["keyword"]: term for term in r.json()["watch_terms"]}
        health = r.json()["notification_health"]
        assert health["healthy"] is False
        assert health["active_notify_terms"] == 2
        assert health["active_silent_orphan_terms"] == 1
        assert health["active_notify_terms_without_verified_devices"] == 1
        assert health["orphaned_notification_grace_minutes"] == 60
        assert health["active_silent_orphan_term_ids"] == [terms["Silent"]["id"]]
        assert health["active_notify_term_ids_without_verified_devices"] == [terms["Notify"]["id"]]

        poller_health = client.get("/api/admin/poller-health").json()["notification_health"]
        assert poller_health["healthy"] is False
        assert poller_health["active_silent_orphan_term_ids"] == [terms["Silent"]["id"]]

    def test_poller_health_ignores_inactive_notification_terms(self, client, db_session):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        db_session.add_all([
            WatchTerm(
                keyword="Inactive notify",
                is_active=False,
                notify_on_new=True,
                owner_device_secret="inactive-notify",
                created_at=old,
            ),
            WatchTerm(
                keyword="Inactive silent",
                is_active=False,
                notify_on_new=False,
                owner_device_secret="inactive-silent",
                created_at=old,
            ),
        ])
        db_session.commit()

        health = client.get("/api/admin/poller-health").json()["notification_health"]

        assert health["healthy"] is True
        assert health["active_notify_terms"] == 0
        assert health["active_silent_orphan_terms"] == 0
        assert health["active_notify_terms_without_verified_devices"] == 0
        assert health["active_silent_orphan_term_ids"] == []
        assert health["active_notify_term_ids_without_verified_devices"] == []

    def test_stats_keeps_recent_owner_scoped_terms_in_grace_period(self, client, db_session):
        db_session.add_all([
            WatchTerm(
                keyword="Recent silent",
                is_active=True,
                notify_on_new=False,
                owner_device_secret="recent-silent-orphan",
                created_at=datetime.now(timezone.utc),
            ),
            WatchTerm(
                keyword="Recent notify",
                is_active=True,
                notify_on_new=True,
                owner_device_secret="recent-notify-orphan",
                created_at=datetime.now(timezone.utc),
            ),
        ])
        db_session.commit()

        r = client.get("/api/admin/stats")

        assert r.status_code == 200
        health = r.json()["notification_health"]
        assert health["healthy"] is True
        assert health["active_notify_terms"] == 1
        assert health["active_silent_orphan_terms"] == 0
        assert health["active_notify_terms_without_verified_devices"] == 0
        assert health["active_silent_orphan_term_ids"] == []
        assert health["active_notify_term_ids_without_verified_devices"] == []

    def test_stats_flags_global_notify_terms_without_verified_devices_immediately(self, client, db_session):
        term = WatchTerm(
            keyword="Global notify",
            is_active=True,
            notify_on_new=True,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(term)
        db_session.commit()

        stats_health = client.get("/api/admin/stats").json()["notification_health"]
        poller_health = client.get("/api/admin/poller-health").json()["notification_health"]

        assert stats_health["healthy"] is False
        assert stats_health["active_notify_term_ids_without_verified_devices"] == [term.id]
        assert poller_health["healthy"] is False
        assert poller_health["active_notify_term_ids_without_verified_devices"] == [term.id]

    def test_poller_health_reports_all_notify_term_ids_without_verified_devices(self, client, db_session):
        terms = [
            WatchTerm(
                keyword=f"Notify {index}",
                is_active=True,
                notify_on_new=True,
                owner_device_secret=f"missing-device-{index}",
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
            for index in range(25)
        ]
        db_session.add_all(terms)
        db_session.commit()
        expected_ids = [term.id for term in terms]

        payload = client.get("/api/admin/poller-health").json()
        health = payload["notification_health"]

        assert health["active_notify_terms_without_verified_devices"] == 25
        assert health["active_notify_term_ids_without_verified_devices"] == expected_ids

    def test_poller_health_requires_verified_device_for_server_apns_environment(self, client, db_session):
        term = WatchTerm(
            keyword="Sandbox only",
            is_active=True,
            notify_on_new=True,
            owner_device_secret="sandbox-only",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db_session.add_all([
            term,
            APNSDeviceToken(
                token="b" * 64,
                environment="sandbox",
                device_secret="sandbox-only",
                is_verified=True,
            ),
        ])
        db_session.commit()

        payload = client.get("/api/admin/poller-health").json()
        terms = {row["keyword"]: row for row in payload["watch_terms"]}
        health = payload["notification_health"]

        assert terms["Sandbox only"]["notification_verified_devices"] == 1
        assert terms["Sandbox only"]["notification_verified_devices_for_server_environment"] == 0
        assert health["healthy"] is False
        assert health["active_notify_terms_without_verified_devices"] == 1
        assert health["active_notify_term_ids_without_verified_devices"] == [term.id]

    def test_stats_includes_recent_backend_events(self, client, db_session):
        db_session.add(BackendEvent(
            kind="apns",
            status="attempted",
            message="APNs notification attempted",
            payload={"new_count": 2, "device_count": 1},
        ))
        db_session.commit()

        r = client.get("/api/admin/stats")

        assert r.status_code == 200
        event = r.json()["recent_events"][0]
        assert event["kind"] == "apns"
        assert event["status"] == "attempted"
        assert event["payload"] == {"new_count": 2, "device_count": 1}

    def test_stats_includes_latest_poll_events_outside_recent_window(self, client, db_session):
        completed = BackendEvent(
            kind="poll",
            status="completed",
            message="Scheduled/backend poll completed",
            payload={"new_matches": 1},
        )
        db_session.add(completed)
        db_session.flush()
        for index in range(25):
            db_session.add(BackendEvent(
                kind="apns",
                status="attempted",
                message="APNs notification attempted",
                payload={"index": index},
            ))
        db_session.commit()

        r = client.get("/api/admin/stats")

        assert r.status_code == 200
        data = r.json()
        assert data["latest_successful_poll"]["id"] == completed.id
        assert all(event["kind"] == "apns" for event in data["recent_events"])

    def test_stats_includes_latest_apns_outside_recent_window(self, client, db_session):
        apns = BackendEvent(
            kind="apns",
            status="attempted",
            message="APNs notification attempted",
            payload={"delivered_count": 1},
        )
        db_session.add(apns)
        db_session.flush()
        for index in range(25):
            db_session.add(BackendEvent(
                kind="poll",
                status="completed",
                message="Scheduled/backend poll completed",
                payload={"index": index},
            ))
        db_session.commit()

        r = client.get("/api/admin/stats")

        assert r.status_code == 200
        data = r.json()
        assert data["latest_apns"]["id"] == apns.id
        assert data["latest_relevant_apns"]["id"] == apns.id
        assert data["latest_apns"]["payload"] == {"delivered_count": 1}
        assert all(event["kind"] == "poll" for event in data["recent_events"])

    def test_stats_ignores_latest_apns_for_inactive_term_when_relevance_checked(self, client, db_session):
        active = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        inactive = WatchTerm(keyword="Aiko", is_active=False, notify_on_new=False)
        db_session.add_all([active, inactive])
        db_session.flush()
        relevant = BackendEvent(
            kind="apns",
            status="attempted",
            message="APNs notification attempted",
            payload={"term_id": active.id, "delivered_count": 1},
        )
        stale = BackendEvent(
            kind="apns",
            status="skipped",
            message="Watch term notifications are disabled",
            payload={"term_id": inactive.id, "new_count": 1},
        )
        db_session.add_all([relevant, stale])
        db_session.commit()

        r = client.get("/api/admin/stats")

        assert r.status_code == 200
        data = r.json()
        assert data["latest_apns"]["id"] == stale.id
        assert data["latest_relevant_apns"]["id"] == relevant.id
        assert data["latest_relevant_apns"]["payload"] == {
            "term_id": active.id,
            "delivered_count": 1,
        }

    def test_stats_returns_no_relevant_apns_when_only_inactive_term_events_exist(self, client, db_session):
        inactive = WatchTerm(keyword="Aiko", is_active=False, notify_on_new=False)
        db_session.add(inactive)
        db_session.flush()
        stale = BackendEvent(
            kind="apns",
            status="skipped",
            message="Watch term notifications are disabled",
            payload={"term_id": inactive.id, "new_count": 1},
        )
        db_session.add(stale)
        db_session.commit()

        r = client.get("/api/admin/stats")

        assert r.status_code == 200
        data = r.json()
        assert data["latest_apns"]["id"] == stale.id
        assert data["latest_relevant_apns"] is None

    def test_stats_ignores_latest_apns_for_muted_active_term(self, client, db_session):
        notifying = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        muted = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=False)
        db_session.add_all([notifying, muted])
        db_session.flush()
        relevant = BackendEvent(
            kind="apns",
            status="attempted",
            message="APNs notification attempted",
            payload={"term_id": notifying.id, "delivered_count": 1},
        )
        skipped = BackendEvent(
            kind="apns",
            status="skipped",
            message="Watch term notifications are disabled",
            payload={"term_id": muted.id, "new_count": 1},
        )
        db_session.add_all([relevant, skipped])
        db_session.commit()

        r = client.get("/api/admin/stats")

        assert r.status_code == 200
        data = r.json()
        assert data["latest_apns"]["id"] == skipped.id
        assert data["latest_relevant_apns"]["id"] == relevant.id

    def test_stats_includes_pending_notifications(self, client, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True, owner_device_secret="owner-secret")
        db_session.add(term)
        db_session.flush()
        db_session.add(PendingNotification(watch_term_id=term.id, new_count=3))
        db_session.commit()

        r = client.get("/api/admin/stats")

        assert r.status_code == 200
        pending = r.json()["pending_notifications"]
        assert pending == [{
            "watch_term_id": term.id,
            "keyword": "Aiko",
            "new_count": 3,
            "updated_at": pending[0]["updated_at"],
            "notify_on_new": True,
            "owner_scoped": True,
        }]


class TestAdminMaintenance:
    def test_prune_storage_caps_matches_muted_items_and_backend_events(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.flush()
        now = datetime.now(timezone.utc)

        for platform in ["youtube", "5ch"]:
            for index in range(3):
                item = SourceItem(
                    id=f"{platform}:{index}",
                    platform=platform,
                    item_id=str(index),
                    url=f"https://example.com/{platform}/{index}",
                    published_at=now - timedelta(days=index),
                )
                db_session.add(item)
                db_session.flush()
                db_session.add(Match(watch_term_id=term.id, source_item_id=item.id))

        for index in range(2):
            item = SourceItem(
                id=f"muted:{index}",
                platform="youtube",
                item_id=f"muted-{index}",
                url=f"https://example.com/muted/{index}",
                published_at=now - timedelta(days=index),
            )
            db_session.add(item)
            db_session.flush()
            db_session.add(MutedFeedItem(watch_term_id=term.id, source_item_id=item.id))

        for index in range(4):
            db_session.add(BackendEvent(
                kind="poll",
                status="completed",
                message="poll",
                payload={"index": index},
                created_at=now - timedelta(minutes=index),
            ))
        db_session.commit()

        r = client.post(
            "/api/admin/maintenance/prune-storage"
            "?match_per_term_platform_limit=1"
            "&muted_per_term_limit=1"
            "&backend_event_keep=2"
            "&include_discussion_platforms=true"
        )

        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "storage pruned"
        assert data["matches_pruned"] == 4
        assert data["muted_feed_items_pruned"] == 1
        assert data["orphan_source_items_pruned"] == 5
        assert data["backend_events_pruned"] == 2
        assert data["included_discussion_platforms"] is True
        remaining_matches = db_session.query(Match).all()
        remaining_source_ids = {match.source_item_id for match in remaining_matches}
        assert remaining_source_ids == {"youtube:0", "5ch:0"}
        assert db_session.query(MutedFeedItem).count() == 1
        assert db_session.query(SourceItem).count() == 3
        assert db_session.query(BackendEvent).filter_by(kind="maintenance").count() == 1

    def test_prune_storage_deletes_existing_orphan_source_items(self, client, db_session):
        db_session.add(SourceItem(
            id="orphan:standalone",
            platform="youtube",
            item_id="standalone",
            url="https://example.com/orphan",
            published_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        r = client.post("/api/admin/maintenance/prune-storage")

        assert r.status_code == 200
        data = r.json()
        assert data["orphan_source_items_pruned"] == 1
        assert db_session.query(SourceItem).count() == 0
        assert db_session.query(BackendEvent).filter_by(kind="maintenance").count() == 1

    @pytest.mark.parametrize("term_ids", ["", "0", "-1", "abc"])
    def test_orphaned_term_maintenance_rejects_invalid_ids(self, client, term_ids):
        r = client.post(
            "/api/admin/maintenance/orphaned-terms",
            params={"action": "mute-notify", "term_ids": term_ids},
        )

        assert r.status_code == 422

    def test_orphaned_term_maintenance_deduplicates_ids_before_limit(self, client, db_session):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        orphan = WatchTerm(
            keyword="Orphan",
            is_active=True,
            notify_on_new=True,
            owner_device_secret="orphan-secret",
            created_at=old,
        )
        db_session.add(orphan)
        db_session.commit()

        r = client.post(
            "/api/admin/maintenance/orphaned-terms",
            params={"action": "mute-notify", "term_ids": f"{orphan.id},{orphan.id}"},
        )

        assert r.status_code == 200
        assert r.json()["updated_terms"] == [
            {
                "term_id": orphan.id,
                "keyword": "Orphan",
                "is_active": True,
                "notify_on_new": False,
            }
        ]
        assert db_session.query(BackendEvent).filter_by(kind="maintenance").count() == 1

    def test_orphaned_term_maintenance_limits_unique_ids(self, client):
        r = client.post(
            "/api/admin/maintenance/orphaned-terms",
            params={
                "action": "mute-notify",
                "term_ids": ",".join(str(index) for index in range(1, 22)),
            },
        )

        assert r.status_code == 422

    def test_mute_orphaned_notify_terms_requires_current_orphan(self, client, db_session):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        orphan = WatchTerm(
            keyword="Orphan",
            is_active=True,
            notify_on_new=True,
            owner_device_secret="orphan-secret",
            created_at=old,
        )
        global_term = WatchTerm(
            keyword="Global",
            is_active=True,
            notify_on_new=True,
            created_at=old,
        )
        verified = WatchTerm(
            keyword="Verified",
            is_active=True,
            notify_on_new=True,
            owner_device_secret="verified-secret",
            created_at=old,
        )
        db_session.add_all([
            orphan,
            global_term,
            verified,
            APNSDeviceToken(
                token="v" * 64,
                environment="production",
                device_secret="verified-secret",
                is_verified=True,
            ),
        ])
        db_session.flush()
        db_session.add(PendingNotification(watch_term_id=orphan.id, new_count=3))
        db_session.commit()

        rejected = client.post(
            "/api/admin/maintenance/orphaned-terms",
            params={
                "action": "mute-notify",
                "term_ids": f"{orphan.id},{global_term.id},{verified.id}",
            },
        )

        assert rejected.status_code == 409
        db_session.refresh(orphan)
        assert orphan.notify_on_new is True
        assert db_session.get(PendingNotification, orphan.id) is not None
        assert {row["reason"] for row in rejected.json()["detail"]["failures"]} == {
            "not_owner_scoped",
            "has_verified_apns_device",
        }

        accepted = client.post(
            "/api/admin/maintenance/orphaned-terms",
            params={"action": "mute-notify", "term_ids": str(orphan.id)},
        )

        assert accepted.status_code == 200
        db_session.refresh(orphan)
        assert orphan.is_active is True
        assert orphan.notify_on_new is False
        assert db_session.get(PendingNotification, orphan.id) is None
        event = db_session.query(BackendEvent).filter_by(kind="maintenance").one()
        assert event.payload["action"] == "mute-notify"
        assert event.payload["term_ids"] == [orphan.id]

    def test_orphaned_term_maintenance_requires_verified_device_for_server_apns_environment(
        self,
        client,
        db_session,
    ):
        original_apns_use_sandbox = settings.apns_use_sandbox
        settings.apns_use_sandbox = False
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        term = WatchTerm(
            keyword="Sandbox only",
            is_active=True,
            notify_on_new=True,
            owner_device_secret="sandbox-secret",
            created_at=old,
        )
        db_session.add_all([
            term,
            APNSDeviceToken(
                token="s" * 64,
                environment="sandbox",
                device_secret="sandbox-secret",
                is_verified=True,
            ),
        ])
        db_session.commit()

        try:
            r = client.post(
                "/api/admin/maintenance/orphaned-terms",
                params={"action": "mute-notify", "term_ids": str(term.id)},
            )
        finally:
            settings.apns_use_sandbox = original_apns_use_sandbox

        assert r.status_code == 200
        db_session.refresh(term)
        assert term.notify_on_new is False

    def test_deactivate_orphaned_terms_requires_silent_orphan(self, client, db_session):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        silent = WatchTerm(
            keyword="Silent",
            is_active=True,
            notify_on_new=False,
            owner_device_secret="silent-secret",
            created_at=old,
        )
        notifying = WatchTerm(
            keyword="Notify",
            is_active=True,
            notify_on_new=True,
            owner_device_secret="notify-secret",
            created_at=old,
        )
        db_session.add_all([silent, notifying])
        db_session.flush()
        db_session.add(PendingNotification(watch_term_id=silent.id, new_count=1))
        db_session.commit()

        rejected = client.post(
            "/api/admin/maintenance/orphaned-terms",
            params={"action": "deactivate", "term_ids": str(notifying.id)},
        )

        assert rejected.status_code == 409
        assert rejected.json()["detail"]["failures"] == [
            {"term_id": notifying.id, "reason": "not_silent"}
        ]

        accepted = client.post(
            "/api/admin/maintenance/orphaned-terms",
            params={"action": "deactivate", "term_ids": str(silent.id)},
        )

        assert accepted.status_code == 200
        db_session.refresh(silent)
        assert silent.is_active is False
        assert silent.notify_on_new is False
        assert db_session.get(PendingNotification, silent.id) is None

    def test_prune_storage_returns_500_when_core_prune_fails(self, client, db_session):
        with patch(
            "app.ingestion.scheduler._prune_old_items_with_limit",
            side_effect=RuntimeError("storage prune failed"),
        ):
            r = client.post("/api/admin/maintenance/prune-storage")

        assert r.status_code == 500
        assert db_session.query(BackendEvent).filter_by(kind="maintenance").count() == 0

    def test_prune_storage_returns_500_when_backend_event_prune_fails(self, client, db_session):
        with patch(
            "app.ingestion.scheduler.prune_backend_events",
            side_effect=RuntimeError("backend event prune failed"),
        ):
            r = client.post("/api/admin/maintenance/prune-storage")

        assert r.status_code == 500
        assert db_session.query(BackendEvent).filter_by(kind="maintenance").count() == 0

    def test_purge_youtube_bad_dates_invokes_youtube_cleanup(self, client, db_session):
        with patch("app.main.run_youtube_bad_date_cleanup", return_value=True) as cleanup:
            r = client.post("/api/admin/maintenance/purge-youtube-bad-dates")

        assert r.status_code == 200
        assert r.json() == {"status": "youtube bad-date cleanup completed", "ran": True}
        cleanup.assert_called_once()
        event = db_session.query(BackendEvent).filter_by(kind="maintenance").one()
        assert event.status == "completed"
        assert event.message == "YouTube bad-date cleanup completed"
        assert event.payload == {"migration": "purge_youtube_fetch_time_dates_v2", "ran": True}

    def test_purge_youtube_bad_dates_returns_500_when_cleanup_fails(self, client, db_session):
        with patch("app.main.run_youtube_bad_date_cleanup", side_effect=RuntimeError("cleanup failed")):
            r = client.post("/api/admin/maintenance/purge-youtube-bad-dates")

        assert r.status_code == 500
        assert db_session.query(BackendEvent).filter_by(kind="maintenance").count() == 0

    def test_purge_youtube_google_news_invokes_forced_cleanup(self, client, db_session):
        with patch("app.main.force_youtube_google_news_cleanup", return_value=3) as cleanup:
            r = client.post("/api/admin/maintenance/purge-youtube-google-news")

        assert r.status_code == 200
        assert r.json() == {
            "status": "youtube google-news cleanup completed",
            "purged_count": 3,
        }
        cleanup.assert_called_once()
        event = db_session.query(BackendEvent).filter_by(kind="maintenance").one()
        assert event.status == "completed"
        assert event.message == "YouTube Google News cleanup completed"
        assert event.payload == {"purged_count": 3}

    def test_purge_youtube_google_news_returns_500_when_cleanup_fails(self, client, db_session):
        with patch("app.main.force_youtube_google_news_cleanup", side_effect=RuntimeError("cleanup failed")):
            r = client.post("/api/admin/maintenance/purge-youtube-google-news")

        assert r.status_code == 500
        assert db_session.query(BackendEvent).filter_by(kind="maintenance").count() == 0


class TestAdminPoll:
    def test_poll_runs_synchronously_and_reports_completed(self, client):
        # The endpoint awaits the poll (so Render keeps the instance alive for the
        # whole run) rather than firing it as a background task.
        mock_lock = MagicMock()
        mock_lock.locked.return_value = False
        async def noop_poll():
            return None

        created_task = None

        def create_task():
            nonlocal created_task
            created_task = asyncio.create_task(noop_poll())
            return created_task

        with patch("app.main.create_poll_task", side_effect=create_task) as mock_create_poll_task, \
             patch("app.main._poll_lock", mock_lock):
            r = client.post("/api/admin/poll")
        assert r.status_code == 200
        assert r.json() == {"status": "poll completed"}
        mock_create_poll_task.assert_called_once()
        assert created_task is not None and created_task.done()

    def test_poll_uses_cloudflare_compatible_completion_budget(self, client):
        mock_lock = MagicMock()
        mock_lock.locked.return_value = False
        observed_timeout = None

        async def capture_timeout(awaitable, timeout):
            nonlocal observed_timeout
            observed_timeout = timeout
            await awaitable

        async def noop_poll():
            return None

        with patch("app.main.create_poll_task", side_effect=lambda: asyncio.create_task(noop_poll())), \
             patch("app.main._poll_lock", mock_lock), \
             patch("app.main.asyncio.wait_for", new=capture_timeout):
            r = client.post("/api/admin/poll")

        assert r.status_code == 200
        assert observed_timeout == 210.0

    def test_poll_timeout_does_not_cancel_underlying_task(self, client):
        mock_lock = MagicMock()
        mock_lock.locked.return_value = False

        async def noop_poll():
            return None

        async def timeout_without_canceling(_awaitable, timeout):
            raise asyncio.TimeoutError

        created_tasks = []

        def create_task():
            task = asyncio.create_task(noop_poll())
            created_tasks.append(task)
            return task

        with patch("app.main.create_poll_task", side_effect=create_task), \
             patch("app.main._poll_lock", mock_lock), \
             patch("app.main.asyncio.wait_for", new=timeout_without_canceling):
            r = client.post("/api/admin/poll")

        assert r.status_code == 200
        assert r.json() == {"status": "poll still running (request timed out)"}
        assert created_tasks
        assert not created_tasks[0].cancelled()

    def test_poll_returns_already_running_when_busy(self, client):
        mock_lock = MagicMock()
        mock_lock.locked.return_value = True
        with patch("app.main._poll_lock", mock_lock):
            r = client.post("/api/admin/poll")
        assert r.status_code == 200
        assert r.json() == {"status": "poll already running"}


class TestAdminNotificationCanary:
    def test_canary_sends_synthetic_new_match_notification(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        db_session.add_all([
            term,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                is_verified=True,
            ),
        ])
        db_session.commit()

        async def fake_send(db, sent_term, count, preview):
            db.add(BackendEvent(
                kind="apns",
                status="attempted",
                message="APNs notification attempted",
                payload={
                    "term_id": sent_term.id,
                    "keyword": sent_term.keyword,
                    "new_count": count,
                    "device_count": 1,
                    "preview_item_id": preview["id"],
                    "delivered_count": 1,
                    "retryable_failures": 0,
                    "pruned_tokens": 0,
                },
            ))
            db.commit()
            return True

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=AsyncMock(side_effect=fake_send)) as mock_send:
            r = client.post("/api/admin/notification-canary")

        assert r.status_code == 200
        body = r.json()
        assert body["term_id"] == term.id
        assert body["keyword"] == "Aiko"
        assert body["owner_scoped"] is False
        assert body["delivered"] is True
        assert body["should_clear"] is True
        assert body["apns_event"]["kind"] == "apns"
        assert body["apns_event"]["payload"]["delivered_count"] == 1
        assert isinstance(body["apns_event"]["created_at"], str)
        mock_send.assert_awaited_once()
        _, sent_term, count, preview = mock_send.await_args.args
        assert sent_term.id == term.id
        assert count == 1
        assert preview["id"].startswith("oshireader:canary:")
        assert preview["title"] == "OshiReader notification canary"

        event = db_session.query(BackendEvent).filter_by(
            kind="notification_canary",
            status="passed",
        ).one()
        assert event.payload["term_id"] == term.id
        assert event.payload["delivered"] is True
        assert event.payload["should_clear"] is True
        assert isinstance(event.payload["apns_event"]["created_at"], str)

    def test_canary_requires_verified_device_for_server_apns_environment(self, client, db_session):
        term = WatchTerm(keyword="Sandbox only", is_active=True, notify_on_new=True)
        db_session.add_all([
            term,
            APNSDeviceToken(
                token="a" * 64,
                environment="sandbox",
                is_verified=True,
            ),
        ])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=AsyncMock()) as mock_send:
            r = client.post("/api/admin/notification-canary")

        assert r.status_code == 503
        assert r.json()["detail"] == "No active notification term has a verified APNs device"
        mock_send.assert_not_called()

        event = db_session.query(BackendEvent).filter_by(
            kind="notification_canary",
            status="failed",
        ).one()
        assert event.message == "No active notification term has a verified APNs device"

    def test_canary_all_terms_sends_every_active_term_with_verified_device(self, client, db_session):
        owner_secret = "owner-secret"
        global_term = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        owner_term = WatchTerm(
            keyword="Aiko",
            is_active=True,
            notify_on_new=True,
            owner_device_secret=owner_secret,
        )
        silent_term = WatchTerm(keyword="NoNotify", is_active=True, notify_on_new=False)
        db_session.add_all([
            global_term,
            owner_term,
            silent_term,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                is_verified=True,
            ),
            APNSDeviceToken(
                token="b" * 64,
                environment="production",
                is_verified=True,
                device_secret=owner_secret,
            ),
        ])
        db_session.commit()

        async def fake_send(db, sent_term, count, preview):
            db.add(BackendEvent(
                kind="apns",
                status="attempted",
                message="APNs notification attempted",
                payload={
                    "term_id": sent_term.id,
                    "keyword": sent_term.keyword,
                    "new_count": count,
                    "device_count": 1,
                    "preview_item_id": preview["id"],
                    "delivered_count": 1,
                    "retryable_failures": 0,
                    "pruned_tokens": 0,
                },
            ))
            db.commit()
            return True

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=AsyncMock(side_effect=fake_send)) as mock_send:
            r = client.post("/api/admin/notification-canary?all_terms=true")

        assert r.status_code == 200
        body = r.json()
        assert body["all_terms"] is True
        assert body["delivered"] is True
        assert [term["term_id"] for term in body["terms"]] == [global_term.id, owner_term.id]
        assert [term["delivered"] for term in body["terms"]] == [True, True]
        assert mock_send.await_count == 2

        event = db_session.query(BackendEvent).filter_by(
            kind="notification_canary",
            status="passed",
        ).one()
        assert event.payload["all_terms"] is True
        assert [term["term_id"] for term in event.payload["terms"]] == [global_term.id, owner_term.id]

    def test_canary_requires_active_notification_term_with_verified_device(self, client, db_session):
        db_session.add(WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True))
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=AsyncMock()) as mock_send:
            r = client.post("/api/admin/notification-canary")

        assert r.status_code == 503
        mock_send.assert_not_awaited()
        event = db_session.query(BackendEvent).filter_by(
            kind="notification_canary",
            status="failed",
        ).one()
        assert event.message == "No active notification term has a verified APNs device"

    def test_canary_fails_when_delivery_fails(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        db_session.add_all([
            term,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                is_verified=True,
            ),
        ])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=AsyncMock(return_value=False)):
            r = client.post("/api/admin/notification-canary")

        assert r.status_code == 503
        event = db_session.query(BackendEvent).filter_by(
            kind="notification_canary",
            status="failed",
        ).one()
        assert event.payload["term_id"] == term.id
        assert event.payload["delivered"] is False

    def test_canary_ignores_stale_prior_apns_success(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        db_session.add_all([
            term,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                is_verified=True,
            ),
            BackendEvent(
                kind="apns",
                status="attempted",
                message="Older APNs notification attempted",
                payload={
                    "term_id": term.id,
                    "keyword": term.keyword,
                    "delivered_count": 1,
                },
            ),
        ])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=AsyncMock(return_value=False)):
            r = client.post("/api/admin/notification-canary")

        assert r.status_code == 503
        event = db_session.query(BackendEvent).filter_by(
            kind="notification_canary",
            status="failed",
        ).one()
        assert event.payload["term_id"] == term.id
        assert event.payload["delivered"] is False
        assert event.payload["should_clear"] is False
        assert event.payload["apns_event"] is None

    def test_canary_ignores_fresh_apns_event_for_different_term(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        other_term = WatchTerm(keyword="Other", is_active=True, notify_on_new=True)
        db_session.add_all([
            term,
            other_term,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                is_verified=True,
            ),
        ])
        db_session.commit()

        async def fake_send(db, _sent_term, _count, preview):
            db.add(BackendEvent(
                kind="apns",
                status="attempted",
                message="APNs notification attempted",
                payload={
                    "term_id": other_term.id,
                    "keyword": other_term.keyword,
                    "preview_item_id": preview["id"],
                    "delivered_count": 1,
                    "retryable_failures": 0,
                    "terminal_failures": 0,
                    "pruned_tokens": 0,
                },
            ))
            db.commit()
            return True

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=AsyncMock(side_effect=fake_send)):
            r = client.post("/api/admin/notification-canary")

        assert r.status_code == 503
        event = db_session.query(BackendEvent).filter_by(
            kind="notification_canary",
            status="failed",
        ).one()
        assert event.payload["term_id"] == term.id
        assert event.payload["delivered"] is False
        assert event.payload["should_clear"] is True
        assert event.payload["apns_event"] is None

    def test_canary_ignores_same_term_event_for_different_canary_id(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        db_session.add_all([
            term,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                is_verified=True,
            ),
        ])
        db_session.commit()

        async def fake_send(db, sent_term, _count, _preview):
            db.add(BackendEvent(
                kind="apns",
                status="attempted",
                message="APNs notification attempted",
                payload={
                    "term_id": sent_term.id,
                    "keyword": sent_term.keyword,
                    "preview_item_id": "oshireader:canary:other-request",
                    "delivered_count": 1,
                    "retryable_failures": 0,
                    "terminal_failures": 0,
                    "pruned_tokens": 0,
                },
            ))
            db.commit()
            return True

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=AsyncMock(side_effect=fake_send)):
            r = client.post("/api/admin/notification-canary")

        assert r.status_code == 503
        event = db_session.query(BackendEvent).filter_by(
            kind="notification_canary",
            status="failed",
        ).one()
        assert event.payload["term_id"] == term.id
        assert event.payload["delivered"] is False
        assert event.payload["should_clear"] is True
        assert event.payload["apns_event"] is None

    def test_canary_finds_matching_event_after_many_unrelated_events(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        other_term = WatchTerm(keyword="Other", is_active=True, notify_on_new=True)
        db_session.add_all([
            term,
            other_term,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                is_verified=True,
            ),
        ])
        db_session.commit()

        async def fake_send(db, sent_term, count, preview):
            db.add(BackendEvent(
                kind="apns",
                status="attempted",
                message="APNs notification attempted",
                payload={
                    "term_id": sent_term.id,
                    "keyword": sent_term.keyword,
                    "new_count": count,
                    "device_count": 1,
                    "preview_item_id": preview["id"],
                    "delivered_count": 1,
                    "retryable_failures": 0,
                    "terminal_failures": 0,
                    "pruned_tokens": 0,
                },
            ))
            for index in range(60):
                db.add(BackendEvent(
                    kind="apns",
                    status="attempted",
                    message="Unrelated APNs notification attempted",
                    payload={
                        "term_id": other_term.id,
                        "keyword": other_term.keyword,
                        "preview_item_id": f"unrelated-{index}",
                        "delivered_count": 1,
                    },
                ))
            db.commit()
            return True

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=AsyncMock(side_effect=fake_send)):
            r = client.post("/api/admin/notification-canary")

        assert r.status_code == 200
        body = r.json()
        assert body["delivered"] is True
        assert body["should_clear"] is True
        assert body["apns_event"]["payload"]["term_id"] == term.id
        assert body["apns_event"]["payload"]["preview_item_id"].startswith("oshireader:canary:")

    def test_canary_fails_when_outbox_clears_without_delivery(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        db_session.add_all([
            term,
            APNSDeviceToken(
                token="a" * 64,
                environment="production",
                is_verified=True,
            ),
        ])
        db_session.commit()

        async def fake_send(db, sent_term, count, preview):
            db.add(BackendEvent(
                kind="apns",
                status="attempted",
                message="APNs notification attempted",
                payload={
                    "term_id": sent_term.id,
                    "keyword": sent_term.keyword,
                    "new_count": count,
                    "device_count": 1,
                    "preview_item_id": preview["id"],
                    "delivered_count": 0,
                    "retryable_failures": 0,
                    "terminal_failures": 1,
                    "pruned_tokens": 0,
                },
            ))
            db.commit()
            return True

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=AsyncMock(side_effect=fake_send)):
            r = client.post("/api/admin/notification-canary")

        assert r.status_code == 503
        event = db_session.query(BackendEvent).filter_by(
            kind="notification_canary",
            status="failed",
        ).one()
        assert event.payload["term_id"] == term.id
        assert event.payload["delivered"] is False
        assert event.payload["should_clear"] is True


class TestAdminAuth:
    def test_stats_requires_auth_when_token_set(self, client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.admin_api_token = "secret123"
            r = client.get("/api/admin/stats")
        assert r.status_code == 401

    def test_stats_accepts_correct_token(self, client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.admin_api_token = "secret123"
            r = client.get(
                "/api/admin/stats",
                headers={"Authorization": "Bearer secret123"},
            )
        assert r.status_code == 200

    def test_stats_rejects_wrong_token(self, client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.admin_api_token = "secret123"
            r = client.get(
                "/api/admin/stats",
                headers={"Authorization": "Bearer wrongtoken"},
            )
        assert r.status_code == 401

    def test_poll_requires_auth_when_token_set(self, client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.admin_api_token = "secret123"
            r = client.post("/api/admin/poll")
        assert r.status_code == 401

    def test_poll_accepts_correct_token(self, client):
        from unittest.mock import AsyncMock
        mock_lock = MagicMock()
        mock_lock.locked.return_value = False
        with patch("app.auth.settings") as mock_settings, \
             patch("app.main.poll_once", new=AsyncMock()), \
             patch("app.main._poll_lock", mock_lock):
            mock_settings.admin_api_token = "secret123"
            r = client.post(
                "/api/admin/poll",
                headers={"Authorization": "Bearer secret123"},
            )
        assert r.status_code == 200


class TestAdminTestFetch:
    def test_test_fetch_returns_platform_counts(self, client):
        from app.connectors.base import SourceItemCreate

        mock_item = SourceItemCreate(
            platform="youtube", item_id="v1",
            url="https://yt.be/v1",
            published_at=datetime.now(timezone.utc),
            media_type="video",
        )
        mock_connector = MagicMock()
        mock_connector.PLATFORM = "youtube"

        with patch("app.ingestion.scheduler._build_connectors", return_value=[mock_connector]), \
             patch("app.ingestion.scheduler._fetch_one", new=AsyncMock(return_value=[mock_item])):
            r = client.get("/api/admin/test-fetch")

        assert r.status_code == 200
        data = r.json()
        assert "youtube" in data
        assert data["youtube"] == 1

    def test_test_fetch_requires_auth_when_token_set(self, client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.admin_api_token = "secret"
            r = client.get("/api/admin/test-fetch")
        assert r.status_code == 401

    def test_test_fetch_empty_connectors_returns_empty_dict(self, client):
        with patch("app.ingestion.scheduler._build_connectors", return_value=[]), \
             patch("app.ingestion.scheduler._fetch_one", new=AsyncMock(return_value=[])):
            r = client.get("/api/admin/test-fetch")
        assert r.status_code == 200
        assert r.json() == {}

    def test_test_fetch_does_not_count_non_matching_connector_results(self, client):
        from app.connectors.base import SourceItemCreate

        mock_item = SourceItemCreate(
            platform="youtube", item_id="v1",
            url="https://yt.be/v1",
            published_at=datetime.now(timezone.utc),
            media_type="video",
            title="unrelated video",
        )
        mock_connector = MagicMock()
        mock_connector.PLATFORM = "youtube"
        mock_connector.fetch = AsyncMock(return_value=[mock_item])

        with patch("app.ingestion.scheduler._build_connectors", return_value=[mock_connector]):
            r = client.get("/api/admin/test-fetch", params={"keyword": "Aiko"})

        assert r.status_code == 200
        assert r.json() == {"youtube": 0}

    def test_test_fetch_counts_matching_connector_results(self, client):
        from app.connectors.base import SourceItemCreate

        mock_item = SourceItemCreate(
            platform="youtube", item_id="v1",
            url="https://yt.be/v1",
            published_at=datetime.now(timezone.utc),
            media_type="video",
            title="Aiko video",
        )
        mock_connector = MagicMock()
        mock_connector.PLATFORM = "youtube"
        mock_connector.fetch = AsyncMock(return_value=[mock_item])

        with patch("app.ingestion.scheduler._build_connectors", return_value=[mock_connector]):
            r = client.get("/api/admin/test-fetch", params={"keyword": "Aiko"})

        assert r.status_code == 200
        assert r.json() == {"youtube": 1}

    def test_test_fetch_can_filter_one_platform(self, client):
        youtube = MagicMock()
        youtube.PLATFORM = "youtube"
        tver = MagicMock()
        tver.PLATFORM = "tver"

        async def _fake_fetch(connector, *_args):
            return [object()] if connector.PLATFORM == "tver" else []

        with patch("app.ingestion.scheduler._build_connectors", return_value=[youtube, tver]), \
             patch("app.ingestion.scheduler._fetch_one", new=AsyncMock(side_effect=_fake_fetch)):
            r = client.get("/api/admin/test-fetch", params={"platform": "tver"})

        assert r.status_code == 200
        assert r.json() == {"tver": 1}

    def test_test_fetch_can_return_dated_samples(self, client):
        from app.connectors.base import SourceItemCreate

        published = datetime(2026, 6, 17, tzinfo=timezone.utc)
        mock_item = SourceItemCreate(
            platform="tver",
            item_id="ep1",
            url="https://tver.jp/episodes/ep1",
            published_at=published,
            media_type="video",
            title="Aiko episode",
            raw_payload={"date_source": "episode_detail"},
        )
        mock_connector = MagicMock()
        mock_connector.PLATFORM = "tver"

        with patch("app.ingestion.scheduler._build_connectors", return_value=[mock_connector]), \
             patch("app.ingestion.scheduler._fetch_one", new=AsyncMock(return_value=[mock_item])):
            r = client.get(
                "/api/admin/test-fetch",
                params={"keyword": "Aiko", "platform": "tver", "samples": 1},
            )

        assert r.status_code == 200
        assert r.json() == {
            "tver": {
                "count": 1,
                "items": [
                    {
                        "item_id": "ep1",
                        "url": "https://tver.jp/episodes/ep1",
                        "title": "Aiko episode",
                        "media_type": "video",
                        "published_at": "2026-06-17T00:00:00+00:00",
                        "raw_payload": {"date_source": "episode_detail"},
                    }
                ],
            }
        }

    def test_test_fetch_custom_timeout_fetches_and_filters_samples(self, client):
        from app.connectors.base import SourceItemCreate

        matching = SourceItemCreate(
            platform="news",
            item_id="n1",
            url="https://example.com/n1",
            published_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
            media_type="article",
            title="Aiko fresh article",
        )
        unrelated = SourceItemCreate(
            platform="news",
            item_id="n2",
            url="https://example.com/n2",
            published_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
            media_type="article",
            title="unrelated article",
        )
        mock_connector = MagicMock()
        mock_connector.PLATFORM = "news"
        mock_connector.fetch = AsyncMock(return_value=[matching, unrelated])

        with patch("app.ingestion.scheduler._build_connectors", return_value=[mock_connector]), \
             patch("app.ingestion.scheduler._fetch_one", new=AsyncMock()) as default_fetch:
            r = client.get(
                "/api/admin/test-fetch",
                params={
                    "keyword": "Aiko",
                    "platform": "news",
                    "samples": 1,
                    "timeout_seconds": 30,
                },
            )

        assert r.status_code == 200
        default_fetch.assert_not_awaited()
        assert r.json()["news"]["count"] == 1
        assert r.json()["news"]["items"][0]["item_id"] == "n1"


class TestAdminSourceProbe:
    def test_source_probe_rejects_unsupported_platform(self, client):
        r = client.get("/api/admin/source-probe", params={"platform": "unknown"})
        assert r.status_code == 404

    def test_source_probe_returns_direct_and_jina_counts(self, client):
        direct_resp = MagicMock()
        direct_resp.status_code = 200
        direct_resp.content = b"<rss />"
        jina_resp = MagicMock()
        jina_resp.status_code = 200
        jina_resp.text = """### [Aiko fresh article](https://news.google.com/rss/articles/abc123)

[Aiko fresh article](https://news.google.com/rss/articles/abc123)

Wed, 24 Jun 2026 02:07:03 GMT
"""
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=[direct_resp, jina_resp])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        fake_feed = MagicMock()
        fake_feed.entries = [{"title": "Aiko direct article"}]

        with patch("app.main.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.main.feedparser.parse", return_value=fake_feed):
            r = client.get(
                "/api/admin/source-probe",
                params={"platform": "news", "keyword": "Aiko"},
            )

        assert r.status_code == 200
        data = r.json()
        assert data["direct"]["entries"] == 1
        assert data["direct"]["keyword_title_matches"] == 1
        assert data["jina"]["entries"] == 1
        assert data["jina"]["keyword_title_matches"] == 1

    def test_source_probe_supports_thetv(self, client):
        direct_resp = MagicMock()
        direct_resp.status_code = 200
        direct_resp.content = b"<rss />"
        jina_resp = MagicMock()
        jina_resp.status_code = 200
        jina_resp.text = ""
        bing_resp = MagicMock()
        bing_resp.status_code = 200
        bing_resp.content = b"<rss />"
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=[direct_resp, jina_resp, bing_resp])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        fake_feed = MagicMock()
        fake_feed.entries = []

        with patch("app.main.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.main.feedparser.parse", return_value=fake_feed):
            r = client.get(
                "/api/admin/source-probe",
                params={"platform": "thetv", "keyword": "Aiko"},
            )

        assert r.status_code == 200
        assert r.json()["query"] == "Aiko site:thetv.jp"

    @pytest.mark.parametrize(
        ("platform", "expected_query"),
        [
            ("natalie", "Aiko site:natalie.mu"),
            ("billboardjapan", "Aiko site:billboard-japan.com"),
            ("soompi", "Aiko site:soompi.com"),
            ("allkpop", "Aiko site:allkpop.com"),
            ("kpopofficial", "Aiko site:kpopofficial.com"),
        ],
    )
    def test_source_probe_supports_added_artist_sources(self, client, platform, expected_query):
        direct_resp = MagicMock()
        direct_resp.status_code = 200
        direct_resp.content = b"<rss />"
        jina_resp = MagicMock()
        jina_resp.status_code = 200
        jina_resp.text = ""
        bing_resp = MagicMock()
        bing_resp.status_code = 200
        bing_resp.content = b"<rss />"
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=[direct_resp, jina_resp, bing_resp])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        fake_feed = MagicMock()
        fake_feed.entries = []

        with patch("app.main.httpx.AsyncClient", MagicMock(return_value=ctx)), \
             patch("app.main.fetch_search_rss_via_proxy", new=AsyncMock(return_value=None)), \
             patch("app.main.feedparser.parse", return_value=fake_feed):
            r = client.get(
                "/api/admin/source-probe",
                params={"platform": platform, "keyword": "Aiko"},
            )

        assert r.status_code == 200
        assert r.json()["query"] == expected_query

    def test_source_probe_uses_english_locale_for_kpop_sources(self, client):
        direct_resp = MagicMock()
        direct_resp.status_code = 200
        direct_resp.content = b"<rss />"
        jina_resp = MagicMock()
        jina_resp.status_code = 200
        jina_resp.text = ""
        bing_resp = MagicMock()
        bing_resp.status_code = 200
        bing_resp.content = b"<rss />"
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(side_effect=[direct_resp, jina_resp, bing_resp])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        fake_feed = MagicMock()
        fake_feed.entries = []
        proxy_fetch = AsyncMock(return_value=None)
        async_client_cls = MagicMock(return_value=ctx)

        with patch("app.main.httpx.AsyncClient", async_client_cls), \
             patch("app.main.fetch_search_rss_via_proxy", new=proxy_fetch), \
             patch("app.main.feedparser.parse", return_value=fake_feed):
            r = client.get(
                "/api/admin/source-probe",
                params={"platform": "soompi", "keyword": "BTS"},
            )

        assert r.status_code == 200
        assert async_client_cls.call_args.kwargs["headers"]["Accept-Language"] == "en,ko;q=0.9,ja;q=0.7"
        direct_url = client_mock.get.await_args_list[0].args[0]
        bing_url = client_mock.get.await_args_list[2].args[0]
        assert "hl=en" in direct_url
        assert "gl=US" in direct_url
        assert "ceid=US%3Aen" in direct_url
        assert "mkt=en-US" in bing_url
        proxy_fetch.assert_any_await(
            "BTS site:soompi.com",
            target="google",
            hl="en",
            gl="US",
            ceid="US:en",
            mkt=None,
            accept_language="en,ko;q=0.9,ja;q=0.7",
        )
        proxy_fetch.assert_any_await(
            "BTS site:soompi.com",
            target="bing",
            hl=None,
            gl=None,
            ceid=None,
            mkt="en-US",
            accept_language="en,ko;q=0.9,ja;q=0.7",
        )


class TestAdminTwitterProbe:
    def test_twitter_probe_reports_no_configured_bearer(self, client):
        with patch("app.main.settings") as mock_settings:
            mock_settings.twitter_bearer_token = ""
            r = client.get("/api/admin/twitter-probe", params={"keyword": "Aiko"})

        assert r.status_code == 200
        data = r.json()
        assert data["env_has_bearer_token"] is False
        assert data["db_has_bearer_token"] is False
        assert data["selected_bearer_source"] is None
        assert data["api"]["attempted"] is False

    def test_twitter_probe_reports_api_status_without_token(self, client):
        resp = MagicMock()
        resp.status_code = 403
        resp.is_success = False
        resp.json.return_value = {
            "errors": [{
                "title": "Unsupported Authentication",
                "detail": "Unsupported Authentication",
            }]
        }
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.main.settings") as mock_settings, \
             patch("app.main.httpx.AsyncClient", MagicMock(return_value=ctx)):
            mock_settings.twitter_bearer_token = "env-token"
            r = client.get("/api/admin/twitter-probe", params={"keyword": "Aiko"})

        assert r.status_code == 200
        data = r.json()
        assert data["env_has_bearer_token"] is True
        assert data["selected_bearer_source"] == "env"
        assert data["api"]["attempted"] is True
        assert data["api"]["status"] == 403
        assert data["api"]["error_title"] == "Unsupported Authentication"
