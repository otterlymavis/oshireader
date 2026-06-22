"""Tests for APNs notification dispatch logic."""
from __future__ import annotations

import app.apns as _apns_mod
import jwt as _jwt_mod
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from unittest.mock import MagicMock

import httpx

import tempfile, os

from app.apns import (
    APNSSendResult,
    _host,
    _payload,
    _payload_size,
    _private_key,
    _send_one,
    apns_configured,
    revalidate_unverified_devices,
    send_new_match_notifications,
    send_test_push,
    send_test_push_to_device,
)
from app.models import APNSDeviceToken, BackendEvent, WatchTerm


class TestPayload:
    def _term(self, keyword: str = "Aiko", term_id: int = 1) -> WatchTerm:
        t = WatchTerm(keyword=keyword)
        t.id = term_id
        return t

    def test_payload_structure(self):
        payload = _payload(self._term("Miku"), 3)
        assert payload["aps"]["alert"]["title"] == "Miku の新着"
        assert payload["aps"]["content-available"] == 1
        assert payload["watch_term_keyword"] == "Miku"
        assert payload["new_count"] == 3

    def test_singular_body(self):
        payload = _payload(self._term(), 1)
        assert payload["aps"]["alert"]["body"] == "1件の新着があります。"

    def test_plural_body(self):
        payload = _payload(self._term(), 5)
        assert payload["aps"]["alert"]["body"] == "5件の新着があります。"

    def test_watch_term_id_included(self):
        payload = _payload(self._term(term_id=42), 1)
        assert payload["watch_term_id"] == 42

    def test_preview_item_replaces_count_body(self):
        payload = _payload(
            self._term("Aiko"),
            3,
            {
                "id": "youtube:1",
                "match_id": 123,
                "platform": "youtube",
                "url": "https://example.com/watch",
                "redirect_url": "https://backend.example.com/api/feed/matches/123/redirect",
                "title": "Aiko announces a new live stream",
                "content_text": "Longer stream details",
                "author": "Aiko Channel",
                "thumbnail_url": "https://example.com/thumb.jpg",
                "media_type": "video",
                "published_at": "2026-06-17T12:00:00Z",
            },
        )
        assert payload["aps"]["mutable-content"] == 1
        assert payload["aps"]["content-available"] == 1
        assert payload["aps"]["category"] == "OSHI_RESULT_PREVIEW"
        assert payload["aps"]["thread-id"] == "oshireader-1"
        assert payload["aps"]["target-content-id"] == "youtube:1"
        assert payload["aps"]["alert"]["body"] == "Aiko announces a new live stream\nほか2件"
        assert "subtitle" not in payload["aps"]["alert"]
        assert payload["preview_item"]["url"] == "https://backend.example.com/api/feed/matches/123/redirect"
        assert payload["preview_item"]["match_id"] == "123"
        assert payload["preview_item"]["content_text"] == "Longer stream details"
        assert payload["preview_item"]["thumbnail_url"] == "https://example.com/thumb.jpg"
        assert payload["item_id"] == "youtube:1"
        assert payload["item_url"] == "https://backend.example.com/api/feed/matches/123/redirect"
        assert payload["match_id"] == "123"
        assert payload["item_platform"] == "youtube"
        assert payload["item_title"] == "Aiko announces a new live stream"
        assert payload["item_content_text"] == "Longer stream details"
        assert payload["item_author"] == "Aiko Channel"
        assert payload["item_media_type"] == "video"
        assert payload["item_published_at"] == "2026-06-17T12:00:00Z"
        assert payload["thumbnail_url"] == "https://example.com/thumb.jpg"

    def test_preview_item_metadata_is_bounded_for_apns_size(self):
        payload = _payload(
            self._term("Aiko"),
            1,
            {
                "id": "x" * 300,
                "match_id": 123456,
                "platform": "youtube",
                "url": "https://example.com/" + ("u" * 800),
                "title": "T" * 300,
                "content_text": "C" * 3000,
                "author": "A" * 300,
                "thumbnail_url": "https://example.com/" + ("t" * 800),
                "media_type": "article",
                "published_at": "2026-06-17T12:00:00Z",
            },
        )
        assert payload["preview_item"]["id"] == "x" * 300
        assert payload["preview_item"]["match_id"] == "123456"
        assert payload["match_id"] == "123456"
        assert payload["preview_item"]["url"] == "https://example.com/" + ("u" * 800)
        assert "title" not in payload["preview_item"]
        assert len(payload["item_title"]) == 180
        assert payload["item_id"] == payload["preview_item"]["id"]
        assert payload["item_url"] == payload["preview_item"]["url"]
        assert "content_text" not in payload["preview_item"]
        assert "item_content_text" not in payload
        assert "author" not in payload["preview_item"]
        assert "item_author" not in payload
        assert "thumbnail_url" not in payload["preview_item"]
        assert "thumbnail_url" not in payload
        assert _payload_size(payload) <= 3500

    def test_payload_trimming_preserves_thumbnail_before_long_text(self):
        thumbnail_url = "https://example.com/thumb.jpg"
        payload = _payload(
            self._term("Aiko"),
            1,
            {
                "id": "youtube:1",
                "platform": "youtube",
                "url": "https://example.com/watch?" + ("u" * 1200),
                "title": "Aiko update",
                "content_text": "C" * 3000,
                "author": "A" * 300,
                "thumbnail_url": thumbnail_url,
                "media_type": "video",
                "published_at": "2026-06-17T12:00:00Z",
            },
        )

        assert "item_content_text" not in payload
        assert payload["thumbnail_url"] == thumbnail_url
        assert payload["preview_item"]["thumbnail_url"] == thumbnail_url
        assert _payload_size(payload) <= 3500

    def test_alert_does_not_expose_source_metadata(self):
        payload = _payload(
            self._term("Aiko"),
            1,
            {
                "id": "youtube:1",
                "url": "https://example.com/watch",
                "title": "Aiko update",
                "author": "A" * 5000,
                "platform": "P" * 5000,
            },
        )

        assert "subtitle" not in payload["aps"]["alert"]
        assert _payload_size(payload) <= 3500


class TestHost:
    def test_sandbox_url(self):
        with patch("app.apns.settings") as mock_settings:
            mock_settings.apns_use_sandbox = True
            assert _host() == "https://api.sandbox.push.apple.com"

    def test_production_url(self):
        with patch("app.apns.settings") as mock_settings:
            mock_settings.apns_use_sandbox = False
            assert _host() == "https://api.push.apple.com"


def _device(token: str, environment: str = "sandbox") -> APNSDeviceToken:
    return APNSDeviceToken(
        token=token,
        environment=environment,
        is_verified=True,
        last_seen_at=datetime.now(timezone.utc),
    )


class TestSendNewMatchNotifications:
    @pytest.mark.asyncio
    async def test_skips_when_count_zero(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns._send_one", new=AsyncMock()) as mock_send:
            await send_new_match_notifications(db_session, term, 0)
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_notify_on_new_false(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=False)
        db_session.add(term)
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns._send_one", new=AsyncMock()) as mock_send:
            await send_new_match_notifications(db_session, term, 3)
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_apns_not_configured(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=False), \
             patch("app.apns._send_one", new=AsyncMock()) as mock_send:
            should_clear = await send_new_match_notifications(db_session, term, 5)
        mock_send.assert_not_called()
        assert should_clear is False

    @pytest.mark.asyncio
    async def test_skips_when_no_devices(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock()) as mock_send:
            mock_settings.apns_use_sandbox = True
            await send_new_match_notifications(db_session, term, 2)
        mock_send.assert_not_called()

        event = db_session.query(BackendEvent).order_by(BackendEvent.id.desc()).first()
        assert event.kind == "apns"
        assert event.status == "skipped"
        assert event.payload["owner_scoped"] is False
        assert event.payload["total_devices"] == 0
        assert event.payload["total_verified_devices"] == 0

    @pytest.mark.asyncio
    async def test_skip_event_explains_owner_scoped_token_miss(self, db_session):
        owner_secret = "owner-secret-digest"
        term = WatchTerm(keyword="Aiko", notify_on_new=True, owner_device_secret=owner_secret)
        owner_device = _device("a" * 64, environment="sandbox")
        owner_device.device_secret = owner_secret
        owner_device.is_verified = False
        other_device = _device("b" * 64, environment="sandbox")
        other_device.device_secret = "other-secret-digest"
        db_session.add_all([term, owner_device, other_device])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock()) as mock_send:
            mock_settings.apns_use_sandbox = True
            should_clear = await send_new_match_notifications(db_session, term, 2)
        mock_send.assert_not_called()
        assert should_clear is False

        event = db_session.query(BackendEvent).order_by(BackendEvent.id.desc()).first()
        assert event.kind == "apns"
        assert event.status == "skipped"
        assert event.payload["owner_scoped"] is True
        assert event.payload["total_devices"] == 2
        assert event.payload["total_verified_devices"] == 1
        assert event.payload["owner_devices"] == 1
        assert event.payload["owner_verified_devices"] == 0

    @pytest.mark.asyncio
    async def test_sends_to_matching_environment_device(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        device = _device("a" * 64, environment="sandbox")
        db_session.add_all([term, device])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock(return_value=APNSSendResult(delivered=True))) as mock_send:
            mock_settings.apns_use_sandbox = True
            await send_new_match_notifications(db_session, term, 3)

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args.args[1].token == "a" * 64
        assert call_args.args[2] == term
        assert call_args.args[3] == 3

    @pytest.mark.asyncio
    async def test_sends_to_device_regardless_of_global_setting(self, db_session):
        # A production (TestFlight) token must still be delivered even when the server's
        # global apns_use_sandbox is True — _send_one routes by the token's own environment.
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        device = _device("b" * 64, environment="production")
        db_session.add_all([term, device])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock(return_value=APNSSendResult(delivered=True))) as mock_send:
            mock_settings.apns_use_sandbox = True  # sandbox mode, but device is production
            await send_new_match_notifications(db_session, term, 1)

        mock_send.assert_called_once()
        assert mock_send.call_args.args[1].token == "b" * 64

    def test_host_routes_by_token_environment(self):
        assert _host("production") == "https://api.push.apple.com"
        assert _host("sandbox") == "https://api.sandbox.push.apple.com"

    @pytest.mark.asyncio
    async def test_deletes_bad_token_device(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        device = _device("c" * 64, environment="sandbox")
        db_session.add_all([term, device])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock(return_value=APNSSendResult(should_delete_token=True))):
            mock_settings.apns_use_sandbox = True
            should_clear = await send_new_match_notifications(db_session, term, 2)

        remaining = db_session.query(APNSDeviceToken).filter_by(token="c" * 64).first()
        assert remaining is None
        assert should_clear is False

    @pytest.mark.asyncio
    async def test_keeps_good_token_device(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        device = _device("d" * 64, environment="sandbox")
        db_session.add_all([term, device])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock(return_value=APNSSendResult(delivered=True))):
            mock_settings.apns_use_sandbox = True
            await send_new_match_notifications(db_session, term, 1)

        remaining = db_session.query(APNSDeviceToken).filter_by(token="d" * 64).first()
        assert remaining is not None

    @pytest.mark.asyncio
    async def test_device_owned_term_only_sends_to_owner_device(self, db_session):
        owner_secret = "owner-secret-digest"
        term = WatchTerm(keyword="Aiko", notify_on_new=True, owner_device_secret=owner_secret)
        owner_device = _device("e" * 64, environment="sandbox")
        owner_device.device_secret = owner_secret
        other_device = _device("f" * 64, environment="sandbox")
        other_device.device_secret = "other-secret-digest"
        db_session.add_all([term, owner_device, other_device])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock(return_value=APNSSendResult(delivered=True))) as mock_send:
            mock_settings.apns_use_sandbox = True
            await send_new_match_notifications(db_session, term, 1)

        mock_send.assert_called_once()
        assert mock_send.call_args.args[1].token == "e" * 64


class TestDeviceRevalidation:
    @pytest.mark.asyncio
    async def test_revalidates_due_unverified_device(self, db_session):
        device = _device("1" * 64)
        device.is_verified = False
        device.verification_attempted_at = datetime.now(timezone.utc) - timedelta(hours=7)
        db_session.add(device)
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.validate_device_registration", new=AsyncMock(return_value=True)):
            verified = await revalidate_unverified_devices(db_session)

        db_session.refresh(device)
        assert verified == 1
        assert device.is_verified is True
        assert device.verified_at is not None

    @pytest.mark.asyncio
    async def test_throttles_recent_failed_verification(self, db_session):
        device = _device("2" * 64)
        device.is_verified = False
        device.verification_attempted_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.add(device)
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.validate_device_registration", new=AsyncMock()) as validate:
            verified = await revalidate_unverified_devices(db_session)

        assert verified == 0
        validate.assert_not_awaited()


class TestApnsConfigured:
    def _mock_settings(self, team_id="", key_id="", topic="", private_key=""):
        return {"apns_team_id": team_id, "apns_key_id": key_id,
                "apns_topic": topic, "apns_private_key": private_key,
                "apns_private_key_path": ""}

    def test_returns_false_when_all_empty(self):
        with patch("app.apns.settings") as s:
            s.apns_team_id = ""
            s.apns_key_id = ""
            s.apns_topic = ""
            s.apns_private_key = ""
            s.apns_private_key_path = ""
            assert apns_configured() is False

    def test_returns_true_when_all_set(self):
        with patch("app.apns.settings") as s:
            s.apns_team_id = "TEAMID1234"
            s.apns_key_id = "KEYID12345"
            s.apns_topic = "com.example.app"
            s.apns_private_key = "-----BEGIN EC PRIVATE KEY-----\nfake\n-----END EC PRIVATE KEY-----"
            s.apns_private_key_path = ""
            assert apns_configured() is True

    def test_returns_false_when_team_id_missing(self):
        with patch("app.apns.settings") as s:
            s.apns_team_id = ""
            s.apns_key_id = "KEYID12345"
            s.apns_topic = "com.example.app"
            s.apns_private_key = "key"
            s.apns_private_key_path = ""
            assert apns_configured() is False

    def test_returns_false_when_private_key_missing(self):
        with patch("app.apns.settings") as s:
            s.apns_team_id = "TEAMID1234"
            s.apns_key_id = "KEYID12345"
            s.apns_topic = "com.example.app"
            s.apns_private_key = ""
            s.apns_private_key_path = ""
            assert apns_configured() is False


class TestPrivateKey:
    def test_returns_private_key_string_with_escaped_newlines_expanded(self):
        with patch("app.apns.settings") as s:
            s.apns_private_key = "line1\\nline2"
            s.apns_private_key_path = ""
            result = _private_key()
        assert result == "line1\nline2"

    def test_reads_from_file_when_env_key_is_empty(self):
        key_content = "-----BEGIN EC PRIVATE KEY-----\nfakekey\n-----END EC PRIVATE KEY-----\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".p8", delete=False) as f:
            f.write(key_content)
            tmp_path = f.name
        try:
            with patch("app.apns.settings") as s:
                s.apns_private_key = ""
                s.apns_private_key_path = tmp_path
                result = _private_key()
            assert result == key_content
        finally:
            os.unlink(tmp_path)

    def test_returns_empty_string_when_neither_set(self):
        with patch("app.apns.settings") as s:
            s.apns_private_key = ""
            s.apns_private_key_path = ""
            result = _private_key()
        assert result == ""


def _mock_response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = Exception("not json")
        resp.text = text
    return resp


def _mock_client(response: MagicMock | None = None, raise_exc: Exception | None = None) -> MagicMock:
    client = MagicMock(spec=httpx.AsyncClient)
    if raise_exc is not None:
        client.post = AsyncMock(side_effect=raise_exc)
    else:
        client.post = AsyncMock(return_value=response)
    return client


def _term_and_device():
    term = WatchTerm(keyword="Aiko")
    term.id = 1
    device = APNSDeviceToken(token="a" * 64, environment="sandbox")
    return term, device


class TestSendOne:
    @pytest.mark.asyncio
    async def test_returns_false_on_network_exception(self):
        term, device = _term_and_device()
        client = _mock_client(raise_exc=httpx.ConnectError("timeout"))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.asyncio.sleep", new=AsyncMock()), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result == APNSSendResult(retryable_failure=True)
        assert client.post.await_count == 3

    @pytest.mark.asyncio
    async def test_retries_transient_response_then_succeeds(self):
        term, device = _term_and_device()
        client = _mock_client()
        client.post.side_effect = [
            _mock_response(503, {"reason": "ServiceUnavailable"}),
            _mock_response(200, {}),
        ]
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.asyncio.sleep", new=AsyncMock()) as mock_sleep, \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            result = await _send_one(client, device, term, 1)

        assert result == APNSSendResult(delivered=True)
        assert client.post.await_count == 2
        mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_retry_permanent_rejection(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(400, {"reason": "BadDeviceToken"}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.asyncio.sleep", new=AsyncMock()) as mock_sleep, \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            result = await _send_one(client, device, term, 1)

        assert result == APNSSendResult(should_delete_token=True)
        client.post.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_on_200(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(200, {}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result == APNSSendResult(delivered=True)

    @pytest.mark.asyncio
    async def test_skips_send_when_preview_payload_exceeds_apns_limit(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(200, {}))
        huge_preview = {
            "id": "x" * 5000,
            "url": "https://example.com/" + ("u" * 5000),
            "title": "Aiko",
        }
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1, huge_preview)

        assert result == APNSSendResult()
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_collapse_header_per_term(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(200, {}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            await _send_one(client, device, term, 1)

        headers = client.post.call_args.kwargs["headers"]
        assert headers["apns-collapse-id"] == "oshireader-1"

    @pytest.mark.asyncio
    async def test_returns_true_for_bad_device_token(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(400, {"reason": "BadDeviceToken"}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result == APNSSendResult(should_delete_token=True)

    @pytest.mark.asyncio
    async def test_returns_true_for_unregistered(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(410, {"reason": "Unregistered"}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result == APNSSendResult(should_delete_token=True)

    @pytest.mark.asyncio
    async def test_returns_true_for_410_regardless_of_reason(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(410, {"reason": "SomeOtherReason"}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result == APNSSendResult(should_delete_token=True)

    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_rejection_reason(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(400, {"reason": "PayloadTooLarge"}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result == APNSSendResult()

    @pytest.mark.asyncio
    async def test_returns_false_when_json_parse_fails(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(500, text="Internal Server Error"))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result == APNSSendResult(retryable_failure=True)


class TestSendTestPush:
    @pytest.mark.asyncio
    async def test_admin_test_push_uses_preview_payload(self, db_session):
        device = _device("a" * 64, environment="sandbox")
        db_session.add(device)
        db_session.commit()

        client = _mock_client(response=_mock_response(200, {}))
        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.httpx.AsyncClient") as mock_client_class, \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.backend_public_url = "https://backend.example.com"
            mock_client_class.return_value.__aenter__.return_value = client
            await send_test_push(db_session)

        payload = client.post.call_args.kwargs["json"]
        assert payload["aps"]["category"] == "OSHI_RESULT_PREVIEW"
        assert payload["aps"]["mutable-content"] == 1
        assert payload["preview_item"]["id"] == "oshireader:test-preview"
        assert payload["item_title"] == "通知プレビューのテスト"
        assert payload["thumbnail_url"] == "https://backend.example.com/api/notification-preview.png"
        headers = client.post.call_args.kwargs["headers"]
        assert headers["apns-collapse-id"].startswith("oshireader-test-")
        assert len(headers["apns-collapse-id"]) == len("oshireader-test-") + 12

    @pytest.mark.asyncio
    async def test_device_test_push_uses_preview_payload(self, db_session):
        device = _device("b" * 64, environment="production")
        db_session.add(device)
        db_session.commit()

        client = _mock_client(response=_mock_response(200, {}))
        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.httpx.AsyncClient") as mock_client_class, \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.backend_public_url = "https://backend.example.com"
            mock_client_class.return_value.__aenter__.return_value = client
            await send_test_push_to_device(db_session, device)

        payload = client.post.call_args.kwargs["json"]
        assert payload["aps"]["category"] == "OSHI_RESULT_PREVIEW"
        assert payload["aps"]["target-content-id"] == "oshireader:test-preview"
        assert payload["item_url"] == "https://backend.example.com"
        headers = client.post.call_args.kwargs["headers"]
        assert headers["apns-collapse-id"].startswith("oshireader-test-")
        assert len(headers["apns-collapse-id"]) == len("oshireader-test-") + 12


class TestAuthToken:
    def setup_method(self):
        _apns_mod._auth_token.cache_clear()

    def teardown_method(self):
        _apns_mod._auth_token.cache_clear()

    def test_encodes_jwt_with_correct_claims(self):
        with patch("app.apns._private_key", return_value="pem-key"), \
             patch("app.apns.settings") as s, \
             patch("app.apns.time") as mock_time, \
             patch.object(_jwt_mod, "encode", return_value="header.payload.sig") as mock_enc:
            s.apns_team_id = "TEAMX"
            s.apns_key_id = "KIDX"
            mock_time.time.return_value = 1000000
            token, issued_at = _apns_mod._auth_token()
        assert token == "header.payload.sig"
        assert issued_at == 1000000
        mock_enc.assert_called_once_with(
            {"iss": "TEAMX", "iat": 1000000},
            "pem-key",
            algorithm="ES256",
            headers={"alg": "ES256", "kid": "KIDX"},
        )

    def test_caches_result(self):
        with patch("app.apns._private_key", return_value="pem-key"), \
             patch("app.apns.settings") as s, \
             patch("app.apns.time") as mock_time, \
             patch.object(_jwt_mod, "encode", return_value="header.payload.sig") as mock_enc:
            s.apns_team_id = "TEAMX"
            s.apns_key_id = "KIDX"
            mock_time.time.return_value = 1000000
            _apns_mod._auth_token()
            _apns_mod._auth_token()
        assert mock_enc.call_count == 1


class TestCachedAuthToken:
    def setup_method(self):
        _apns_mod._auth_token.cache_clear()

    def teardown_method(self):
        _apns_mod._auth_token.cache_clear()

    def test_returns_token_when_fresh(self):
        issued_at = 1700000000
        mock_fn = MagicMock(return_value=("fresh-tok", issued_at))
        mock_fn.cache_clear = MagicMock()
        with patch("app.apns._auth_token", mock_fn), \
             patch("app.apns.time") as mock_time:
            mock_time.time.return_value = issued_at + 100
            result = _apns_mod._cached_auth_token()
        assert result == "fresh-tok"
        mock_fn.cache_clear.assert_not_called()
        assert mock_fn.call_count == 1

    def test_refreshes_when_expired(self):
        issued_at = 1700000000
        mock_fn = MagicMock(return_value=("new-tok", issued_at))
        mock_fn.cache_clear = MagicMock()
        with patch("app.apns._auth_token", mock_fn), \
             patch("app.apns.time") as mock_time:
            mock_time.time.return_value = issued_at + 51 * 60
            result = _apns_mod._cached_auth_token()
        assert result == "new-tok"
        mock_fn.cache_clear.assert_called_once()
        assert mock_fn.call_count == 2
