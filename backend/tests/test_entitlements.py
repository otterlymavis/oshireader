"""Tests for the /api/entitlements endpoints."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from appstoreserverlibrary.models.Environment import Environment

from app.config import settings
from app.entitlements import EntitlementVerificationError
from app.models import DeviceEntitlement

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


def _configure_product_ids():
    original = settings.plus_subscription_product_ids
    settings.plus_subscription_product_ids = _PRODUCT_ID
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
        original = settings.plus_subscription_product_ids
        settings.plus_subscription_product_ids = ""
        try:
            with _device_auth(), patch("app.api.entitlements.verify_signed_transaction", return_value=_FakeTransaction()):
                resp = client.post(
                    "/api/entitlements/verify",
                    json={"signed_transaction": "jws"},
                    headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                )
        finally:
            settings.plus_subscription_product_ids = original
        assert resp.status_code == 503

    def test_rejects_unrecognized_product_id(self, client):
        original = _configure_product_ids()
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
            settings.plus_subscription_product_ids = original
        assert resp.status_code == 422

    def test_verified_transaction_upserts_entitlement(self, client, db_session):
        original = _configure_product_ids()
        try:
            with _device_auth(), patch("app.api.entitlements.verify_signed_transaction", return_value=_FakeTransaction()):
                resp = client.post(
                    "/api/entitlements/verify",
                    json={"signed_transaction": "jws"},
                    headers={"X-Device-Secret": _DEVICE_SECRET_HEADER},
                )
        finally:
            settings.plus_subscription_product_ids = original

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_active"] is True
        assert body["product_id"] == _PRODUCT_ID

        stored = db_session.get(DeviceEntitlement, _OWNER_DEVICE_SECRET)
        assert stored is not None
        assert stored.original_transaction_id == "orig-1"
        assert stored.environment == "production"

    def test_verify_twice_updates_existing_row_not_duplicate(self, client, db_session):
        original = _configure_product_ids()
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
            settings.plus_subscription_product_ids = original

        assert db_session.query(DeviceEntitlement).count() == 1
        stored = db_session.get(DeviceEntitlement, _OWNER_DEVICE_SECRET)
        assert stored.latest_transaction_id == "txn-2"

    def test_revoked_transaction_stores_inactive_entitlement(self, client, db_session):
        original = _configure_product_ids()
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
            settings.plus_subscription_product_ids = original

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False


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

    def test_active_entitlement_returns_active(self, client, db_session):
        db_session.add(DeviceEntitlement(
            owner_device_secret=_OWNER_DEVICE_SECRET,
            product_id=_PRODUCT_ID,
            original_transaction_id="orig-1",
            latest_transaction_id="txn-1",
            purchase_date=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        ))
        db_session.commit()

        with _device_auth():
            resp = client.get("/api/entitlements/status", headers={"X-Device-Secret": _DEVICE_SECRET_HEADER})
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_active"] is True
        assert body["product_id"] == _PRODUCT_ID
