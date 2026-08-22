"""StoreKit 2 transaction verification for the "Plus" refresh-tier subscription.

Verifies signed transactions against Apple's certificate chain via the official
appstoreserverlibrary rather than trusting anything the client asserts — see
app/api/watch_terms.py for why refresh_tier is never taken from the client
directly. The Apple Root CA - G3 certificate bundled in app/certs was obtained
from https://www.apple.com/certificateauthority/ per the library's setup docs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.models.JWSTransactionDecodedPayload import JWSTransactionDecodedPayload
from appstoreserverlibrary.signed_data_verifier import (
    SignedDataVerifier,
    VerificationException,
    VerificationStatus,
)

from app.config import settings
from app.models import DeviceEntitlement, PendingNotification, WatchTerm
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_ROOT_CERT_PATH = Path(__file__).parent / "certs" / "AppleRootCA-G3.cer"


class EntitlementVerificationError(Exception):
    """Raised when a signed transaction can't be verified as a genuine Apple purchase."""


@lru_cache(maxsize=1)
def _root_certificates() -> list[bytes]:
    return [_ROOT_CERT_PATH.read_bytes()]


@lru_cache(maxsize=1)
def _production_verifier() -> SignedDataVerifier:
    if not settings.app_store_apple_id:
        raise EntitlementVerificationError("app_store_apple_id is not configured")
    return SignedDataVerifier(
        _root_certificates(),
        enable_online_checks=True,
        environment=Environment.PRODUCTION,
        bundle_id=settings.app_store_bundle_id,
        app_apple_id=int(settings.app_store_apple_id),
    )


@lru_cache(maxsize=1)
def _sandbox_verifier() -> SignedDataVerifier:
    return SignedDataVerifier(
        _root_certificates(),
        enable_online_checks=True,
        environment=Environment.SANDBOX,
        bundle_id=settings.app_store_bundle_id,
    )


def verify_signed_transaction(signed_transaction: str) -> JWSTransactionDecodedPayload:
    """Verify a StoreKit 2 signedTransaction JWS and return its decoded payload.

    Tries the production verifier first, then falls back to sandbox — a
    TestFlight or Xcode-run build signs transactions in the sandbox
    environment, and the client has no trustworthy way to declare which
    environment it's in ahead of verification.
    """
    try:
        return _production_verifier().verify_and_decode_signed_transaction(signed_transaction)
    except EntitlementVerificationError:
        pass
    except VerificationException as exc:
        if exc.status != VerificationStatus.INVALID_ENVIRONMENT:
            raise EntitlementVerificationError(f"verification failed: {exc.status.name}") from exc

    try:
        return _sandbox_verifier().verify_and_decode_signed_transaction(signed_transaction)
    except VerificationException as exc:
        raise EntitlementVerificationError(f"verification failed: {exc.status.name}") from exc


def verify_signed_notification(signed_payload: str):
    """Verify an App Store Server Notification V2 in either environment."""
    try:
        return _production_verifier().verify_and_decode_notification(signed_payload)
    except EntitlementVerificationError:
        pass
    except VerificationException as exc:
        if exc.status != VerificationStatus.INVALID_ENVIRONMENT:
            raise EntitlementVerificationError(f"notification verification failed: {exc.status.name}") from exc

    try:
        return _sandbox_verifier().verify_and_decode_notification(signed_payload)
    except VerificationException as exc:
        raise EntitlementVerificationError(f"notification verification failed: {exc.status.name}") from exc


def _epoch_ms_to_datetime(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


class DecodedEntitlement:
    """Plain fields pulled out of a verified transaction, ready to persist."""

    def __init__(self, payload: JWSTransactionDecodedPayload):
        if payload.originalTransactionId is None or payload.transactionId is None or payload.productId is None:
            raise EntitlementVerificationError("verified transaction is missing required fields")
        self.product_id = payload.productId
        self.original_transaction_id = payload.originalTransactionId
        self.latest_transaction_id = payload.transactionId
        self.environment = "sandbox" if payload.environment == Environment.SANDBOX else "production"
        self.purchase_date = _epoch_ms_to_datetime(payload.purchaseDate) or datetime.now(timezone.utc)
        self.expires_at = _epoch_ms_to_datetime(payload.expiresDate)
        self.revoked_at = _epoch_ms_to_datetime(payload.revocationDate)


def push_term_count(db: Session, owner_device_secret: str | None) -> int:
    if owner_device_secret is None:
        return 0
    return (
        db.query(WatchTerm)
        .filter(
            WatchTerm.owner_device_secret == owner_device_secret,
            WatchTerm.notify_on_new == True,  # noqa: E712
        )
        .count()
    )


def push_delivery_status(
    db: Session,
    owner_device_secret: str | None,
) -> tuple[str, int, int]:
    count = push_term_count(db, owner_device_secret)
    if owner_device_secret is None:
        return "active", count, count
    entitlement = db.get(DeviceEntitlement, owner_device_secret)
    if entitlement is None or not entitlement.is_active or entitlement.push_term_limit <= 0:
        return "inactive", 0, count
    limit = max(0, entitlement.push_term_limit)
    if count > limit:
        return "selection_required", limit, count
    return "active", limit, count


def push_delivery_allowed(db: Session, term: WatchTerm) -> bool:
    if term.owner_device_secret is None:
        return True
    state, _, _ = push_delivery_status(db, term.owner_device_secret)
    return state == "active"


def backend_access_allowed(db: Session, owner_device_secret: str | None) -> bool:
    """Return whether a device may consume hosted polling/feed resources."""
    if owner_device_secret is None:
        return False
    entitlement = db.get(DeviceEntitlement, owner_device_secret)
    return entitlement is not None and entitlement.is_active


def backend_access_owner_secrets(db: Session, owner_device_secrets: set[str]) -> set[str]:
    """Return active hosted-backend owners with one query for scheduler/health paths."""
    if not owner_device_secrets:
        return set()
    entitlements = (
        db.query(DeviceEntitlement)
        .filter(DeviceEntitlement.owner_device_secret.in_(owner_device_secrets))
        .all()
    )
    return {
        entitlement.owner_device_secret
        for entitlement in entitlements
        if entitlement.is_active
    }


def clear_paused_pending_notifications(db: Session, owner_device_secret: str) -> int:
    state, _, _ = push_delivery_status(db, owner_device_secret)
    if state == "active":
        return 0
    term_ids = [
        term_id
        for (term_id,) in (
            db.query(WatchTerm.id)
            .filter(WatchTerm.owner_device_secret == owner_device_secret)
            .all()
        )
    ]
    if not term_ids:
        return 0
    return (
        db.query(PendingNotification)
        .filter(PendingNotification.watch_term_id.in_(term_ids))
        .delete(synchronize_session=False)
    )
