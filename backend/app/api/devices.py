from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_admin_auth
from app.config import settings
from app.database import SessionLocal, get_db
from app.diagnostics import record_backend_event
from app.models import APNSDeviceToken, BackendEvent, WatchTerm
from app.schemas import APNSDeviceTestPush, APNSDeviceTokenOut, APNSDeviceTokenUpsert

router = APIRouter(prefix="/api/devices", tags=["devices"])

_BACKGROUND_REFRESH_MIN_INTERVAL = timedelta(minutes=170)
_BACKGROUND_REFRESH_ATTEMPT_TTL = timedelta(minutes=180)
_BACKGROUND_REFRESH_POLL_TIMEOUT_SECONDS = 6.0
_background_refresh_attempts: dict[str, datetime] = {}
_RECENT_POLL_STATUSES = {
    "started",
    "running_past_request_timeout",
    "completed",
    "completed_with_errors",
    "failed",
    "interrupted",
}


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
        query = db.query(APNSDeviceToken).filter(
            APNSDeviceToken.device_id == body.device_id,
            APNSDeviceToken.environment == body.environment,
        )
        if require_verified:
            query = query.filter(APNSDeviceToken.is_verified == True)  # noqa: E712
        stored = query.order_by(
            APNSDeviceToken.is_verified.desc(),
            APNSDeviceToken.last_seen_at.desc(), APNSDeviceToken.token.desc()
        ).first()
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


def _recent_backend_poll_event(db: Session, now: datetime) -> bool:
    cutoff = now - _BACKGROUND_REFRESH_MIN_INTERVAL
    event = (
        db.query(BackendEvent)
        .filter(
            BackendEvent.kind == "poll",
            BackendEvent.status.in_(_RECENT_POLL_STATUSES),
            BackendEvent.created_at >= cutoff,
        )
        .order_by(BackendEvent.created_at.desc(), BackendEvent.id.desc())
        .first()
    )
    return event is not None


def _mark_verified(device: APNSDeviceToken) -> None:
    device.is_verified = True
    device.verified_at = datetime.now(timezone.utc)


def _registration_failure_revokes_existing_token(reason: str | None) -> bool:
    return reason in {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"}


async def _send_delayed_device_test_push(token: str, delay_seconds: float) -> None:
    if delay_seconds:
        await asyncio.sleep(delay_seconds)

    from app.apns import send_test_push_to_device

    db = SessionLocal()
    try:
        stored = db.get(APNSDeviceToken, token)
        if not stored:
            return
        report = await send_test_push_to_device(db, stored)
        if any(result.get("status") in {200, 201} for result in report.get("results", [])):
            _mark_verified(stored)
            db.commit()
    finally:
        db.close()


def _is_same_device_identity(stored: APNSDeviceToken, body: APNSDeviceTokenUpsert) -> bool:
    return bool(
        stored.device_id
        and body.device_id
        and stored.device_id == body.device_id
        and stored.environment == body.environment
    )


def _retire_superseded_device_tokens(
    db: Session,
    stored: APNSDeviceToken,
) -> int:
    """Remove older APNs tokens that represent the same physical app install."""
    if not stored.device_id:
        return 0
    retired = 0
    candidates = (
        db.query(APNSDeviceToken)
        .filter(
            APNSDeviceToken.environment == stored.environment,
            APNSDeviceToken.token != stored.token,
            APNSDeviceToken.device_id == stored.device_id,
        )
        .all()
    )
    for candidate in candidates:
        db.delete(candidate)
        retired += 1
    return retired


def _device_recency(device: APNSDeviceToken) -> float:
    candidate = device.last_seen_at or device.verified_at
    if candidate is None:
        return 0
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    return candidate.timestamp()


def _redacted_device_row(device: APNSDeviceToken, owner_term_count: int) -> dict:
    return {
        "token": device.token[-8:],
        "environment": device.environment,
        "device_id_present": bool(device.device_id),
        "device_id": device.device_id[-8:] if device.device_id else None,
        "owner_term_count": owner_term_count,
        "verified_at": device.verified_at,
        "last_seen_at": device.last_seen_at,
    }


@router.post("/apns-token", response_model=APNSDeviceTokenOut, status_code=201)
async def upsert_apns_token(body: APNSDeviceTokenUpsert, db: Session = Depends(get_db)):
    token = _normalize_token(body.token)
    if not token or len(token) != 64 or any(ch not in "0123456789abcdef" for ch in token):
        raise HTTPException(400, "Invalid APNs device token")

    stored = db.get(APNSDeviceToken, token)
    previously_verified_for_environment = bool(
        stored
        and stored.is_verified
        and stored.environment == body.environment
        and stored.device_secret
        and _secret_matches(stored.device_secret, body.device_secret)
    )
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
    if not previously_verified_for_environment:
        stored.is_verified = False
        stored.verified_at = None
    stored.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(stored)

    from app.apns import validate_device_registration_result

    stored.verification_attempted_at = datetime.now(timezone.utc)
    verification_error = None
    if (
        settings.allow_unauthenticated_admin
        and not settings.admin_api_token
    ) or settings.apns_trust_registered_tokens:
        verified = True
    else:
        verified, verification_error = await validate_device_registration_result(stored)
    if verified:
        _mark_verified(stored)
        # Retire the previous token only after the replacement has passed APNs
        # validation. A transient rotation failure must not remove the last
        # verified token for this device.
        _retire_superseded_device_tokens(db, stored)
        db.commit()
        db.refresh(stored)
    else:
        if _registration_failure_revokes_existing_token(verification_error):
            stored.is_verified = False
            stored.verified_at = None
        db.commit()
        db.refresh(stored)
        event_status = "unverified"
        if stored.is_verified:
            event_status = "verification_failed_preserved"
        record_backend_event(
            db,
            "apns_registration",
            event_status,
            "APNs device registration was not verified",
            {
                "environment": stored.environment,
                "device_id_present": bool(stored.device_id),
                "verification_error": verification_error,
                "preserved_existing_verification": stored.is_verified,
            },
        )
        db.commit()
    return APNSDeviceTokenOut(
        token=stored.token,
        environment=stored.environment,
        device_id=stored.device_id,
        last_seen_at=stored.last_seen_at,
        is_verified=stored.is_verified,
        verification_error=verification_error,
    )


@router.post("/apns-test-push")
async def send_device_test_push(body: APNSDeviceTestPush, db: Session = Depends(get_db)) -> dict:
    stored = _find_authenticated_device(body, db, require_verified=False)

    from app.apns import send_test_push_to_device
    if body.return_before_delivery:
        asyncio.create_task(_send_delayed_device_test_push(stored.token, body.delivery_delay_seconds))
        return {
            "configured": True,
            "results": [
                {
                    "token": stored.token[-8:],
                    "environment": stored.environment,
                    "status": 202,
                    "reason": "queued",
                }
            ],
            "pruned_tokens": 0,
            "note": "queued",
        }
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
    if _recent_backend_poll_event(db, now):
        return {"status": "poll throttled"}
    if _recent_background_refresh_attempt(stored.token, now):
        return {"status": "poll throttled"}
    poll_task = ingestion_scheduler.create_poll_task()
    try:
        await asyncio.wait_for(
            asyncio.shield(poll_task),
            timeout=_BACKGROUND_REFRESH_POLL_TIMEOUT_SECONDS,
        )
        return {"status": "poll completed"}
    except asyncio.TimeoutError:
        return {"status": "poll still running (request timed out)"}


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


@router.delete("/apns-tokens/unverified", status_code=200)
def prune_unverified_apns_tokens(
    dry_run: bool = Query(True),
    _: None = Depends(require_admin_auth),
    db: Session = Depends(get_db),
) -> dict:
    """Delete all unverified APNs device tokens. Safe to run anytime — they will
    re-register and be immediately verified when APNS_TRUST_REGISTERED_TOKENS is set."""
    count = db.query(APNSDeviceToken).filter(APNSDeviceToken.is_verified == False).count()  # noqa: E712
    if not dry_run:
        db.query(APNSDeviceToken).filter(APNSDeviceToken.is_verified == False).delete(synchronize_session=False)  # noqa: E712
        db.commit()
    return {"deleted": count, "dry_run": dry_run}


@router.post("/apns-tokens/prune-superseded")
def prune_superseded_apns_tokens(
    dry_run: bool = Query(True),
    keep_per_environment: int = Query(1, ge=1, le=20),
    preserve_owner_scoped: bool = Query(True),
    _: None = Depends(require_admin_auth),
    db: Session = Depends(get_db),
) -> dict:
    owner_term_rows = (
        db.query(WatchTerm.owner_device_secret, WatchTerm.id)
        .filter(WatchTerm.owner_device_secret.isnot(None))
        .all()
    )
    owner_term_counts_by_secret: dict[str, int] = defaultdict(int)
    for device_secret, _term_id in owner_term_rows:
        owner_term_counts_by_secret[device_secret] += 1

    devices = (
        db.query(APNSDeviceToken)
        .filter(APNSDeviceToken.is_verified == True)  # noqa: E712
        .all()
    )
    devices_by_environment: dict[str, list[APNSDeviceToken]] = defaultdict(list)
    for device in devices:
        devices_by_environment[device.environment or "unknown"].append(device)

    kept: list[APNSDeviceToken] = []
    removed: list[APNSDeviceToken] = []

    def owner_count(device: APNSDeviceToken) -> int:
        if not device.device_secret:
            return 0
        return owner_term_counts_by_secret.get(device.device_secret, 0)

    for environment_devices in devices_by_environment.values():
        ordered = sorted(
            environment_devices,
            key=lambda device: (_device_recency(device), device.token),
            reverse=True,
        )
        keep_tokens = {device.token for device in ordered[:keep_per_environment]}
        if preserve_owner_scoped:
            keep_tokens.update(device.token for device in ordered if owner_count(device) > 0)
        for device in ordered:
            if device.token in keep_tokens:
                kept.append(device)
            else:
                removed.append(device)

    if not dry_run:
        for device in removed:
            db.delete(device)
        db.commit()

    return {
        "dry_run": dry_run,
        "keep_per_environment": keep_per_environment,
        "preserve_owner_scoped": preserve_owner_scoped,
        "candidate_count": len(devices),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "kept": [_redacted_device_row(device, owner_count(device)) for device in kept],
        "removed": [_redacted_device_row(device, owner_count(device)) for device in removed],
    }
