"""Tests for the /api/watch-terms CRUD endpoints."""
from __future__ import annotations

import hashlib
import pytest
from unittest.mock import patch

from app.config import settings
from app.models import APNSDeviceToken, WatchTerm


class TestListWatchTerms:
    def test_empty_returns_empty_list(self, client):
        resp = client.get("/api/watch-terms/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_terms(self, client, db_session):
        db_session.add_all([WatchTerm(keyword="Aiko"), WatchTerm(keyword="Haruka")])
        db_session.commit()

        resp = client.get("/api/watch-terms/")
        assert resp.status_code == 200
        keywords = {t["keyword"] for t in resp.json()}
        assert keywords == {"Aiko", "Haruka"}

    def test_list_ordered_newest_first(self, client, db_session):
        db_session.add_all([WatchTerm(keyword="First"), WatchTerm(keyword="Second")])
        db_session.commit()

        resp = client.get("/api/watch-terms/")
        keywords = [t["keyword"] for t in resp.json()]
        assert keywords[0] == "Second", "Most recently created term must appear first"

    def test_returns_term_fields(self, client, db_session):
        db_session.add(WatchTerm(keyword="Miku", aliases=["初音ミク"], notify_on_new=True))
        db_session.commit()

        terms = client.get("/api/watch-terms/").json()
        assert len(terms) == 1
        t = terms[0]
        assert t["keyword"] == "Miku"
        assert "初音ミク" in t["aliases"]
        assert t["notify_on_new"] is True
        assert t["is_active"] is True
        assert t["collection_mode"] == "all_info"

    def test_registered_device_only_lists_owned_terms(self, client, db_session):
        token = "a" * 64
        other_token = "b" * 64
        secret = "device-secret-value"
        other_secret = "other-device-secret-value"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        other_owner_secret = hashlib.sha256(other_secret.encode()).hexdigest()
        db_session.add_all([
            APNSDeviceToken(
                token=token,
                environment="sandbox",
                device_secret=owner_secret,
                is_verified=True,
            ),
            APNSDeviceToken(
                token=other_token,
                environment="sandbox",
                device_secret=other_owner_secret,
                is_verified=True,
            ),
            WatchTerm(keyword="Owned", owner_device_secret=owner_secret),
            WatchTerm(keyword="Other", owner_device_secret=other_owner_secret),
            WatchTerm(keyword="AdminOnly"),
        ])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"):
            resp = client.get(
                "/api/watch-terms/",
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert resp.status_code == 200
        assert [term["keyword"] for term in resp.json()] == ["Owned"]

    def test_device_headers_take_precedence_over_admin_token(self, client, db_session):
        token = "a" * 64
        secret = "device-secret-value"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        db_session.add_all([
            APNSDeviceToken(
                token=token,
                environment="sandbox",
                device_secret=owner_secret,
                is_verified=True,
            ),
            WatchTerm(keyword="Owned", owner_device_secret=owner_secret),
            WatchTerm(keyword="AdminOnly"),
        ])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"):
            resp = client.get(
                "/api/watch-terms/",
                headers={
                    "Authorization": "Bearer admin-secret",
                    "X-Device-Token": token,
                    "X-Device-Secret": secret,
                },
            )

        assert resp.status_code == 200
        assert [term["keyword"] for term in resp.json()] == ["Owned"]


class TestCreateWatchTerm:
    def test_creates_term_returns_201(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            resp = client.post("/api/watch-terms/", json={"keyword": "Aiko"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["keyword"] == "Aiko"
        assert body["id"] is not None

    def test_creates_term_with_aliases(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Miku", "aliases": ["初音ミク", "Hatsune Miku"]},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert "初音ミク" in body["aliases"]

    def test_keyword_too_long_returns_422(self, client):
        resp = client.post("/api/watch-terms/", json={"keyword": "x" * 201})
        assert resp.status_code == 422

    def test_blank_keyword_returns_422(self, client):
        resp = client.post("/api/watch-terms/", json={"keyword": "   "})
        assert resp.status_code == 422

    def test_too_many_aliases_returns_422(self, client):
        aliases = [f"alias{i}" for i in range(21)]
        resp = client.post("/api/watch-terms/", json={"keyword": "Aiko", "aliases": aliases})
        assert resp.status_code == 422

    def test_triggers_poll_on_create(self, client):
        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.post("/api/watch-terms/", json={"keyword": "Aiko"})
        mock_poll.assert_called_once()

    def test_creates_term_default_collection_mode_is_all_info(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            resp = client.post("/api/watch-terms/", json={"keyword": "Aiko"})
        assert resp.status_code == 201
        assert resp.json()["collection_mode"] == "all_info"

    def test_creates_term_with_explicit_collection_mode(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "collection_mode": "media_only"},
            )
        assert resp.status_code == 201
        assert resp.json()["collection_mode"] == "media_only"

    def test_duplicate_keyword_returns_409(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            client.post("/api/watch-terms/", json={"keyword": "Aiko"})
            resp = client.post("/api/watch-terms/", json={"keyword": "Aiko"})
        assert resp.status_code == 409

    def test_registered_devices_can_create_same_keyword_for_different_owners(self, client, db_session):
        first_token = "a" * 64
        second_token = "b" * 64
        first_secret = "first-device-secret"
        second_secret = "second-device-secret"
        db_session.add_all([
            APNSDeviceToken(
                token=first_token,
                environment="sandbox",
                device_secret=hashlib.sha256(first_secret.encode()).hexdigest(),
                is_verified=True,
            ),
            APNSDeviceToken(
                token=second_token,
                environment="sandbox",
                device_secret=hashlib.sha256(second_secret.encode()).hexdigest(),
                is_verified=True,
            ),
        ])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            first = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko"},
                headers={"X-Device-Token": first_token, "X-Device-Secret": first_secret},
            )
            second = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko"},
                headers={"X-Device-Token": second_token, "X-Device-Secret": second_secret},
            )

        assert first.status_code == 201
        assert second.status_code == 201
        assert db_session.query(WatchTerm).filter_by(keyword="Aiko").count() == 2

    def test_registered_device_adopts_orphaned_same_keyword_without_device(self, client, db_session):
        token = "a" * 64
        secret = "current-device-secret"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        stale_owner_secret = hashlib.sha256("stale-device-secret".encode()).hexdigest()
        stale = WatchTerm(
            keyword="Aiko",
            collection_mode="media_only",
            notify_on_new=False,
            is_active=True,
            aliases=["old"],
            owner_device_secret=stale_owner_secret,
        )
        db_session.add_all([
            stale,
            APNSDeviceToken(
                token=token,
                environment="sandbox",
                device_secret=owner_secret,
                is_verified=True,
            ),
        ])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.post(
                "/api/watch-terms/",
                json={
                    "keyword": "Aiko",
                    "collection_mode": "all_info",
                    "notify_on_new": True,
                    "aliases": ["new"],
                },
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert resp.status_code == 201
        assert resp.json()["id"] == stale.id
        db_session.refresh(stale)
        assert stale.owner_device_secret == owner_secret
        assert stale.collection_mode == "all_info"
        assert stale.notify_on_new is True
        assert stale.aliases == ["new"]
        assert db_session.query(WatchTerm).filter_by(keyword="Aiko").count() == 1
        mock_poll.assert_called_once()

    def test_registered_device_does_not_adopt_same_keyword_with_existing_owner_device(self, client, db_session):
        current_token = "a" * 64
        old_token = "b" * 64
        current_secret = "current-device-secret"
        old_secret = "old-device-secret"
        current_owner_secret = hashlib.sha256(current_secret.encode()).hexdigest()
        old_owner_secret = hashlib.sha256(old_secret.encode()).hexdigest()
        stale = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret=old_owner_secret,
        )
        db_session.add_all([
            stale,
            APNSDeviceToken(
                token=current_token,
                environment="sandbox",
                device_secret=current_owner_secret,
                is_verified=True,
            ),
            APNSDeviceToken(
                token=old_token,
                environment="sandbox",
                device_secret=old_owner_secret,
                is_verified=True,
            ),
        ])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "notify_on_new": True},
                headers={"X-Device-Token": current_token, "X-Device-Secret": current_secret},
            )

        assert resp.status_code == 201
        db_session.refresh(stale)
        assert stale.owner_device_secret == old_owner_secret
        assert stale.notify_on_new is False
        assert db_session.query(WatchTerm).filter_by(keyword="Aiko").count() == 2

    def test_registered_device_duplicate_keyword_for_same_owner_returns_409(self, client, db_session):
        token = "a" * 64
        secret = "device-secret-value"
        db_session.add(APNSDeviceToken(
            token=token,
            environment="sandbox",
            device_secret=hashlib.sha256(secret.encode()).hexdigest(),
            is_verified=True,
        ))
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko"},
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )
            duplicate = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko"},
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert duplicate.status_code == 409

    def test_registered_device_can_create_when_admin_auth_is_enabled(self, client, db_session):
        token = "a" * 64
        secret = "device-secret-value"
        db_session.add(APNSDeviceToken(
            token=token,
            environment="sandbox",
            device_secret=hashlib.sha256(secret.encode()).hexdigest(),
            is_verified=True,
        ))
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko"},
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert resp.status_code == 201

    def test_secret_only_device_can_create_when_apns_is_unavailable(self, client):
        secret = "simulator-device-secret"

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko"},
                headers={"X-Device-Secret": secret},
            )

        assert resp.status_code == 201

    def test_secret_only_device_only_sees_its_own_terms(self, client):
        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            first = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko"},
                headers={"X-Device-Secret": "first-device"},
            )
            second = client.get(
                "/api/watch-terms/",
                headers={"X-Device-Secret": "second-device"},
            )

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json() == []

    def test_unverified_apns_token_uses_secret_identity(self, client, db_session):
        token = "a" * 64
        secret = "device-secret-value"
        db_session.add(APNSDeviceToken(
            token=token,
            environment="sandbox",
            device_secret=hashlib.sha256(secret.encode()).hexdigest(),
            is_verified=False,
        ))
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko"},
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert resp.status_code == 201
        assert db_session.query(WatchTerm).one().owner_device_secret == hashlib.sha256(
            secret.encode()
        ).hexdigest()

    def test_unregistered_apns_token_uses_secret_identity(self, client):
        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko"},
                headers={"X-Device-Token": "b" * 64, "X-Device-Secret": "device-secret"},
            )

        assert resp.status_code == 201


class TestUpdateWatchTerm:
    def test_update_is_active(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"is_active": False})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_update_notify_on_new(self, client, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=False)
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"notify_on_new": True})
        assert resp.status_code == 200
        assert resp.json()["notify_on_new"] is True

    def test_update_aliases_triggers_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko", aliases=[])
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.patch(f"/api/watch-terms/{term.id}", json={"aliases": ["愛子"]})
        mock_poll.assert_called_once()

    def test_update_without_aliases_does_not_trigger_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.patch(f"/api/watch-terms/{term.id}", json={"is_active": False})
        mock_poll.assert_not_called()

    def test_update_collection_mode_triggers_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko", collection_mode="all_info")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"collection_mode": "media_only"})
        assert resp.status_code == 200
        assert resp.json()["collection_mode"] == "media_only"
        mock_poll.assert_called_once()

    def test_update_collection_mode(self, client, db_session):
        term = WatchTerm(keyword="Aiko", collection_mode="all_info")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"collection_mode": "media_only"})
        assert resp.status_code == 200
        assert resp.json()["collection_mode"] == "media_only"

    def test_reactivating_term_triggers_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=False)
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.patch(f"/api/watch-terms/{term.id}", json={"is_active": True})
        mock_poll.assert_called_once()

    def test_deactivating_term_does_not_trigger_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=True)
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.patch(f"/api/watch-terms/{term.id}", json={"is_active": False})
        mock_poll.assert_not_called()

    def test_update_keyword_alone_triggers_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"keyword": "Aiko Updated"})
        assert resp.status_code == 200
        mock_poll.assert_called_once()

    def test_update_notify_on_new_alone_does_not_trigger_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=False)
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.patch(f"/api/watch-terms/{term.id}", json={"notify_on_new": True})
        mock_poll.assert_not_called()

    def test_update_invalid_collection_mode_returns_422(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"collection_mode": "unknown_mode"})
        assert resp.status_code == 422

    def test_update_nonexistent_returns_404(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch("/api/watch-terms/999999", json={"is_active": False})
        assert resp.status_code == 404


class TestDeleteWatchTerm:
    def test_delete_existing_returns_204(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        resp = client.delete(f"/api/watch-terms/{term.id}")
        assert resp.status_code == 204

    def test_delete_removes_from_list(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        client.delete(f"/api/watch-terms/{term.id}")

        resp = client.get("/api/watch-terms/")
        assert resp.json() == []

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/watch-terms/999999")
        assert resp.status_code == 404

    def test_delete_removes_orphan_source_items(self, client, db_session):
        from datetime import datetime, timezone
        from app.models import Match, SourceItem

        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        item = SourceItem(
            id="news:orphan-001",
            platform="news",
            item_id="orphan-001",
            url="https://example.com/orphan",
            published_at=datetime.now(timezone.utc),
            media_type="article",
        )
        db_session.add(item)
        db_session.flush()
        db_session.add(Match(watch_term_id=term.id, source_item_id=item.id))
        db_session.commit()

        item_id = item.id  # capture before the delete flushes the identity map
        client.delete(f"/api/watch-terms/{term.id}")

        remaining = db_session.query(SourceItem).filter_by(id=item_id).first()
        assert remaining is None, "Orphaned source_item should be deleted when its only match is removed"
