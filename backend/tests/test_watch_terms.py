"""Tests for the /api/watch-terms CRUD endpoints."""
from __future__ import annotations

import hashlib
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models import APNSDeviceToken, DeviceEntitlement, Match, PendingNotification, SourceItem, WatchTerm


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

    def test_selected_source_mode_requires_a_platform(self, client, db_session):
        resp = client.post(
            "/api/watch-terms/",
            json={"keyword": "Aiko", "source_mode": "selected", "selected_platforms": []},
        )

        assert resp.status_code == 422
        assert "at least one selected platform" in resp.json()["detail"]
        assert db_session.query(WatchTerm).count() == 0

    def test_update_selected_source_mode_requires_a_platform(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        resp = client.patch(
            f"/api/watch-terms/{term.id}",
            json={"source_mode": "selected", "selected_platforms": []},
        )

        assert resp.status_code == 422
        db_session.refresh(term)
        assert term.source_mode == "all"
        assert term.selected_platforms == []

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
                environment="production",
                device_secret=owner_secret,
                is_verified=True,
            ),
            APNSDeviceToken(
                token=other_token,
                environment="production",
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
                environment="production",
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
        assert body["notify_on_new"] is False

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

    def test_normalizes_retired_collection_mode_on_create(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "collection_mode": "media_only"},
            )
        assert resp.status_code == 201
        assert resp.json()["collection_mode"] == "all_info"

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
                environment="production",
                device_secret=hashlib.sha256(first_secret.encode()).hexdigest(),
                is_verified=True,
            ),
            APNSDeviceToken(
                token=second_token,
                environment="production",
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

    def test_registered_device_does_not_adopt_orphaned_same_keyword_without_device(self, client, db_session):
        token = "a" * 64
        secret = "current-device-secret"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        stale_owner_secret = hashlib.sha256("stale-device-secret".encode()).hexdigest()
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        stale = WatchTerm(
            keyword="Aiko",
            collection_mode="media_only",
            source_mode="all",
            selected_platforms=[],
            notify_on_new=False,
            is_active=True,
            aliases=["old"],
            owner_device_secret=stale_owner_secret,
            created_at=old,
        )
        db_session.add_all([
            stale,
            APNSDeviceToken(
                token=token,
                environment="production",
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
                    "source_mode": "selected",
                    "selected_platforms": ["youtube"],
                    "notify_on_new": True,
                    "aliases": ["new"],
                },
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert resp.status_code == 201
        assert resp.json()["id"] != stale.id
        db_session.refresh(stale)
        assert stale.owner_device_secret == stale_owner_secret
        assert stale.collection_mode == "media_only"
        assert stale.notify_on_new is False
        assert stale.aliases == ["old"]
        assert db_session.query(WatchTerm).filter_by(keyword="Aiko").count() == 2
        mock_poll.assert_called_once()

    def test_registered_device_does_not_adopt_same_keyword_when_old_owner_has_only_unverified_device(
        self,
        client,
        db_session,
    ):
        token = "a" * 64
        stale_token = "b" * 64
        secret = "current-device-secret"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        stale_owner_secret = hashlib.sha256("stale-device-secret".encode()).hexdigest()
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        stale = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret=stale_owner_secret,
            created_at=old,
        )
        db_session.add_all([
            stale,
            APNSDeviceToken(
                token=token,
                environment="production",
                device_secret=owner_secret,
                is_verified=True,
            ),
            APNSDeviceToken(
                token=stale_token,
                environment="production",
                device_secret=stale_owner_secret,
                is_verified=False,
            ),
        ])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "notify_on_new": True},
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert resp.status_code == 201
        assert resp.json()["id"] != stale.id
        db_session.refresh(stale)
        assert stale.owner_device_secret == stale_owner_secret
        assert stale.notify_on_new is False
        assert db_session.query(WatchTerm).filter_by(keyword="Aiko").count() == 2
        mock_poll.assert_called_once()

    def test_registered_device_does_not_adopt_recent_orphaned_same_keyword(self, client, db_session):
        token = "a" * 64
        secret = "current-device-secret"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        stale_owner_secret = hashlib.sha256("stale-device-secret".encode()).hexdigest()
        recent = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            is_active=True,
            owner_device_secret=stale_owner_secret,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add_all([
            recent,
            APNSDeviceToken(
                token=token,
                environment="production",
                device_secret=owner_secret,
                is_verified=True,
            ),
        ])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch.object(settings, "orphaned_notification_grace_minutes", 60), \
             patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "notify_on_new": True},
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert resp.status_code == 201
        assert resp.json()["id"] != recent.id
        db_session.refresh(recent)
        assert recent.owner_device_secret == stale_owner_secret
        assert recent.notify_on_new is False
        assert db_session.query(WatchTerm).filter_by(keyword="Aiko").count() == 2
        mock_poll.assert_called_once()

    def test_device_create_notify_term_requires_verified_device(self, client, db_session):
        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "notify_on_new": True},
                headers={"X-Device-Secret": "unverified-device-secret"},
            )

        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "notification_device_required"
        assert "verified APNs device" in resp.json()["detail"]["message"]
        assert db_session.query(WatchTerm).count() == 0
        mock_poll.assert_not_called()

    def test_device_create_notify_term_accepts_verified_device_from_its_own_environment(self, client, db_session):
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
             patch.object(settings, "apns_use_sandbox", False), \
             patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "notify_on_new": True},
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert resp.status_code == 201
        assert resp.json()["notify_on_new"] is True
        assert db_session.query(WatchTerm).count() == 1
        mock_poll.assert_called_once()

    def test_device_can_create_muted_term_without_verified_device(self, client, db_session):
        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "notify_on_new": False},
                headers={"X-Device-Secret": "unverified-device-secret"},
            )

        assert resp.status_code == 201
        assert resp.json()["notify_on_new"] is False
        assert db_session.query(WatchTerm).count() == 1
        mock_poll.assert_called_once()

    def test_device_update_notify_term_requires_verified_device(self, client, db_session):
        owner_secret = hashlib.sha256("unverified-device-secret".encode()).hexdigest()
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            owner_device_secret=owner_secret,
        )
        db_session.add(term)
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.patch(
                f"/api/watch-terms/{term.id}",
                json={"notify_on_new": True},
                headers={"X-Device-Secret": "unverified-device-secret"},
            )

        assert resp.status_code == 409
        db_session.refresh(term)
        assert term.notify_on_new is False
        mock_poll.assert_not_called()

    def test_device_update_notify_term_accepts_verified_device_from_its_own_environment(self, client, db_session):
        token = "a" * 64
        secret = "device-secret-value"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        term = WatchTerm(
            keyword="Aiko",
            notify_on_new=False,
            owner_device_secret=owner_secret,
        )
        db_session.add_all([
            term,
            APNSDeviceToken(
                token=token,
                environment="sandbox",
                device_secret=owner_secret,
                is_verified=True,
            ),
        ])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch.object(settings, "apns_use_sandbox", False), \
             patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.patch(
                f"/api/watch-terms/{term.id}",
                json={"notify_on_new": True},
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert resp.status_code == 200
        db_session.refresh(term)
        assert term.notify_on_new is True
        mock_poll.assert_not_called()

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
                environment="production",
                device_secret=current_owner_secret,
                is_verified=True,
            ),
            APNSDeviceToken(
                token=old_token,
                environment="production",
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
            environment="production",
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
            environment="production",
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
                json={"keyword": "Aiko", "notify_on_new": False},
                headers={"X-Device-Secret": secret},
            )

        assert resp.status_code == 201
        assert resp.json()["notify_on_new"] is False

    def test_secret_only_device_only_sees_its_own_terms(self, client):
        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            first = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "notify_on_new": False},
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
            environment="production",
            device_secret=hashlib.sha256(secret.encode()).hexdigest(),
            is_verified=False,
        ))
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "notify_on_new": False},
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
                json={"keyword": "Aiko", "notify_on_new": False},
                headers={"X-Device-Token": "b" * 64, "X-Device-Secret": "device-secret"},
            )

        assert resp.status_code == 201

    def test_device_created_term_reuses_matching_global_term_history(self, client, db_session):
        global_term = WatchTerm(keyword="Aiko", notify_on_new=False)
        item = SourceItem(
            id="news:aiko-1",
            platform="news",
            item_id="aiko-1",
            url="https://example.com/aiko-1",
            published_at=datetime.now(timezone.utc),
            title="Aiko update",
            media_type="article",
        )
        db_session.add_all([global_term, item])
        db_session.flush()
        db_session.add(Match(watch_term_id=global_term.id, source_item_id=item.id, confidence=0.9))
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "notify_on_new": False},
                headers={"X-Device-Secret": "device-secret"},
            )
            feed = client.get(
                "/api/feed/",
                headers={"X-Device-Secret": "device-secret"},
            )

        assert resp.status_code == 201
        created_id = int(resp.json()["id"])
        assert created_id != global_term.id
        device_matches = db_session.query(Match).filter_by(watch_term_id=created_id).all()
        assert len(device_matches) == 1
        assert device_matches[0].source_item_id == item.id
        assert device_matches[0].confidence == 0.9

        assert feed.status_code == 200
        assert [row["item"]["id"] for row in feed.json()] == [item.id]

    def test_device_created_term_without_matching_global_history_has_no_seeded_matches(
        self,
        client,
        db_session,
    ):
        global_term = WatchTerm(keyword="Haruka", notify_on_new=False)
        item = SourceItem(
            id="news:haruka-1",
            platform="news",
            item_id="haruka-1",
            url="https://example.com/haruka-1",
            published_at=datetime.now(timezone.utc),
            title="Haruka update",
            media_type="article",
        )
        db_session.add_all([global_term, item])
        db_session.flush()
        db_session.add(Match(watch_term_id=global_term.id, source_item_id=item.id))
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "notify_on_new": False},
                headers={"X-Device-Secret": "device-secret"},
            )

        assert resp.status_code == 201
        created_id = int(resp.json()["id"])
        assert db_session.query(Match).filter_by(watch_term_id=created_id).count() == 0


class TestRefreshTierGating:
    """refresh_tier must never be trusted from the client — see
    app.api.watch_terms._effective_refresh_tier. Only an active DeviceEntitlement
    for the requesting device may unlock standard/premium.

    A device-secret request only takes the non-admin path when ADMIN_API_TOKEN is
    configured (see require_admin_or_device_auth); the default test client leaves
    it unset and treats every request as admin, so each test patches it."""

    device_secret_header = "plus-device-secret"

    @property
    def owner_device_secret(self) -> str:
        return hashlib.sha256(self.device_secret_header.encode()).hexdigest()

    def test_create_without_entitlement_clamps_to_free(self, client):
        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "refresh_tier": "premium"},
                headers={"X-Device-Secret": self.device_secret_header},
            )
        assert resp.status_code == 201
        assert resp.json()["refresh_tier"] == "free"

    def test_create_with_active_entitlement_keeps_requested_tier(self, client, db_session):
        db_session.add(DeviceEntitlement(
            owner_device_secret=self.owner_device_secret,
            product_id="com.otterpia.oshireader.plus.monthly",
            original_transaction_id="orig-1",
            latest_transaction_id="txn-1",
            purchase_date=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        ))
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "refresh_tier": "premium"},
                headers={"X-Device-Secret": self.device_secret_header},
            )
        assert resp.status_code == 201
        assert resp.json()["refresh_tier"] == "premium"

    def test_create_with_expired_entitlement_clamps_to_free(self, client, db_session):
        db_session.add(DeviceEntitlement(
            owner_device_secret=self.owner_device_secret,
            product_id="com.otterpia.oshireader.plus.monthly",
            original_transaction_id="orig-1",
            latest_transaction_id="txn-1",
            purchase_date=datetime.now(timezone.utc) - timedelta(days=60),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        ))
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "refresh_tier": "standard"},
                headers={"X-Device-Secret": self.device_secret_header},
            )
        assert resp.status_code == 201
        assert resp.json()["refresh_tier"] == "free"

    def test_update_without_entitlement_clamps_to_free(self, client, db_session):
        term = WatchTerm(keyword="Aiko", owner_device_secret=self.owner_device_secret, refresh_tier="free")
        db_session.add(term)
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"):
            resp = client.patch(
                f"/api/watch-terms/{term.id}",
                json={"refresh_tier": "premium"},
                headers={"X-Device-Secret": self.device_secret_header},
            )
        assert resp.status_code == 200
        assert resp.json()["refresh_tier"] == "free"

    def test_update_with_active_entitlement_keeps_requested_tier(self, client, db_session):
        db_session.add(DeviceEntitlement(
            owner_device_secret=self.owner_device_secret,
            product_id="com.otterpia.oshireader.plus.monthly",
            original_transaction_id="orig-1",
            latest_transaction_id="txn-1",
            purchase_date=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        ))
        term = WatchTerm(keyword="Aiko", owner_device_secret=self.owner_device_secret, refresh_tier="free")
        db_session.add(term)
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"):
            resp = client.patch(
                f"/api/watch-terms/{term.id}",
                json={"refresh_tier": "premium"},
                headers={"X-Device-Secret": self.device_secret_header},
            )
        assert resp.status_code == 200
        assert resp.json()["refresh_tier"] == "premium"

    def test_admin_created_term_is_not_clamped(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Aiko", "refresh_tier": "premium"},
            )
        assert resp.status_code == 201
        assert resp.json()["refresh_tier"] == "premium"


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

    def test_update_explicit_null_clears_language_hint(self, client, db_session):
        term = WatchTerm(keyword="Aiko", language_hint="ja")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"language_hint": None})
        assert resp.status_code == 200
        assert resp.json()["language_hint"] is None
        db_session.refresh(term)
        assert term.language_hint is None

    def test_update_omitted_field_leaves_language_hint_unchanged(self, client, db_session):
        term = WatchTerm(keyword="Aiko", language_hint="ja")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"is_active": False})
        assert resp.status_code == 200
        assert resp.json()["language_hint"] == "ja"

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

    def test_retired_collection_mode_update_triggers_poll(self, client, db_session):
        term = WatchTerm(
            keyword="Aiko",
            collection_mode="all_info",
            last_polled_at=datetime.now(timezone.utc),
        )
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"collection_mode": "media_only"})
        assert resp.status_code == 200
        assert resp.json()["collection_mode"] == "all_info"
        mock_poll.assert_called_once()
        db_session.refresh(term)
        assert term.last_polled_at is None

    def test_expanding_selected_sources_makes_term_due_and_triggers_poll(self, client, db_session):
        term = WatchTerm(
            keyword="Aiko",
            source_mode="selected",
            selected_platforms=["youtube"],
            last_polled_at=datetime.now(timezone.utc),
        )
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.patch(
                f"/api/watch-terms/{term.id}",
                json={"selected_platforms": ["youtube", "note"]},
            )

        assert resp.status_code == 200
        assert resp.json()["selected_platforms"] == ["note", "youtube"]
        mock_poll.assert_called_once()
        db_session.refresh(term)
        assert term.last_polled_at is None

    def test_normalizes_retired_collection_mode_on_update(self, client, db_session):
        term = WatchTerm(keyword="Aiko", collection_mode="all_info")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"collection_mode": "media_only"})
        assert resp.status_code == 200
        assert resp.json()["collection_mode"] == "all_info"

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

    def test_muting_term_clears_pending_notification(self, client, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.flush()
        db_session.add(PendingNotification(watch_term_id=term.id, new_count=2))
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"notify_on_new": False})

        assert resp.status_code == 200
        assert resp.json()["notify_on_new"] is False
        assert db_session.get(PendingNotification, term.id) is None
        mock_poll.assert_not_called()

    def test_deactivating_term_clears_pending_notification(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=True, notify_on_new=True)
        db_session.add(term)
        db_session.flush()
        db_session.add(PendingNotification(watch_term_id=term.id, new_count=2))
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"is_active": False})

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
        assert db_session.get(PendingNotification, term.id) is None
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

    def test_device_update_notify_term_recovers_via_inline_reverification(self, client, db_session):
        secret = "device-secret-value"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        token = "c" * 64
        term = WatchTerm(keyword="Aiko", notify_on_new=False, owner_device_secret=owner_secret)
        unverified_device = APNSDeviceToken(
            token=token,
            environment="sandbox",
            device_secret=owner_secret,
            is_verified=False,
            last_seen_at=datetime.now(timezone.utc),
        )
        db_session.add_all([term, unverified_device])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"), \
             patch(
                 "app.apns.validate_device_registration_result",
                 return_value=(True, None),
             ):
            resp = client.patch(
                f"/api/watch-terms/{term.id}",
                json={"notify_on_new": True},
                headers={"X-Device-Secret": secret},
            )

        assert resp.status_code == 200
        assert resp.json()["notify_on_new"] is True
        db_session.refresh(unverified_device)
        assert unverified_device.is_verified is True

    def test_device_update_notify_term_inline_reverification_fails_returns_409(self, client, db_session):
        secret = "device-secret-value"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        token = "d" * 64
        term = WatchTerm(keyword="Aiko", notify_on_new=False, owner_device_secret=owner_secret)
        unverified_device = APNSDeviceToken(
            token=token,
            environment="sandbox",
            device_secret=owner_secret,
            is_verified=False,
            last_seen_at=datetime.now(timezone.utc),
        )
        db_session.add_all([term, unverified_device])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"), \
             patch(
                 "app.apns.validate_device_registration_result",
                 return_value=(False, "BadDeviceToken"),
             ):
            resp = client.patch(
                f"/api/watch-terms/{term.id}",
                json={"notify_on_new": True},
                headers={"X-Device-Secret": secret},
            )

        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "notification_device_required"
        db_session.refresh(term)
        assert term.notify_on_new is False

    def test_device_update_notify_term_stale_unverified_token_skipped(self, client, db_session):
        secret = "device-secret-value"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        token = "e" * 64
        term = WatchTerm(keyword="Aiko", notify_on_new=False, owner_device_secret=owner_secret)
        stale_device = APNSDeviceToken(
            token=token,
            environment="sandbox",
            device_secret=owner_secret,
            is_verified=False,
            last_seen_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db_session.add_all([term, stale_device])
        db_session.commit()

        with patch.object(settings, "admin_api_token", "admin-secret"), \
             patch("app.api.watch_terms.queue_poll"), \
             patch(
                 "app.apns.validate_device_registration_result",
             ) as mock_verify:
            resp = client.patch(
                f"/api/watch-terms/{term.id}",
                json={"notify_on_new": True},
                headers={"X-Device-Secret": secret},
            )

        assert resp.status_code == 409
        mock_verify.assert_not_called()


class TestTriggerNotification:
    def test_unwraps_queued_items_preview_before_sending(self, client, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.flush()
        db_session.add(PendingNotification(
            watch_term_id=term.id,
            new_count=2,
            preview_item={
                "items": [
                    {
                        "id": "note:1",
                        "title": "First note",
                        "url": "https://note.com/1",
                        "published_at": "2026-08-01T00:00:00+00:00",
                    },
                    {
                        "id": "note:2",
                        "title": "Second note",
                        "url": "https://note.com/2",
                        "published_at": "2026-08-02T00:00:00+00:00",
                    },
                ]
            },
        ))
        db_session.commit()

        mock_send = AsyncMock(return_value=True)
        with patch("app.apns.apns_configured", return_value=True), \
             patch("app.apns.send_new_match_notifications", new=mock_send):
            resp = client.post(f"/api/watch-terms/{term.id}/notify")

        assert resp.status_code == 200
        assert resp.json() == {"term_id": term.id, "keyword": "Aiko", "count": 2, "cleared": True}
        mock_send.assert_awaited_once()
        _, sent_term, count, preview = mock_send.await_args.args
        assert sent_term.id == term.id
        assert count == 2
        assert "items" not in preview
        assert preview["title"] in {"First note", "Second note"}
        assert preview["url"] in {"https://note.com/1", "https://note.com/2"}

    def test_returns_409_when_no_pending_content(self, client, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=True)
        db_session.add(term)
        db_session.commit()

        with patch("app.apns.apns_configured", return_value=True):
            resp = client.post(f"/api/watch-terms/{term.id}/notify")

        assert resp.status_code == 409


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
