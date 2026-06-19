"""Tests for the _require_bearer_token auth helper."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from unittest.mock import patch

from app.auth import _require_bearer_token


class TestRequireBearerToken:
    def test_no_expected_token_requires_explicit_dev_escape_hatch(self):
        with pytest.raises(HTTPException) as exc_info:
            _require_bearer_token("", None)
        assert exc_info.value.status_code == 503

    def test_no_expected_token_passes_when_dev_escape_hatch_enabled(self):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.allow_unauthenticated_admin = True
            _require_bearer_token("", None)
            _require_bearer_token("", "Bearer wrong")
            _require_bearer_token("", "garbage")

    def test_correct_bearer_passes(self):
        _require_bearer_token("secret", "Bearer secret")

    def test_missing_authorization_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _require_bearer_token("secret", None)
        assert exc_info.value.status_code == 401

    def test_empty_authorization_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _require_bearer_token("secret", "")
        assert exc_info.value.status_code == 401

    def test_wrong_token_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _require_bearer_token("secret", "Bearer wrong-token")
        assert exc_info.value.status_code == 401

    def test_wrong_scheme_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _require_bearer_token("secret", "Basic secret")
        assert exc_info.value.status_code == 401

    def test_token_without_scheme_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _require_bearer_token("secret", "secret")
        assert exc_info.value.status_code == 401

    def test_response_includes_www_authenticate_header(self):
        with pytest.raises(HTTPException) as exc_info:
            _require_bearer_token("secret", None)
        assert exc_info.value.headers.get("WWW-Authenticate") == "Bearer"

    def test_case_insensitive_scheme(self):
        """'bearer' and 'BEARER' should both be accepted."""
        _require_bearer_token("secret", "bearer secret")
        _require_bearer_token("secret", "BEARER secret")
