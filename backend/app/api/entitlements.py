from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_admin_or_device_auth
from app.config import settings
from app.database import get_db
from app.entitlements import DecodedEntitlement, EntitlementVerificationError, verify_signed_transaction
from app.models import DeviceEntitlement
from app.schemas import EntitlementStatusOut, EntitlementVerifyRequest

router = APIRouter(prefix="/api/entitlements", tags=["entitlements"])
log = logging.getLogger(__name__)


def _status_out(entitlement: DeviceEntitlement | None) -> EntitlementStatusOut:
    if entitlement is None:
        return EntitlementStatusOut(is_active=False)
    return EntitlementStatusOut(
        is_active=entitlement.is_active,
        product_id=entitlement.product_id,
        expires_at=entitlement.expires_at,
    )


@router.get("/status", response_model=EntitlementStatusOut)
def get_status(
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
):
    if auth.device_secret is None:
        raise HTTPException(400, "a device secret is required")
    entitlement = db.get(DeviceEntitlement, auth.device_secret)
    return _status_out(entitlement)


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

    allowed_products = settings.plus_subscription_product_id_set
    if not allowed_products:
        raise HTTPException(503, "plus_subscription_product_ids is not configured")
    if decoded.product_id not in allowed_products:
        raise HTTPException(422, "product_id is not a recognized Plus subscription")

    entitlement = db.get(DeviceEntitlement, auth.device_secret)
    if entitlement is None:
        entitlement = DeviceEntitlement(owner_device_secret=auth.device_secret)
        db.add(entitlement)
    entitlement.product_id = decoded.product_id
    entitlement.environment = decoded.environment
    entitlement.original_transaction_id = decoded.original_transaction_id
    entitlement.latest_transaction_id = decoded.latest_transaction_id
    entitlement.purchase_date = decoded.purchase_date
    entitlement.expires_at = decoded.expires_at
    entitlement.revoked_at = decoded.revoked_at
    db.commit()
    db.refresh(entitlement)
    return _status_out(entitlement)
