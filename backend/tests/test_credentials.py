import pytest
from unittest.mock import patch

from app.models import PlatformCredential


class TestCredentialAuth:
    def test_list_requires_auth_when_token_set(self, client):
        with patch("app.auth.settings") as s:
            s.admin_api_token = "secret"
            r = client.get("/api/credentials/")
        assert r.status_code == 401

    def test_upsert_requires_auth_when_token_set(self, client):
        with patch("app.auth.settings") as s:
            s.admin_api_token = "secret"
            r = client.put("/api/credentials/youtube", json={"bearer_token": "tok"})
        assert r.status_code == 401

    def test_list_accepts_correct_token(self, client):
        with patch("app.auth.settings") as s:
            s.admin_api_token = "secret"
            r = client.get("/api/credentials/", headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200


class TestListCredentials:
    def test_list_returns_all_supported_platforms(self, client):
        r = client.get("/api/credentials/")
        assert r.status_code == 200
        platforms = [c["platform"] for c in r.json()]
        assert "youtube" in platforms
        assert "twitter" in platforms

    def test_list_empty_credentials_show_no_keys(self, client):
        r = client.get("/api/credentials/")
        assert r.status_code == 200
        for cred in r.json():
            assert cred["has_bearer_token"] is False
            assert cred["has_api_key"] is False
            assert cred["updated_at"] is None

    def test_list_treats_legacy_whitespace_credentials_as_empty(self, client, db_session):
        db_session.add(PlatformCredential(platform="twitter", bearer_token="   ", api_key="\n"))
        db_session.commit()

        r = client.get("/api/credentials/")

        assert r.status_code == 200
        tw = next(c for c in r.json() if c["platform"] == "twitter")
        assert tw["has_bearer_token"] is False
        assert tw["has_api_key"] is False


class TestUpsertCredential:
    def test_upsert_bearer_token(self, client):
        r = client.put("/api/credentials/youtube", json={"bearer_token": "mytoken"})
        assert r.status_code == 200
        data = r.json()
        assert data["has_bearer_token"] is True
        assert data["has_api_key"] is False

    def test_upsert_reflects_in_list(self, client):
        client.put("/api/credentials/youtube", json={"bearer_token": "mytoken"})
        r = client.get("/api/credentials/")
        yt = next(c for c in r.json() if c["platform"] == "youtube")
        assert yt["has_bearer_token"] is True

    def test_upsert_api_secret(self, client):
        # api_secret is a third credential field alongside bearer_token and api_key.
        # Sending it must not crash and must be stored (the response doesn't expose it,
        # but a second PUT that omits it must not clear it — persists via partial update).
        r = client.put("/api/credentials/twitter", json={"bearer_token": "tok", "api_secret": "mysecret"})
        assert r.status_code == 200

    def test_partial_update_preserves_existing_fields(self, client):
        client.put("/api/credentials/twitter", json={"bearer_token": "tok1", "api_key": "key1"})
        client.put("/api/credentials/twitter", json={"bearer_token": "tok2"})
        r = client.get("/api/credentials/")
        tw = next(c for c in r.json() if c["platform"] == "twitter")
        assert tw["has_bearer_token"] is True
        assert tw["has_api_key"] is True

    def test_clear_bearer_token_with_empty_string(self, client):
        client.put("/api/credentials/youtube", json={"bearer_token": "tok"})
        client.put("/api/credentials/youtube", json={"bearer_token": ""})
        r = client.get("/api/credentials/")
        yt = next(c for c in r.json() if c["platform"] == "youtube")
        assert yt["has_bearer_token"] is False

    def test_whitespace_bearer_token_clears_stored_value(self, client):
        client.put("/api/credentials/twitter", json={"bearer_token": "tok"})
        r = client.put("/api/credentials/twitter", json={"bearer_token": "   "})

        assert r.status_code == 200
        assert r.json()["has_bearer_token"] is False

    def test_updated_at_is_utc_aware(self, client):
        r = client.put("/api/credentials/youtube", json={"bearer_token": "tok"})
        assert r.status_code == 200
        updated = r.json()["updated_at"]
        assert updated is not None
        assert updated.endswith("Z") or "+" in updated


class TestDeleteCredential:
    def test_delete_credential_returns_204(self, client):
        client.put("/api/credentials/youtube", json={"bearer_token": "tok"})
        r = client.delete("/api/credentials/youtube")
        assert r.status_code == 204

    def test_delete_clears_stored_values(self, client):
        client.put("/api/credentials/youtube", json={"bearer_token": "tok"})
        client.delete("/api/credentials/youtube")
        r = client.get("/api/credentials/")
        yt = next(c for c in r.json() if c["platform"] == "youtube")
        assert yt["has_bearer_token"] is False

    def test_delete_nonexistent_returns_204(self, client):
        r = client.delete("/api/credentials/youtube")
        assert r.status_code == 204

    def test_delete_unsupported_platform_returns_404(self, client):
        r = client.delete("/api/credentials/notaplatform")
        assert r.status_code == 404


class TestUnsupportedPlatform:
    def test_upsert_unsupported_platform_returns_404(self, client):
        r = client.put("/api/credentials/notaplatform", json={"bearer_token": "tok"})
        assert r.status_code == 404


class TestCredentialValidation:
    def test_bearer_token_too_long_returns_422(self, client):
        r = client.put("/api/credentials/youtube", json={"bearer_token": "x" * 501})
        assert r.status_code == 422

    def test_api_key_too_long_returns_422(self, client):
        r = client.put("/api/credentials/youtube", json={"api_key": "k" * 501})
        assert r.status_code == 422

    def test_token_at_max_length_accepted(self, client):
        r = client.put("/api/credentials/youtube", json={"bearer_token": "t" * 500})
        assert r.status_code == 200

    @pytest.mark.parametrize("field", ["bearer_token", "api_key", "api_secret"])
    def test_padded_secret_at_max_length_accepted_after_trim(self, client, db_session, field):
        value = "t" * 500
        r = client.put("/api/credentials/twitter", json={field: f" {value}\n"})

        assert r.status_code == 200
        db_session.expire_all()
        cred = db_session.get(PlatformCredential, "twitter")
        assert getattr(cred, field) == value
