from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin_auth
from app.database import get_db
from app.models import APNSDeviceToken
from app.schemas import APNSDeviceTestPush, APNSDeviceTokenOut, APNSDeviceTokenUpsert

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _normalize_token(token: str) -> str:
    return "".join(token.strip().lower().split())


def _secret_digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _secret_matches(stored_secret: str | None, provided_secret: str) -> bool:
    if not stored_secret:
        return False
    digest = _secret_digest(provided_secret)
    if secrets.compare_digest(stored_secret, digest):
        return True
    # Upgrade tokens registered before secrets were stored as hashes.
    return secrets.compare_digest(stored_secret, provided_secret)


@router.post("/apns-token", response_model=APNSDeviceTokenOut, status_code=201)
def upsert_apns_token(body: APNSDeviceTokenUpsert, db: Session = Depends(get_db)):
    token = _normalize_token(body.token)
    if not token or len(token) != 64 or any(ch not in "0123456789abcdef" for ch in token):
        raise HTTPException(400, "Invalid APNs device token")

    stored = db.get(APNSDeviceToken, token)
    if not stored:
        stored = APNSDeviceToken(token=token)
        db.add(stored)
    elif stored.device_secret and not _secret_matches(stored.device_secret, body.device_secret):
        raise HTTPException(409, "APNs device token is registered to another device secret")

    stored.environment = body.environment
    stored.device_id = body.device_id
    stored.device_secret = _secret_digest(body.device_secret)
    stored.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(stored)
    return stored


@router.post("/apns-test-push")
async def send_device_test_push(body: APNSDeviceTestPush, db: Session = Depends(get_db)) -> dict:
    stored: APNSDeviceToken | None = None
    if body.token:
        token = _normalize_token(body.token)
        if not token or len(token) != 64 or any(ch not in "0123456789abcdef" for ch in token):
            raise HTTPException(400, "Invalid APNs device token")
        stored = db.get(APNSDeviceToken, token)
    elif body.device_id:
        stored = (
            db.query(APNSDeviceToken)
            .filter(
                APNSDeviceToken.device_id == body.device_id,
                APNSDeviceToken.environment == body.environment,
            )
            .order_by(APNSDeviceToken.last_seen_at.desc(), APNSDeviceToken.token.desc())
            .first()
        )
    else:
        raise HTTPException(400, "APNs device token or device_id is required")

    if not stored or not _secret_matches(stored.device_secret, body.device_secret):
        raise HTTPException(404, "APNs device token not registered")

    from app.apns import send_test_push_to_device
    return await send_test_push_to_device(db, stored)


@router.delete("/apns-token/{token}", status_code=204)
def delete_apns_token(
    token: str,
    device_secret: str = Header(alias="X-Device-Secret"),
    db: Session = Depends(get_db),
):
    stored = db.get(APNSDeviceToken, _normalize_token(token))
    if not stored or not _secret_matches(stored.device_secret, device_secret):
        raise HTTPException(404, "APNs device token not registered")
    db.delete(stored)
    db.commit()


@router.get("/apns-tokens", response_model=list[APNSDeviceTokenOut])
def list_apns_tokens(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)):
    return db.query(APNSDeviceToken).order_by(APNSDeviceToken.last_seen_at.desc()).all()
