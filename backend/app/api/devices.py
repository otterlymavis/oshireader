from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin_auth
from app.config import settings
from app.database import get_db
from app.models import APNSDeviceToken
from app.schemas import APNSDeviceTestPush, APNSDeviceTokenOut, APNSDeviceTokenUpsert

router = APIRouter(prefix="/api/devices", tags=["devices"])

_BACKGROUND_REFRESH_MIN_INTERVAL = timedelta(seconds=60)
_BACKGROUND_REFRESH_ATTEMPT_TTL = timedelta(minutes=10)
_background_refresh_attempts: dict[str, datetime] = {}


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


def _find_authenticated_device(
    body: APNSDeviceTestPush,
    db: Session,
    *,
    require_verified: bool = True,
) -> APNSDeviceToken:
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
    if require_verified and not stored.is_verified:
        raise HTTPException(404, "APNs device token not verified")
    return stored


def _recent_background_refresh_attempt(token: str, now: datetime) -> bool:
    stale_before = now - _BACKGROUND_REFRESH_ATTEMPT_TTL
    for stored_token, attempted_at in list(_background_refresh_attempts.items()):
        if attempted_at < stale_before:
            _background_refresh_attempts.pop(stored_token, None)

    last_attempt = _background_refresh_attempts.get(token)
    if last_attempt and now - last_attempt < _BACKGROUND_REFRESH_MIN_INTERVAL:
        return True
    _background_refresh_attempts[token] = now
    return False


def _mark_verified(device: APNSDeviceToken) -> None:
    device.is_verified = True
    device.verified_at = datetime.now(timezone.utc)


def _is_same_device_identity(stored: APNSDeviceToken, body: APNSDeviceTokenUpsert) -> bool:
    return bool(
        stored.device_id
        and body.device_id
        and stored.device_id == body.device_id
        and stored.environment == body.environment
    )


@router.post("/apns-token", response_model=APNSDeviceTokenOut, status_code=201)
async def upsert_apns_token(body: APNSDeviceTokenUpsert, db: Session = Depends(get_db)):
    token = _normalize_token(body.token)
    if not token or len(token) != 64 or any(ch not in "0123456789abcdef" for ch in token):
        raise HTTPException(400, "Invalid APNs device token")

    stored = db.get(APNSDeviceToken, token)
    if not stored:
        stored = APNSDeviceToken(token=token, is_verified=False)
        db.add(stored)
    elif (
        stored.device_secret
        and not _secret_matches(stored.device_secret, body.device_secret)
        and not _is_same_device_identity(stored, body)
    ):
        raise HTTPException(409, "APNs device token is registered to another device secret")

    stored.environment = body.environment
    stored.device_id = body.device_id
    stored.device_secret = _secret_digest(body.device_secret)
    stored.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(stored)

    from app.apns import validate_device_registration

    if (
        settings.allow_unauthenticated_admin
        and not settings.admin_api_token
    ) or await validate_device_registration(stored):
        _mark_verified(stored)
        db.commit()
        db.refresh(stored)
    return stored


@router.post("/apns-test-push")
async def send_device_test_push(body: APNSDeviceTestPush, db: Session = Depends(get_db)) -> dict:
    stored = _find_authenticated_device(body, db, require_verified=False)

    from app.apns import send_test_push_to_device
    if body.delivery_delay_seconds:
        await asyncio.sleep(body.delivery_delay_seconds)
    report = await send_test_push_to_device(db, stored)
    if any(result.get("status") in {200, 201} for result in report.get("results", [])):
        _mark_verified(stored)
        db.commit()
    return report


@router.post("/background-refresh")
async def request_device_background_refresh(body: APNSDeviceTestPush, db: Session = Depends(get_db)) -> dict:
    stored = _find_authenticated_device(body, db)

    from app.ingestion import scheduler as ingestion_scheduler

    if ingestion_scheduler._poll_lock.locked():
        return {"status": "poll already running"}
    now = datetime.now(timezone.utc)
    if _recent_background_refresh_attempt(stored.token, now):
        return {"status": "poll throttled"}
    try:
        await asyncio.wait_for(ingestion_scheduler.poll_once(), timeout=20.0)
        return {"status": "poll completed"}
    except asyncio.TimeoutError:
        return {"status": "poll timed out (partial progress saved)"}


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
