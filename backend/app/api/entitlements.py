from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
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
        product_id=entitlement.effective_product_id,
        expires_at=entitlement.effective_expires_at,
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


def _entitlement_values(
    decoded: DecodedEntitlement,
    push_term_limit: int,
) -> dict:
    return {
        "product_id": decoded.product_id,
        "environment": decoded.environment,
        "original_transaction_id": decoded.original_transaction_id,
        "latest_transaction_id": decoded.latest_transaction_id,
        "purchase_date": decoded.purchase_date,
        "expires_at": decoded.expires_at,
        "revoked_at": decoded.revoked_at,
        "push_term_limit": push_term_limit,
    }


def _permanent_entitlement_values(
    decoded: DecodedEntitlement,
    push_term_limit: int,
) -> dict:
    return {
        "permanent_product_id": decoded.product_id,
        "permanent_environment": decoded.environment,
        "permanent_original_transaction_id": decoded.original_transaction_id,
        "permanent_latest_transaction_id": decoded.latest_transaction_id,
        "permanent_purchase_date": decoded.purchase_date,
        "permanent_revoked_at": decoded.revoked_at,
        "permanent_push_term_limit": push_term_limit,
    }


def _load_entitlement(db: Session, owner_device_secret: str) -> DeviceEntitlement | None:
    return (
        db.query(DeviceEntitlement)
        .filter(DeviceEntitlement.owner_device_secret == owner_device_secret)
        .populate_existing()
        .one_or_none()
    )


def _update_entitlement_atomically(
    db: Session,
    owner_device_secret: str,
    decoded: DecodedEntitlement,
    push_term_limit: int,
) -> bool:
    """Apply a transaction only if the stored row is not newer.

    StoreKit transaction JWS values remain cryptographically valid after a
    refund. A client can therefore replay the original transaction after the
    server has processed its revocation. Purchase time orders renewals and new
    purchases; for one transaction, revocation is terminal. Expressing those
    rules in the UPDATE predicate makes refund/replay ordering atomic even when
    the client verification and Apple notification endpoints run concurrently.
    """
    predicates = [
        DeviceEntitlement.owner_device_secret == owner_device_secret,
        or_(
            DeviceEntitlement.purchase_date.is_(None),
            DeviceEntitlement.purchase_date <= decoded.purchase_date,
        ),
    ]
    if decoded.revoked_at is None:
        predicates.append(or_(
            DeviceEntitlement.latest_transaction_id != decoded.latest_transaction_id,
            DeviceEntitlement.revoked_at.is_(None),
        ))
    result = db.execute(
        update(DeviceEntitlement)
        .where(and_(*predicates))
        .values(**_entitlement_values(decoded, push_term_limit))
    )
    return result.rowcount == 1


def _upsert_entitlement_atomically(
    db: Session,
    owner_device_secret: str,
    decoded: DecodedEntitlement,
    push_term_limit: int,
    *,
    create_if_missing: bool,
) -> tuple[DeviceEntitlement | None, bool]:
    if _update_entitlement_atomically(db, owner_device_secret, decoded, push_term_limit):
        return _load_entitlement(db, owner_device_secret), True

    entitlement = _load_entitlement(db, owner_device_secret)
    if entitlement is not None or not create_if_missing:
        return entitlement, False

    try:
        # The savepoint lets a simultaneous first verification lose the unique-key
        # race without aborting the outer request transaction.
        with db.begin_nested():
            entitlement = DeviceEntitlement(
                owner_device_secret=owner_device_secret,
                **_entitlement_values(decoded, push_term_limit),
            )
            db.add(entitlement)
            db.flush()
        return entitlement, True
    except IntegrityError:
        if _update_entitlement_atomically(db, owner_device_secret, decoded, push_term_limit):
            return _load_entitlement(db, owner_device_secret), True
        return _load_entitlement(db, owner_device_secret), False


def _update_permanent_entitlement_atomically(
    db: Session,
    owner_device_secret: str,
    decoded: DecodedEntitlement,
    push_term_limit: int,
) -> bool:
    predicates = [
        DeviceEntitlement.owner_device_secret == owner_device_secret,
        or_(
            DeviceEntitlement.permanent_purchase_date.is_(None),
            DeviceEntitlement.permanent_purchase_date <= decoded.purchase_date,
        ),
    ]
    if decoded.revoked_at is None:
        predicates.append(or_(
            DeviceEntitlement.permanent_latest_transaction_id != decoded.latest_transaction_id,
            DeviceEntitlement.permanent_latest_transaction_id.is_(None),
            DeviceEntitlement.permanent_revoked_at.is_(None),
        ))
    result = db.execute(
        update(DeviceEntitlement)
        .where(and_(*predicates))
        .values(**_permanent_entitlement_values(decoded, push_term_limit))
    )
    if result.rowcount != 1:
        return False

    # Keep legacy primary fields synchronized when they still represent this
    # same non-consumable. Effective-state properties otherwise prefer the
    # dedicated permanent slot.
    db.execute(
        update(DeviceEntitlement)
        .where(
            DeviceEntitlement.owner_device_secret == owner_device_secret,
            DeviceEntitlement.original_transaction_id == decoded.original_transaction_id,
        )
        .values(**_entitlement_values(decoded, push_term_limit))
    )
    return True


def _upsert_permanent_entitlement_atomically(
    db: Session,
    owner_device_secret: str,
    decoded: DecodedEntitlement,
    push_term_limit: int,
    *,
    create_if_missing: bool,
) -> tuple[DeviceEntitlement | None, bool]:
    if _update_permanent_entitlement_atomically(
        db, owner_device_secret, decoded, push_term_limit
    ):
        return _load_entitlement(db, owner_device_secret), True

    entitlement = _load_entitlement(db, owner_device_secret)
    if entitlement is not None or not create_if_missing:
        return entitlement, False

    values = _entitlement_values(decoded, push_term_limit)
    values.update(_permanent_entitlement_values(decoded, push_term_limit))
    try:
        with db.begin_nested():
            entitlement = DeviceEntitlement(
                owner_device_secret=owner_device_secret,
                **values,
            )
            db.add(entitlement)
            db.flush()
        return entitlement, True
    except IntegrityError:
        if _update_permanent_entitlement_atomically(
            db, owner_device_secret, decoded, push_term_limit
        ):
            return _load_entitlement(db, owner_device_secret), True
        return _load_entitlement(db, owner_device_secret), False

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
        raise HTTPException(422, "product_id is not a recognized paid product")

    upsert = (
        _upsert_permanent_entitlement_atomically
        if decoded.product_id in settings.plus_non_consumable_product_id_set
        else _upsert_entitlement_atomically
    )
    entitlement, applied = upsert(
        db, auth.device_secret, decoded, push_term_limit, create_if_missing=True
    )
    db.flush()
    if applied:
        clear_paused_pending_notifications(db, auth.device_secret)
    db.commit()
    if entitlement is not None:
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

    owner_device_secrets = [
        owner_device_secret
        for (owner_device_secret,) in (
            db.query(DeviceEntitlement.owner_device_secret)
            .filter(or_(
                DeviceEntitlement.original_transaction_id == decoded.original_transaction_id,
                DeviceEntitlement.permanent_original_transaction_id == decoded.original_transaction_id,
            ))
            .all()
        )
    ]
    upsert = (
        _upsert_permanent_entitlement_atomically
        if decoded.product_id in settings.plus_non_consumable_product_id_set
        else _upsert_entitlement_atomically
    )
    applied = []
    for owner_device_secret in owner_device_secrets:
        _, did_apply = upsert(
            db,
            owner_device_secret,
            decoded,
            tier_limit,
            create_if_missing=False,
        )
        if did_apply:
            applied.append(owner_device_secret)
    db.flush()
    for owner_device_secret in applied:
        clear_paused_pending_notifications(db, owner_device_secret)
    db.commit()
    return {
        "accepted": True,
        "updated": len(applied),
        "notification_type": str(getattr(notification, "rawNotificationType", None) or ""),
    }
