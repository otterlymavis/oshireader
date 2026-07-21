import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.main import database_operational_error_handler
from app.models import APNSDeviceToken, BackendEvent, Match, PendingNotification, SourceItem, WatchTerm


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


class TestNotificationPreviewImage:
    def test_returns_cacheable_png(self, client):
        r = client.get("/api/notification-preview.png")

        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.headers["cache-control"] == "public, max-age=86400"
        assert r.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(r.content) > 1_000


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
