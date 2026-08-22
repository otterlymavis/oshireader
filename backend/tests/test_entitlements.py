"""Tests for the /api/entitlements endpoints."""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from types import SimpleNamespace

from appstoreserverlibrary.models.Environment import Environment
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.entitlements import _upsert_entitlement_atomically
from app.config import settings
from app.database import Base
from app.entitlements import DecodedEntitlement, EntitlementVerificationError
from app.models import DeviceEntitlement, WatchTerm

_DEVICE_SECRET_HEADER = "plus-device-secret"
_OWNER_DEVICE_SECRET = hashlib.sha256(_DEVICE_SECRET_HEADER.encode()).hexdigest()
_PRODUCT_ID = "com.otterpia.oshireader.plus.monthly"


class _FakeTransaction:
    def __init__(self, **overrides):
        self.originalTransactionId = "orig-1"
        self.transactionId = "txn-1"
        self.productId = _PRODUCT_ID
        self.environment = Environment.PRODUCTION
        self.purchaseDate = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.expiresDate = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000)
        self.revocationDate = None
        self.__dict__.update(overrides)


def _configure_product_tiers(limit: int = 3):
    original = settings.plus_subscription_tiers
    settings.plus_subscription_tiers = f"{_PRODUCT_ID}:{limit}"
    return original


def _device_auth():
    """require_admin_or_device_auth only takes the device path when an admin
    token is configured (see auth.py) — the default test client leaves it
    unset and treats every request as admin, so every test here patches it."""
    return patch.object(settings, "admin_api_token", "admin-secret")


class TestVerifyEntitlement:
    def test_requires_device_secret(self, client):
        with _device_auth():
            resp = client.post("/api/entitlements/verify", json={"signed_transaction": "x"})
        assert resp.status_code == 401

    def test_rejects_unverifiable_transaction(self, client):
        with _device_auth(), patch(
            "app.api.entitlements.verify_signed_transaction",
            side_effect=EntitlementVerificationError("bad signature"),
        ):
            resp = client.post(
                "/api/entitlements/verify",
                json={"signed_transaction": "not-a-real-jws"},
                headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
            )
        assert resp.status_code == 422

    def test_rejects_when_product_ids_not_configured(self, client):
        original = settings.plus_subscription_tiers
        settings.plus_subscription_tiers = ""
        try:
            with _device_auth(), patch("app.api.entitlements.verify_signed_transaction", return_value=_FakeTransaction()):
                resp = client.post(
                    "/api/entitlements/verify",
                    json={"signed_transaction": "jws"},
                    headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                )
        finally:
            settings.plus_subscription_tiers = original
        assert resp.status_code == 503

    def test_rejects_unrecognized_product_id(self, client):
        original = _configure_product_tiers()
        try:
            with _device_auth(), patch(
                "app.api.entitlements.verify_signed_transaction",
                return_value=_FakeTransaction(productId="com.otterpia.oshireader.other"),
            ):
                resp = client.post(
                    "/api/entitlements/verify",
                    json={"signed_transaction": "jws"},
                    headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                )
        finally:
            settings.plus_subscription_tiers = original
        assert resp.status_code == 422

    def test_verified_transaction_upserts_entitlement(self, client, db_session):
        original = _configure_product_tiers(10)
        try:
            with _device_auth(), patch("app.api.entitlements.verify_signed_transaction", return_value=_FakeTransaction()):
                resp = client.post(
                    "/api/entitlements/verify",
                    json={"signed_transaction": "jws"},
                    headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                )
        finally:
            settings.plus_subscription_tiers = original

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_active"] is True
        assert body["product_id"] == _PRODUCT_ID
        assert body["push_term_limit"] == 10

        stored = db_session.get(DeviceEntitlement, _OWNER_DEVICE_SECRET)
        assert stored is not None
        assert stored.original_transaction_id == "orig-1"
        assert stored.environment == "production"
        assert stored.push_term_limit == 10

    def test_verify_twice_updates_existing_row_not_duplicate(self, client, db_session):
        original = _configure_product_tiers()
        try:
            with _device_auth():
                with patch("app.api.entitlements.verify_signed_transaction", return_value=_FakeTransaction()):
                    client.post(
                        "/api/entitlements/verify",
                        json={"signed_transaction": "jws"},
                        headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                    )
                with patch(
                    "app.api.entitlements.verify_signed_transaction",
                    return_value=_FakeTransaction(transactionId="txn-2"),
                ):
                    client.post(
                        "/api/entitlements/verify",
                        json={"signed_transaction": "jws-2"},
                        headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                    )
        finally:
            settings.plus_subscription_tiers = original

        assert db_session.query(DeviceEntitlement).count() == 1
        stored = db_session.get(DeviceEntitlement, _OWNER_DEVICE_SECRET)
        assert stored.latest_transaction_id == "txn-2"

    def test_revoked_transaction_stores_inactive_entitlement(self, client, db_session):
        original = _configure_product_tiers()
        try:
            with _device_auth(), patch(
                "app.api.entitlements.verify_signed_transaction",
                return_value=_FakeTransaction(revocationDate=int(datetime.now(timezone.utc).timestamp() * 1000)),
            ):
                resp = client.post(
                    "/api/entitlements/verify",
                    json={"signed_transaction": "jws"},
                    headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                )
        finally:
            settings.plus_subscription_tiers = original

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
        assert resp.json()["push_term_limit"] == 0

    def test_replayed_purchase_cannot_clear_a_revocation(self, client, db_session):
        purchase_date = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
        revocation_date = int(datetime.now(timezone.utc).timestamp() * 1000)
        original = _configure_product_tiers()
        try:
            with _device_auth():
                with patch(
                    "app.api.entitlements.verify_signed_transaction",
                    return_value=_FakeTransaction(
                        purchaseDate=purchase_date,
                        revocationDate=revocation_date,
                    ),
                ):
                    client.post(
                        "/api/entitlements/verify",
                        json={"signed_transaction": "revoked-jws"},
                        headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                    )
                with patch(
                    "app.api.entitlements.verify_signed_transaction",
                    return_value=_FakeTransaction(
                        purchaseDate=purchase_date,
                        revocationDate=None,
                        expiresDate=None,
                    ),
                ):
                    resp = client.post(
                        "/api/entitlements/verify",
                        json={"signed_transaction": "original-purchase-jws"},
                        headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                    )
        finally:
            settings.plus_subscription_tiers = original

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
        stored = db_session.get(DeviceEntitlement, _OWNER_DEVICE_SECRET)
        assert stored.revoked_at is not None
        assert stored.expires_at is not None

    def test_newer_purchase_after_revocation_reactivates_entitlement(self, client, db_session):
        old_purchase = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp() * 1000)
        new_purchase = int(datetime.now(timezone.utc).timestamp() * 1000)
        revocation_date = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
        original = _configure_product_tiers(10)
        try:
            with _device_auth():
                with patch(
                    "app.api.entitlements.verify_signed_transaction",
                    return_value=_FakeTransaction(
                        transactionId="refunded",
                        purchaseDate=old_purchase,
                        revocationDate=revocation_date,
                    ),
                ):
                    client.post(
                        "/api/entitlements/verify",
                        json={"signed_transaction": "refunded-jws"},
                        headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                    )
                with patch(
                    "app.api.entitlements.verify_signed_transaction",
                    return_value=_FakeTransaction(
                        transactionId="repurchased",
                        purchaseDate=new_purchase,
                        revocationDate=None,
                        expiresDate=None,
                    ),
                ):
                    resp = client.post(
                        "/api/entitlements/verify",
                        json={"signed_transaction": "repurchase-jws"},
                        headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                    )
        finally:
            settings.plus_subscription_tiers = original

        assert resp.status_code == 200
        assert resp.json()["is_active"] is True
        stored = db_session.get(DeviceEntitlement, _OWNER_DEVICE_SECRET)
        assert stored.latest_transaction_id == "repurchased"
        assert stored.revoked_at is None
        assert stored.expires_at is None
        assert stored.push_term_limit == 10

    def test_concurrent_refund_and_replay_leave_entitlement_revoked(self, tmp_path):
        engine = create_engine(
            f"sqlite:///{tmp_path / 'entitlement-race.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        purchase_date = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
        revocation_date = int(datetime.now(timezone.utc).timestamp() * 1000)
        seed = Session()
        seed.add(DeviceEntitlement(
            owner_device_secret=_OWNER_DEVICE_SECRET,
            product_id=_PRODUCT_ID,
            original_transaction_id="orig-1",
            latest_transaction_id="txn-1",
            purchase_date=datetime.fromtimestamp(purchase_date / 1000, timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            push_term_limit=3,
        ))
        seed.commit()
        seed.close()

        barrier = threading.Barrier(2)
        errors = []

        def apply(transaction):
            session = Session()
            try:
                barrier.wait(timeout=5)
                _upsert_entitlement_atomically(
                    session,
                    _OWNER_DEVICE_SECRET,
                    DecodedEntitlement(transaction),
                    3,
                    create_if_missing=False,
                )
                session.commit()
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                session.close()

        refund = threading.Thread(target=apply, args=(_FakeTransaction(
            purchaseDate=purchase_date,
            revocationDate=revocation_date,
        ),))
        replay = threading.Thread(target=apply, args=(_FakeTransaction(
            purchaseDate=purchase_date,
            revocationDate=None,
        ),))
        refund.start()
        replay.start()
        refund.join(timeout=10)
        replay.join(timeout=10)

        assert not refund.is_alive()
        assert not replay.is_alive()
        assert errors == []
        verify_session = Session()
        try:
            stored = verify_session.get(DeviceEntitlement, _OWNER_DEVICE_SECRET)
            assert stored.revoked_at is not None
        finally:
            verify_session.close()
            engine.dispose()

    def test_non_consumable_without_expiry_is_lifetime_active(self, client, db_session):
        original = _configure_product_tiers(3)
        try:
            with _device_auth(), patch(
                "app.api.entitlements.verify_signed_transaction",
                return_value=_FakeTransaction(expiresDate=None),
            ):
                resp = client.post(
                    "/api/entitlements/verify",
                    json={"signed_transaction": "lifetime-jws"},
                    headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                )
        finally:
            settings.plus_subscription_tiers = original
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True
        assert resp.json()["push_term_limit"] == 3
        assert db_session.get(DeviceEntitlement, _OWNER_DEVICE_SECRET).expires_at is None


class TestEntitlementStatus:
    def test_requires_device_secret(self, client):
        with _device_auth():
            resp = client.get("/api/entitlements/status")
        assert resp.status_code == 401

    def test_no_entitlement_returns_inactive(self, client):
        with _device_auth():
            resp = client.get("/api/entitlements/status", headers={"X-Device-Secret": _DEVICE_SECRET_HEADER})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
        assert resp.json()["push_term_limit"] == 0

    def test_active_entitlement_returns_active(self, client, db_session):
        db_session.add(DeviceEntitlement(
            owner_device_secret=_OWNER_DEVICE_SECRET,
            product_id=_PRODUCT_ID,
            original_transaction_id="orig-1",
            latest_transaction_id="txn-1",
            purchase_date=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            push_term_limit=3,
        ))
        db_session.commit()

        with _device_auth():
            resp = client.get("/api/entitlements/status", headers={"X-Device-Secret": _DEVICE_SECRET_HEADER})
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_active"] is True
        assert body["product_id"] == _PRODUCT_ID
        assert body["push_term_limit"] == 3

    def test_over_limit_requires_selection(self, client, db_session):
        db_session.add(DeviceEntitlement(
            owner_device_secret=_OWNER_DEVICE_SECRET,
            product_id=_PRODUCT_ID,
            original_transaction_id="orig-1",
            latest_transaction_id="txn-1",
            purchase_date=datetime.now(timezone.utc),
            expires_at=None,
            push_term_limit=1,
        ))
        db_session.add_all([
            WatchTerm(keyword="Aiko", owner_device_secret=_OWNER_DEVICE_SECRET, notify_on_new=True),
            WatchTerm(keyword="Miku", owner_device_secret=_OWNER_DEVICE_SECRET, notify_on_new=True),
        ])
        db_session.commit()
        with _device_auth():
            resp = client.get("/api/entitlements/status", headers={"X-Device-Secret": _DEVICE_SECRET_HEADER})
        assert resp.status_code == 200
        assert resp.json()["push_term_count"] == 2
        assert resp.json()["push_term_limit"] == 1
        assert resp.json()["push_delivery_state"] == "selection_required"


class TestAppStoreServerNotifications:
    def test_rejects_unverified_notification(self, client):
        with patch(
            "app.api.entitlements.verify_signed_notification",
            side_effect=EntitlementVerificationError("bad"),
        ):
            resp = client.post("/api/entitlements/apple-notifications", json={"signedPayload": "bad"})
        assert resp.status_code == 422

    def test_updates_every_device_for_original_transaction(self, client, db_session):
        owners = ["owner-a", "owner-b"]
        for owner in owners:
            db_session.add(DeviceEntitlement(
                owner_device_secret=owner,
                product_id=_PRODUCT_ID,
                original_transaction_id="orig-1",
                latest_transaction_id="old",
                purchase_date=datetime.now(timezone.utc) - timedelta(days=30),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                push_term_limit=3,
            ))
        db_session.commit()
        notification = SimpleNamespace(
            data=SimpleNamespace(signedTransactionInfo="transaction-jws"),
            rawNotificationType="DID_RENEW",
        )
        original = _configure_product_tiers(10)
        try:
            with patch("app.api.entitlements.verify_signed_notification", return_value=notification), \
                 patch(
                     "app.api.entitlements.verify_signed_transaction",
                     return_value=_FakeTransaction(transactionId="new", expiresDate=None),
                 ):
                resp = client.post(
                    "/api/entitlements/apple-notifications",
                    json={"signedPayload": "notification-jws"},
                )
        finally:
            settings.plus_subscription_tiers = original
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2
        for owner in owners:
            stored = db_session.get(DeviceEntitlement, owner)
            db_session.refresh(stored)
            assert stored.latest_transaction_id == "new"
            assert stored.push_term_limit == 10
            assert stored.expires_at is None

    def test_ignores_notification_for_an_older_transaction(self, client, db_session):
        current_purchase = datetime.now(timezone.utc)
        current_expiry = current_purchase + timedelta(days=30)
        db_session.add(DeviceEntitlement(
            owner_device_secret="owner-a",
            product_id=_PRODUCT_ID,
            original_transaction_id="orig-1",
            latest_transaction_id="current",
            purchase_date=current_purchase,
            expires_at=current_expiry,
            push_term_limit=10,
        ))
        db_session.commit()
        notification = SimpleNamespace(
            data=SimpleNamespace(signedTransactionInfo="old-transaction-jws"),
            rawNotificationType="DID_RENEW",
        )
        old_purchase = int((current_purchase - timedelta(days=30)).timestamp() * 1000)
        old_expiry = int((current_purchase - timedelta(days=1)).timestamp() * 1000)
        original = _configure_product_tiers(3)
        try:
            with patch("app.api.entitlements.verify_signed_notification", return_value=notification), \
                 patch(
                     "app.api.entitlements.verify_signed_transaction",
                     return_value=_FakeTransaction(
                         transactionId="old",
                         purchaseDate=old_purchase,
                         expiresDate=old_expiry,
                     ),
                 ):
                resp = client.post(
                    "/api/entitlements/apple-notifications",
                    json={"signedPayload": "notification-jws"},
                )
        finally:
            settings.plus_subscription_tiers = original

        assert resp.status_code == 200
        assert resp.json()["updated"] == 0
        stored = db_session.get(DeviceEntitlement, "owner-a")
        db_session.refresh(stored)
        assert stored.latest_transaction_id == "current"
        assert stored.push_term_limit == 10
        assert stored.expires_at.replace(tzinfo=timezone.utc) == current_expiry
