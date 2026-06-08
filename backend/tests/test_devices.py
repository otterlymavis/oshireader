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
