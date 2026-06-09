import pytest


class TestAPNSTokenUpsert:
    def test_upsert_new_token_returns_201(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json={"token": "a" * 64, "environment": "sandbox"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["token"] == "a" * 64
        assert data["environment"] == "sandbox"

    def test_upsert_normalizes_token(self, client):
        raw = "  " + "AB" * 32 + "  "
        r = client.post(
            "/api/devices/apns-token",
            json={"token": raw, "environment": "sandbox"},
        )
        assert r.status_code == 201
        assert r.json()["token"] == "ab" * 32

    def test_upsert_updates_existing_token(self, client):
        token = "b" * 64
        client.post("/api/devices/apns-token", json={"token": token, "environment": "sandbox"})
        r = client.post(
            "/api/devices/apns-token",
            json={"token": token, "environment": "production", "device_id": "dev-xyz"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["environment"] == "production"
        assert data["device_id"] == "dev-xyz"

    def test_upsert_removes_internal_spaces(self, client):
        # _normalize_token uses split() which removes ALL whitespace including internal spaces
        raw = " ".join(["ab"] * 32)  # "ab ab ab ... ab" — 64 hex chars with spaces
        r = client.post(
            "/api/devices/apns-token",
            json={"token": raw, "environment": "sandbox"},
        )
        assert r.status_code == 201
        assert r.json()["token"] == "ab" * 32

    def test_upsert_invalid_token_returns_400(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json={"token": "not-hex!!", "environment": "sandbox"},
        )
        assert r.status_code == 400

    def test_upsert_empty_token_returns_400(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json={"token": "   ", "environment": "sandbox"},
        )
        assert r.status_code == 400

    def test_upsert_short_token_returns_400(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json={"token": "a" * 32, "environment": "sandbox"},
        )
        assert r.status_code == 400

    def test_upsert_long_token_returns_400(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json={"token": "a" * 65, "environment": "sandbox"},
        )
        assert r.status_code == 400

    def test_last_seen_at_is_utc_aware(self, client):
        r = client.post(
            "/api/devices/apns-token",
            json={"token": "c" * 64, "environment": "sandbox"},
        )
        assert r.status_code == 201
        last_seen = r.json()["last_seen_at"]
        assert last_seen is not None
        assert last_seen.endswith("Z") or "+" in last_seen


class TestAPNSTokenDelete:
    def test_delete_existing_token_returns_204(self, client):
        token = "d" * 64
        client.post("/api/devices/apns-token", json={"token": token, "environment": "sandbox"})
        r = client.delete(f"/api/devices/apns-token/{token}")
        assert r.status_code == 204

    def test_delete_nonexistent_token_returns_204(self, client):
        r = client.delete("/api/devices/apns-token/" + "e" * 64)
        assert r.status_code == 204

    def test_deleted_token_no_longer_listed(self, client):
        token = "f" * 64
        client.post("/api/devices/apns-token", json={"token": token, "environment": "sandbox"})
        client.delete(f"/api/devices/apns-token/{token}")
        r = client.get("/api/devices/apns-tokens")
        listed = [d["token"] for d in r.json()]
        assert token not in listed


class TestListAPNSTokens:
    def test_list_empty_returns_empty(self, client):
        r = client.get("/api/devices/apns-tokens")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_returns_all_tokens(self, client):
        token1 = "a" * 64
        token2 = "b" * 64
        client.post("/api/devices/apns-token", json={"token": token1, "environment": "sandbox"})
        client.post("/api/devices/apns-token", json={"token": token2, "environment": "production"})

        r = client.get("/api/devices/apns-tokens")
        assert r.status_code == 200
        tokens = [d["token"] for d in r.json()]
        assert token1 in tokens
        assert token2 in tokens

    def test_list_sorted_newest_first(self, client):
        token1 = "c" * 64
        token2 = "d" * 64
        client.post("/api/devices/apns-token", json={"token": token1, "environment": "sandbox"})
        client.post("/api/devices/apns-token", json={"token": token2, "environment": "sandbox"})

        r = client.get("/api/devices/apns-tokens")
        tokens = [d["token"] for d in r.json()]
        # token2 was upserted last, so it should appear first
        assert tokens.index(token2) < tokens.index(token1)
