"""Tests for APNs notification dispatch logic."""
from __future__ import annotations

import app.apns as _apns_mod
import jwt as _jwt_mod
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from unittest.mock import MagicMock

import httpx

import tempfile, os

from app.apns import _host, _payload, _private_key, _send_one, apns_configured, send_new_match_notifications
from app.models import APNSDeviceToken, WatchTerm


class TestPayload:
    def _term(self, keyword: str = "Aiko", term_id: int = 1) -> WatchTerm:
        t = WatchTerm(keyword=keyword)
        t.id = term_id
        return t

    def test_payload_structure(self):
        payload = _payload(self._term("Miku"), 3)
        assert payload["aps"]["alert"]["title"] == "Miku の新着"
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
            await send_new_match_notifications(db_session, term, 5)
        mock_send.assert_not_called()

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

    @pytest.mark.asyncio
    async def test_sends_to_matching_environment_device(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        device = _device("a" * 64, environment="sandbox")
        db_session.add_all([term, device])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock(return_value=False)) as mock_send:
            mock_settings.apns_use_sandbox = True
            await send_new_match_notifications(db_session, term, 3)

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args.args[1].token == "a" * 64
        assert call_args.args[2] == term
        assert call_args.args[3] == 3

    @pytest.mark.asyncio
    async def test_does_not_send_to_wrong_environment(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        device = _device("b" * 64, environment="production")
        db_session.add_all([term, device])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock(return_value=False)) as mock_send:
            mock_settings.apns_use_sandbox = True  # sandbox mode, but device is production
            await send_new_match_notifications(db_session, term, 1)

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_deletes_bad_token_device(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        device = _device("c" * 64, environment="sandbox")
        db_session.add_all([term, device])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock(return_value=True)):
            mock_settings.apns_use_sandbox = True
            await send_new_match_notifications(db_session, term, 2)

        remaining = db_session.query(APNSDeviceToken).filter_by(token="c" * 64).first()
        assert remaining is None

    @pytest.mark.asyncio
    async def test_keeps_good_token_device(self, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        device = _device("d" * 64, environment="sandbox")
        db_session.add_all([term, device])
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.settings") as mock_settings, \
             patch("app.apns._send_one", new=AsyncMock(return_value=False)):
            mock_settings.apns_use_sandbox = True
            await send_new_match_notifications(db_session, term, 1)

        remaining = db_session.query(APNSDeviceToken).filter_by(token="d" * 64).first()
        assert remaining is not None


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
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result is False  # keep token on network failure

    @pytest.mark.asyncio
    async def test_returns_false_on_200(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(200, {}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result is False  # success — keep token

    @pytest.mark.asyncio
    async def test_returns_true_for_bad_device_token(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(400, {"reason": "BadDeviceToken"}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result is True  # delete token

    @pytest.mark.asyncio
    async def test_returns_true_for_unregistered(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(410, {"reason": "Unregistered"}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result is True  # delete token

    @pytest.mark.asyncio
    async def test_returns_true_for_410_regardless_of_reason(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(410, {"reason": "SomeOtherReason"}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result is True  # 410 always means delete token

    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_rejection_reason(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(400, {"reason": "PayloadTooLarge"}))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result is False  # not a bad-token error — keep it

    @pytest.mark.asyncio
    async def test_returns_false_when_json_parse_fails(self):
        term, device = _term_and_device()
        client = _mock_client(response=_mock_response(500, text="Internal Server Error"))
        with patch("app.apns._cached_auth_token", return_value="tok"), \
             patch("app.apns.settings") as s:
            s.apns_topic = "com.example.app"
            s.apns_use_sandbox = True
            result = await _send_one(client, device, term, 1)
        assert result is False  # server error without bad-token reason — keep token


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
