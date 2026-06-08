import pytest


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
