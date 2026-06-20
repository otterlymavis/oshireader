from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import get_db
from app.models import APNSDeviceToken, BackendEvent, Match, SourceItem, WatchTerm


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
        assert r.json() == {"status": "ok"}

    def test_health_accepts_head_checks(self, client):
        r = client.head("/api/health")
        assert r.status_code == 200
        assert r.content == b""


class TestNotificationPreviewImage:
    def test_returns_cacheable_png(self, client):
        r = client.get("/api/notification-preview.png")

        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.headers["cache-control"] == "public, max-age=86400"
        assert r.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(r.content) > 1_000


class TestClientDiagnostics:
    def test_client_diagnostics_accepts_refresh_report(self, client, caplog):
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


class TestAdminPoll:
    def test_poll_runs_synchronously_and_reports_completed(self, client):
        # The endpoint awaits the poll (so Render keeps the instance alive for the
        # whole run) rather than firing it as a background task.
        from unittest.mock import AsyncMock
        mock_lock = MagicMock()
        mock_lock.locked.return_value = False
        with patch("app.main.poll_once", new=AsyncMock()) as mock_poll, \
             patch("app.main._poll_lock", mock_lock):
            r = client.post("/api/admin/poll")
        assert r.status_code == 200
        assert r.json() == {"status": "poll completed"}
        mock_poll.assert_awaited_once()

    def test_poll_uses_cloudflare_compatible_completion_budget(self, client):
        from unittest.mock import AsyncMock
        mock_lock = MagicMock()
        mock_lock.locked.return_value = False
        observed_timeout = None

        async def capture_timeout(awaitable, timeout):
            nonlocal observed_timeout
            observed_timeout = timeout
            await awaitable

        with patch("app.main.poll_once", new=AsyncMock()), \
             patch("app.main._poll_lock", mock_lock), \
             patch("app.main.asyncio.wait_for", new=capture_timeout):
            r = client.post("/api/admin/poll")

        assert r.status_code == 200
        assert observed_timeout == 210.0

    def test_poll_returns_already_running_when_busy(self, client):
        mock_lock = MagicMock()
        mock_lock.locked.return_value = True
        with patch("app.main._poll_lock", mock_lock):
            r = client.post("/api/admin/poll")
        assert r.status_code == 200
        assert r.json() == {"status": "poll already running"}


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
