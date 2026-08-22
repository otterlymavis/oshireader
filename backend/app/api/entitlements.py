from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_admin_or_device_auth
from app.config import settings
from app.database import get_db
from app.entitlements import (
    DecodedEntitlement,
    EntitlementVerificationError,
    clear_paused_pending_notifications,
    push_delivery_status,
    verify_signed_notification,
    verify_signed_transaction,
)
from app.models import DeviceEntitlement
from app.schemas import AppStoreNotificationRequest, EntitlementStatusOut, EntitlementVerifyRequest

router = APIRouter(prefix="/api/entitlements", tags=["entitlements"])
log = logging.getLogger(__name__)


def _status_out(
    db: Session,
    owner_device_secret: str,
    entitlement: DeviceEntitlement | None,
) -> EntitlementStatusOut:
    state, limit, count = push_delivery_status(db, owner_device_secret)
    if entitlement is None:
        return EntitlementStatusOut(
            is_active=False,
            push_term_limit=limit,
            push_term_count=count,
            push_delivery_state=state,
        )
    return EntitlementStatusOut(
        is_active=entitlement.is_active,
        product_id=entitlement.product_id,
        expires_at=entitlement.expires_at,
        push_term_limit=limit,
        push_term_count=count,
        push_delivery_state=state,
    )


@router.get("/status", response_model=EntitlementStatusOut)
def get_status(
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
):
    if auth.device_secret is None:
        raise HTTPException(400, "a device secret is required")
    entitlement = db.get(DeviceEntitlement, auth.device_secret)
    return _status_out(db, auth.device_secret, entitlement)


def _apply_decoded_entitlement(
    entitlement: DeviceEntitlement,
    decoded: DecodedEntitlement,
    push_term_limit: int,
) -> None:
    entitlement.product_id = decoded.product_id
    entitlement.environment = decoded.environment
    entitlement.original_transaction_id = decoded.original_transaction_id
    entitlement.latest_transaction_id = decoded.latest_transaction_id
    entitlement.purchase_date = decoded.purchase_date
    entitlement.expires_at = decoded.expires_at
    entitlement.revoked_at = decoded.revoked_at
    entitlement.push_term_limit = push_term_limit


@router.post("/verify", response_model=EntitlementStatusOut)
def verify(
    body: EntitlementVerifyRequest,
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
):
    if auth.device_secret is None:
        raise HTTPException(400, "a device secret is required")

    try:
        payload = verify_signed_transaction(body.signed_transaction)
        decoded = DecodedEntitlement(payload)
    except EntitlementVerificationError as exc:
        log.warning("StoreKit transaction verification failed: %s", exc)
        raise HTTPException(422, "transaction could not be verified") from exc

    tier_limits = settings.plus_subscription_tier_limits
    if not tier_limits:
        raise HTTPException(503, "plus_subscription_tiers is not configured")
    push_term_limit = tier_limits.get(decoded.product_id)
    if push_term_limit is None:
        raise HTTPException(422, "product_id is not a recognized Plus subscription")

    entitlement = db.get(DeviceEntitlement, auth.device_secret)
    if entitlement is None:
        entitlement = DeviceEntitlement(owner_device_secret=auth.device_secret)
        db.add(entitlement)
    _apply_decoded_entitlement(entitlement, decoded, push_term_limit)
    db.flush()
    clear_paused_pending_notifications(db, auth.device_secret)
    db.commit()
    db.refresh(entitlement)
    return _status_out(db, auth.device_secret, entitlement)


@router.post("/apple-notifications")
def apple_notifications(
    body: AppStoreNotificationRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Apply a cryptographically verified App Store Server Notification V2."""
    try:
        notification = verify_signed_notification(body.signedPayload)
        signed_transaction = getattr(getattr(notification, "data", None), "signedTransactionInfo", None)
        if not signed_transaction:
            return {"accepted": True, "updated": 0}
        decoded = DecodedEntitlement(verify_signed_transaction(signed_transaction))
    except EntitlementVerificationError as exc:
        log.warning("App Store server notification verification failed: %s", exc)
        raise HTTPException(422, "notification could not be verified") from exc

    tier_limit = settings.plus_subscription_tier_limits.get(decoded.product_id)
    if tier_limit is None:
        raise HTTPException(422, "product_id is not a recognized push product")

    entitlements = (
        db.query(DeviceEntitlement)
        .filter(DeviceEntitlement.original_transaction_id == decoded.original_transaction_id)
        .all()
    )
    for entitlement in entitlements:
        _apply_decoded_entitlement(entitlement, decoded, tier_limit)
    db.flush()
    for entitlement in entitlements:
        clear_paused_pending_notifications(db, entitlement.owner_device_secret)
    db.commit()
    return {
        "accepted": True,
        "updated": len(entitlements),
        "notification_type": str(getattr(notification, "rawNotificationType", None) or ""),
    }
