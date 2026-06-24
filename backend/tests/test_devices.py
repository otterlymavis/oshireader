import asyncio

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.api import devices as devices_api
from app.models import APNSDeviceToken, WatchTerm

_DEVICE_SECRET = "device-secret-123"


def _registration(token: str, environment: str = "sandbox", **extra):
    return {
        "token": token,
        "environment": environment,
        "device_secret": _DEVICE_SECRET,
        **extra,
    }


class TestAPNSTokenUpsert:
    def test_upsert_new_token_returns_201(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json=_registration("a" * 64),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["token"] == "a" * 64
        assert data["environment"] == "sandbox"

    def test_upsert_normalizes_token(self, client):
        raw = "  " + "AB" * 32 + "  "
        r = client.post(
            "/api/devices/apns-token",
            json=_registration(raw),
        )
        assert r.status_code == 201
        assert r.json()["token"] == "ab" * 32

    def test_upsert_updates_existing_token(self, client):
        token = "b" * 64
        client.post("/api/devices/apns-token", json=_registration(token))
        r = client.post(
            "/api/devices/apns-token",
            json=_registration(token, "production", device_id="dev-xyz"),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["environment"] == "production"
        assert data["device_id"] == "dev-xyz"
        assert "device_secret" not in data

    def test_upsert_stores_device_secret_as_hash(self, client, db_session):
        token = "b" * 64
        client.post("/api/devices/apns-token", json=_registration(token))
        stored = db_session.get(APNSDeviceToken, token)
        assert stored.device_secret != _DEVICE_SECRET
        assert len(stored.device_secret) == 64

    def test_upsert_rejects_wrong_secret_for_existing_token(self, client):
        token = "b" * 64
        client.post("/api/devices/apns-token", json=_registration(token))
        r = client.post(
            "/api/devices/apns-token",
            json=_registration(token, "production", device_secret="different-secret-123"),
        )
        assert r.status_code == 409

    def test_upsert_allows_secret_rotation_for_same_device_identity(self, client, db_session):
        token = "b" * 64
        client.post(
            "/api/devices/apns-token",
            json=_registration(token, device_id="device-xyz"),
        )

        r = client.post(
            "/api/devices/apns-token",
            json=_registration(token, device_id="device-xyz", device_secret="new-device-secret-123"),
        )

        assert r.status_code == 201
        stored = db_session.get(APNSDeviceToken, token)
        assert stored.device_id == "device-xyz"
        assert devices_api._secret_matches(stored.device_secret, "new-device-secret-123")

    def test_upsert_rejects_secret_rotation_for_different_device_identity(self, client):
        token = "b" * 64
        client.post(
            "/api/devices/apns-token",
            json=_registration(token, device_id="device-xyz"),
        )

        r = client.post(
            "/api/devices/apns-token",
            json=_registration(token, device_id="other-device", device_secret="new-device-secret-123"),
        )

        assert r.status_code == 409

    def test_upsert_retires_older_token_for_same_device_identity(self, client, db_session):
        older_token = "b" * 64
        newer_token = "c" * 64
        client.post(
            "/api/devices/apns-token",
            json=_registration(older_token, "production", device_id="device-xyz"),
        )

        r = client.post(
            "/api/devices/apns-token",
            json=_registration(newer_token, "production", device_id="device-xyz"),
        )

        assert r.status_code == 201
        assert db_session.get(APNSDeviceToken, older_token) is None
        assert db_session.get(APNSDeviceToken, newer_token) is not None

    def test_upsert_keeps_token_only_registrations_without_device_id(self, client, db_session):
        older_token = "b" * 64
        newer_token = "c" * 64
        client.post("/api/devices/apns-token", json=_registration(older_token, "production"))

        r = client.post("/api/devices/apns-token", json=_registration(newer_token, "production"))

        assert r.status_code == 201
        assert db_session.get(APNSDeviceToken, older_token) is not None
        assert db_session.get(APNSDeviceToken, newer_token) is not None

    def test_upsert_requires_device_secret(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json={"token": "b" * 64, "environment": "sandbox"},
        )
        assert r.status_code == 422

    def test_upsert_removes_internal_spaces(self, client):
        # _normalize_token uses split() which removes ALL whitespace including internal spaces
        raw = " ".join(["ab"] * 32)  # "ab ab ab ... ab" — 64 hex chars with spaces
        r = client.post(
            "/api/devices/apns-token",
            json=_registration(raw),
        )
        assert r.status_code == 201
        assert r.json()["token"] == "ab" * 32

    def test_upsert_invalid_token_returns_400(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json=_registration("not-hex!!"),
        )
        assert r.status_code == 400

    def test_upsert_empty_token_returns_400(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json=_registration("   "),
        )
        assert r.status_code == 400

    def test_upsert_short_token_returns_400(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json=_registration("a" * 32),
        )
        assert r.status_code == 400

    def test_upsert_long_token_returns_400(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json=_registration("a" * 65),
        )
        assert r.status_code == 400

    def test_last_seen_at_is_utc_aware(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json=_registration("c" * 64),
        )
        assert r.status_code == 201
        last_seen = r.json()["last_seen_at"]
        assert last_seen is not None
        assert last_seen.endswith("Z") or "+" in last_seen


class TestAPNSTokenDelete:
    def test_delete_existing_token_returns_204(self, client):
        token = "d" * 64
        client.post("/api/devices/apns-token", json=_registration(token))
        r = client.delete(
            f"/api/devices/apns-token/{token}",
            headers={"X-Device-Secret": _DEVICE_SECRET},
        )
        assert r.status_code == 204

    def test_delete_nonexistent_token_returns_404(self, client):
        r = client.delete(
            "/api/devices/apns-token/" + "e" * 64,
            headers={"X-Device-Secret": _DEVICE_SECRET},
        )
        assert r.status_code == 404

    def test_delete_rejects_wrong_secret(self, client):
        token = "d" * 64
        client.post("/api/devices/apns-token", json=_registration(token))
        r = client.delete(
            f"/api/devices/apns-token/{token}",
            headers={"X-Device-Secret": "wrong-secret-123"},
        )
        assert r.status_code == 404

    def test_deleted_token_no_longer_listed(self, client):
        token = "f" * 64
        client.post("/api/devices/apns-token", json=_registration(token))
        client.delete(
            f"/api/devices/apns-token/{token}",
            headers={"X-Device-Secret": _DEVICE_SECRET},
        )
        r = client.get("/api/devices/apns-tokens")
        listed = [d["token"] for d in r.json()]
        assert token not in listed


class TestDeviceScopedTestPush:
    def test_rejects_unregistered_token(self, client):
        r = client.post(
            "/api/devices/apns-test-push",
            json={"token": "a" * 64, "device_secret": "secret-secret-secret"},
        )
        assert r.status_code == 404

    def test_rejects_wrong_secret(self, client):
        token = "a" * 64
        client.post(
            "/api/devices/apns-token",
            json={"token": token, "environment": "sandbox", "device_secret": "correct-secret-123"},
        )
        r = client.post(
            "/api/devices/apns-test-push",
            json={"token": token, "device_secret": "wrong-secret-123"},
        )
        assert r.status_code == 404

    def test_sends_to_registered_device_with_matching_secret(self, client):
        token = "b" * 64
        client.post(
            "/api/devices/apns-token",
            json={"token": token, "environment": "production", "device_secret": "correct-secret-123"},
        )
        expected = {"configured": True, "results": [{"token": token[-8:], "status": 200}], "pruned_tokens": 0}
        with patch("app.apns.send_test_push_to_device", new=AsyncMock(return_value=expected)) as mock_send:
            r = client.post(
                "/api/devices/apns-test-push",
                json={"token": token, "device_secret": "correct-secret-123"},
            )

        assert r.status_code == 200
        assert r.json() == expected
        mock_send.assert_awaited_once()

    def test_delays_authenticated_test_push_delivery(self, client):
        token = "f" * 64
        client.post(
            "/api/devices/apns-token",
            json={"token": token, "environment": "sandbox", "device_secret": "correct-secret-123"},
        )
        expected = {"configured": True, "results": [{"token": token[-8:], "status": 200}], "pruned_tokens": 0}
        with patch("app.api.devices.asyncio.sleep", new=AsyncMock()) as mock_sleep, \
             patch("app.apns.send_test_push_to_device", new=AsyncMock(return_value=expected)):
            r = client.post(
                "/api/devices/apns-test-push",
                json={
                    "token": token,
                    "device_secret": "correct-secret-123",
                    "delivery_delay_seconds": 4,
                },
            )

        assert r.status_code == 200
        mock_sleep.assert_awaited_once_with(4)

    def test_can_queue_delayed_test_push_before_delivery(self, client):
        token = "1" * 64
        client.post(
            "/api/devices/apns-token",
            json={"token": token, "environment": "sandbox", "device_secret": "correct-secret-123"},
        )

        created = []

        async def fake_delayed_send(token_arg: str, delay_seconds: float) -> None:
            pass

        def fake_create_task(coro):
            created.append(coro)
            coro.close()
            return object()

        with patch("app.api.devices._send_delayed_device_test_push", side_effect=fake_delayed_send) as mock_send, \
             patch("app.api.devices.asyncio.create_task", side_effect=fake_create_task) as mock_create_task:
            r = client.post(
                "/api/devices/apns-test-push",
                json={
                    "token": token,
                    "device_secret": "correct-secret-123",
                    "delivery_delay_seconds": 4,
                    "return_before_delivery": True,
                },
            )

        assert r.status_code == 200
        assert r.json()["results"][0]["status"] == 202
        assert r.json()["note"] == "queued"
        mock_send.assert_called_once_with(token, 4)
        mock_create_task.assert_called_once()
        assert len(created) == 1

    def test_rejects_excessive_test_push_delay(self, client):
        r = client.post(
            "/api/devices/apns-test-push",
            json={
                "token": "a" * 64,
                "device_secret": "secret-secret-secret",
                "delivery_delay_seconds": 11,
            },
        )
        assert r.status_code == 422

    def test_sends_to_latest_registered_token_for_device_id(self, client):
        older_token = "c" * 64
        latest_token = "d" * 64
        client.post(
            "/api/devices/apns-token",
            json={
                "token": older_token,
                "environment": "sandbox",
                "device_id": "device-xyz",
                "device_secret": "correct-secret-123",
            },
        )
        client.post(
            "/api/devices/apns-token",
            json={
                "token": latest_token,
                "environment": "sandbox",
                "device_id": "device-xyz",
                "device_secret": "correct-secret-123",
            },
        )
        expected = {"configured": True, "results": [{"token": latest_token[-8:], "status": 200}], "pruned_tokens": 0}
        with patch("app.apns.send_test_push_to_device", new=AsyncMock(return_value=expected)) as mock_send:
            r = client.post(
                "/api/devices/apns-test-push",
                json={
                    "device_id": "device-xyz",
                    "environment": "sandbox",
                    "device_secret": "correct-secret-123",
                },
            )

        assert r.status_code == 200
        assert r.json() == expected
        sent_device = mock_send.await_args.args[1]
        assert sent_device.token == latest_token

    def test_device_id_lookup_respects_environment(self, client):
        production_token = "e" * 64
        client.post(
            "/api/devices/apns-token",
            json={
                "token": production_token,
                "environment": "production",
                "device_id": "device-xyz",
                "device_secret": "correct-secret-123",
            },
        )

        r = client.post(
            "/api/devices/apns-test-push",
            json={
                "device_id": "device-xyz",
                "environment": "sandbox",
                "device_secret": "correct-secret-123",
            },
        )

        assert r.status_code == 404

    def test_rejects_missing_token_and_device_id(self, client):
        r = client.post(
            "/api/devices/apns-test-push",
            json={"device_secret": "secret-secret-secret"},
        )
        assert r.status_code == 400


class TestDeviceScopedBackgroundRefresh:
    def test_rejects_unregistered_token(self, client):
        r = client.post(
            "/api/devices/background-refresh",
            json={"token": "a" * 64, "device_secret": "secret-secret-secret"},
        )
        assert r.status_code == 404

    def test_rejects_wrong_secret(self, client):
        token = "a" * 64
        client.post(
            "/api/devices/apns-token",
            json={"token": token, "environment": "sandbox", "device_secret": "correct-secret-123"},
        )
        r = client.post(
            "/api/devices/background-refresh",
            json={"token": token, "device_secret": "wrong-secret-123"},
        )
        assert r.status_code == 404

    def test_runs_poll_for_registered_device(self, client):
        token = "b" * 64
        client.post(
            "/api/devices/apns-token",
            json={"token": token, "environment": "production", "device_secret": "correct-secret-123"},
        )
        async def noop_poll():
            return None

        with patch(
            "app.ingestion.scheduler.create_poll_task",
            side_effect=lambda: asyncio.create_task(noop_poll()),
        ) as mock_create_poll_task:
            r = client.post(
                "/api/devices/background-refresh",
                json={"token": token, "device_secret": "correct-secret-123"},
            )

        assert r.status_code == 200
        assert r.json() == {"status": "poll completed"}
        mock_create_poll_task.assert_called_once()

    def test_limits_poll_to_fit_ios_background_budget(self, client):
        token = "1" * 64
        captured_timeout = None

        async def capture_wait_for(awaitable, timeout):
            nonlocal captured_timeout
            captured_timeout = timeout
            return await awaitable

        client.post(
            "/api/devices/apns-token",
            json={"token": token, "environment": "production", "device_secret": "correct-secret-123"},
        )
        async def noop_poll():
            return None

        with patch(
            "app.ingestion.scheduler.create_poll_task",
            side_effect=lambda: asyncio.create_task(noop_poll()),
        ) as mock_create_poll_task, \
             patch("app.api.devices.asyncio.wait_for", side_effect=capture_wait_for):
            r = client.post(
                "/api/devices/background-refresh",
                json={"token": token, "device_secret": "correct-secret-123"},
            )

        assert r.status_code == 200
        mock_create_poll_task.assert_called_once()
        assert captured_timeout == devices_api._BACKGROUND_REFRESH_POLL_TIMEOUT_SECONDS
        assert devices_api._BACKGROUND_REFRESH_POLL_TIMEOUT_SECONDS < 8

    def test_returns_busy_without_starting_second_poll(self, client):
        token = "c" * 64
        client.post(
            "/api/devices/apns-token",
            json={"token": token, "environment": "sandbox", "device_secret": "correct-secret-123"},
        )

        class BusyLock:
            def locked(self):
                return True

        with patch("app.ingestion.scheduler._poll_lock", BusyLock()), \
             patch("app.ingestion.scheduler.create_poll_task") as mock_create_poll_task:
            r = client.post(
                "/api/devices/background-refresh",
                json={"token": token, "device_secret": "correct-secret-123"},
            )

        assert r.status_code == 200
        assert r.json() == {"status": "poll already running"}
        mock_create_poll_task.assert_not_called()

    def test_throttles_repeated_refresh_for_same_device(self, client):
        token = "f" * 64
        client.post(
            "/api/devices/apns-token",
            json={"token": token, "environment": "production", "device_secret": "correct-secret-123"},
        )
        async def noop_poll():
            return None

        with patch(
            "app.ingestion.scheduler.create_poll_task",
            side_effect=lambda: asyncio.create_task(noop_poll()),
        ) as mock_create_poll_task:
            first = client.post(
                "/api/devices/background-refresh",
                json={"token": token, "device_secret": "correct-secret-123"},
            )
            second = client.post(
                "/api/devices/background-refresh",
                json={"token": token, "device_secret": "correct-secret-123"},
            )

        assert first.status_code == 200
        assert first.json() == {"status": "poll completed"}
        assert second.status_code == 200
        assert second.json() == {"status": "poll throttled"}
        mock_create_poll_task.assert_called_once()

    def test_refresh_throttle_prunes_stale_attempts(self):
        now = datetime.now(timezone.utc)
        devices_api._background_refresh_attempts.clear()
        devices_api._background_refresh_attempts["stale-token"] = now - timedelta(minutes=11)

        assert devices_api._recent_background_refresh_attempt("fresh-token", now) is False
        assert "stale-token" not in devices_api._background_refresh_attempts
        assert devices_api._background_refresh_attempts["fresh-token"] == now


class TestListAPNSTokens:
    def test_list_empty_returns_empty(self, client):
        r = client.get("/api/devices/apns-tokens")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_returns_all_tokens(self, client):
        token1 = "a" * 64
        token2 = "b" * 64
        client.post("/api/devices/apns-token", json=_registration(token1))
        client.post("/api/devices/apns-token", json=_registration(token2, "production"))

        r = client.get("/api/devices/apns-tokens")
        assert r.status_code == 200
        tokens = [d["token"] for d in r.json()]
        assert token1 in tokens
        assert token2 in tokens

    def test_list_sorted_newest_first(self, client):
        token1 = "c" * 64
        token2 = "d" * 64
        client.post("/api/devices/apns-token", json=_registration(token1))
        client.post("/api/devices/apns-token", json=_registration(token2))

        r = client.get("/api/devices/apns-tokens")
        tokens = [d["token"] for d in r.json()]
        # token2 was upserted last, so it should appear first
        assert tokens.index(token2) < tokens.index(token1)


class TestPruneSupersededAPNSTokens:
    def test_dry_run_reports_without_deleting(self, client, db_session):
        older = APNSDeviceToken(
            token="1" * 64,
            environment="production",
            device_id="device-old",
            device_secret="owner-secret",
            is_verified=True,
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        newer = APNSDeviceToken(
            token="2" * 64,
            environment="production",
            device_id="device-new",
            is_verified=True,
            last_seen_at=datetime.now(timezone.utc),
        )
        db_session.add_all([older, newer, WatchTerm(keyword="Owned", owner_device_secret="owner-secret")])
        db_session.commit()

        r = client.post(
            "/api/devices/apns-tokens/prune-superseded"
            "?dry_run=true&keep_per_environment=1&preserve_owner_scoped=false"
        )

        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert data["candidate_count"] == 2
        assert data["kept_count"] == 1
        assert data["removed_count"] == 1
        assert data["kept"][0]["token"] == "22222222"
        assert data["removed"][0]["token"] == "11111111"
        assert data["removed"][0]["owner_term_count"] == 1
        assert data["removed"][0]["device_id"] == "vice-old"
        assert db_session.get(APNSDeviceToken, "1" * 64) is not None
        assert db_session.get(APNSDeviceToken, "2" * 64) is not None

    def test_dry_run_preserves_owner_scoped_tokens_by_default(self, client, db_session):
        older = APNSDeviceToken(
            token="1" * 64,
            environment="production",
            device_secret="owner-secret",
            is_verified=True,
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        newer = APNSDeviceToken(
            token="2" * 64,
            environment="production",
            is_verified=True,
            last_seen_at=datetime.now(timezone.utc),
        )
        db_session.add_all([older, newer, WatchTerm(keyword="Owned", owner_device_secret="owner-secret")])
        db_session.commit()

        r = client.post("/api/devices/apns-tokens/prune-superseded?dry_run=true&keep_per_environment=1")

        assert r.status_code == 200
        data = r.json()
        assert data["preserve_owner_scoped"] is True
        assert data["kept_count"] == 2
        assert data["removed_count"] == 0

    def test_execute_removes_older_verified_tokens_per_environment(self, client, db_session):
        production_old = APNSDeviceToken(
            token="1" * 64,
            environment="production",
            is_verified=True,
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        production_new = APNSDeviceToken(
            token="2" * 64,
            environment="production",
            is_verified=True,
            last_seen_at=datetime.now(timezone.utc),
        )
        sandbox = APNSDeviceToken(
            token="3" * 64,
            environment="sandbox",
            is_verified=True,
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        unverified = APNSDeviceToken(
            token="4" * 64,
            environment="production",
            is_verified=False,
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
        db_session.add_all([production_old, production_new, sandbox, unverified])
        db_session.commit()

        r = client.post("/api/devices/apns-tokens/prune-superseded?dry_run=false&keep_per_environment=1")

        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is False
        assert data["candidate_count"] == 3
        assert data["kept_count"] == 2
        assert data["removed_count"] == 1
        assert db_session.get(APNSDeviceToken, "1" * 64) is None
        assert db_session.get(APNSDeviceToken, "2" * 64) is not None
        assert db_session.get(APNSDeviceToken, "3" * 64) is not None
        assert db_session.get(APNSDeviceToken, "4" * 64) is not None
