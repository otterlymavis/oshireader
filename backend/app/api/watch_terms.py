from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_admin_or_device_auth
from app.database import get_db
from app.ingestion.scheduler import queue_poll
from app.models import APNSDeviceToken, Match, PendingNotification, WatchTerm
from app.schemas import WatchTermCreate, WatchTermOut, WatchTermUpdate

router = APIRouter(prefix="/api/watch-terms", tags=["watch-terms"])


def _require_nonempty_source_selection(source_mode: str, selected_platforms: list[str]) -> None:
    if source_mode == "selected" and not selected_platforms:
        raise HTTPException(
            422,
            "selected source mode requires at least one selected platform",
        )


def _term_with_keyword_exists(
    db: Session,
    *,
    keyword: str,
    owner_device_secret: str | None,
    exclude_id: int | None = None,
) -> bool:
    query = db.query(WatchTerm).filter(WatchTerm.keyword == keyword)
    if owner_device_secret is None:
        query = query.filter(WatchTerm.owner_device_secret.is_(None))
    else:
        query = query.filter(WatchTerm.owner_device_secret == owner_device_secret)
    if exclude_id is not None:
        query = query.filter(WatchTerm.id != exclude_id)
    return db.query(query.exists()).scalar()


_INLINE_REVERIFY_WINDOW = timedelta(minutes=5)


def _owner_has_verified_device(db: Session, owner_device_secret: str | None) -> bool:
    if owner_device_secret is None:
        return False
    return (
        db.query(APNSDeviceToken.token)
        .filter(
            APNSDeviceToken.device_secret == owner_device_secret,
            APNSDeviceToken.is_verified == True,  # noqa: E712
        )
        .first()
        is not None
    )


def _recently_registered_unverified_devices(
    db: Session, owner_device_secret: str
) -> list[APNSDeviceToken]:
    cutoff = datetime.now(timezone.utc) - _INLINE_REVERIFY_WINDOW
    return (
        db.query(APNSDeviceToken)
        .filter(
            APNSDeviceToken.device_secret == owner_device_secret,
            APNSDeviceToken.is_verified == False,  # noqa: E712
            APNSDeviceToken.last_seen_at >= cutoff,
        )
        .all()
    )


async def _require_verified_notification_device(db: Session, term: WatchTerm) -> None:
    if not (term.notify_on_new and term.owner_device_secret):
        return
    if _owner_has_verified_device(db, term.owner_device_secret):
        return

    # Attempt inline re-verification of any recently-registered unverified tokens.
    # This recovers from transient APNs failures during device registration that
    # leave a valid token stuck in the unverified state.
    candidates = _recently_registered_unverified_devices(db, term.owner_device_secret)
    if candidates:
        from app.apns import validate_device_registration_result
        import asyncio
        results = await asyncio.gather(
            *[validate_device_registration_result(d) for d in candidates],
            return_exceptions=True,
        )
        now = datetime.now(timezone.utc)
        for device, result in zip(candidates, results):
            if isinstance(result, Exception):
                continue
            verified, _ = result
            device.verification_attempted_at = now
            if verified:
                device.is_verified = True
                device.verified_at = now
                db.commit()
                return

    raise HTTPException(
        409,
        {
            "code": "notification_device_required",
            "message": "Notification-enabled watch terms require a verified APNs device",
        },
    )


def _seed_matches_from_global_term(db: Session, term: WatchTerm) -> None:
    if term.owner_device_secret is None:
        return
    global_term = (
        db.query(WatchTerm)
        .filter(
            WatchTerm.keyword == term.keyword,
            WatchTerm.owner_device_secret.is_(None),
        )
        .order_by(WatchTerm.created_at.desc(), WatchTerm.id.desc())
        .first()
    )
    if global_term is None:
        return

    rows = (
        db.query(Match)
        .filter(Match.watch_term_id == global_term.id)
        .all()
    )
    for row in rows:
        db.add(
            Match(
                watch_term_id=term.id,
                source_item_id=row.source_item_id,
                confidence=row.confidence,
                created_at=row.created_at,
            )
        )


@router.get("/", response_model=list[WatchTermOut])
def list_terms(
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
):
    query = db.query(WatchTerm)
    if not auth.is_admin:
        query = query.filter(WatchTerm.owner_device_secret == auth.device_secret)
    return query.order_by(WatchTerm.created_at.desc()).all()


@router.post("/", response_model=WatchTermOut, status_code=201)
async def create_term(
    body: WatchTermCreate,
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
):
    _require_nonempty_source_selection(body.source_mode, body.selected_platforms)
    term = WatchTerm(**body.model_dump())
    if not auth.is_admin:
        term.owner_device_secret = auth.device_secret
        await _require_verified_notification_device(db, term)
    if _term_with_keyword_exists(
        db,
        keyword=term.keyword,
        owner_device_secret=term.owner_device_secret,
    ):
        raise HTTPException(409, "A watch term with this keyword already exists")
    db.add(term)
    try:
        db.flush()
        _seed_matches_from_global_term(db, term)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A watch term with this keyword already exists")
    db.refresh(term)
    queue_poll()
    return term


@router.patch("/{term_id}", response_model=WatchTermOut)
async def update_term(
    term_id: int,
    body: WatchTermUpdate,
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
):
    term = db.get(WatchTerm, term_id)
    if not term or (not auth.is_admin and term.owner_device_secret != auth.device_secret):
        raise HTTPException(404, "Watch term not found")
    updates = body.model_dump(exclude_none=True)
    _require_nonempty_source_selection(
        updates.get("source_mode", term.source_mode),
        updates.get("selected_platforms", term.selected_platforms or []),
    )
    if "keyword" in updates and _term_with_keyword_exists(
        db,
        keyword=updates["keyword"],
        owner_device_secret=term.owner_device_secret,
        exclude_id=term.id,
    ):
        raise HTTPException(409, "A watch term with this keyword already exists")
    for k, v in updates.items():
        setattr(term, k, v)
    # A queued poll still passes through due-term selection. Make every fetch-
    # scope expansion immediately eligible instead of allowing a recent poll
    # timestamp from the old scope to discard the queued work.
    should_poll = (
        "keyword" in updates
        or "aliases" in updates
        or "collection_mode" in updates
        or "source_mode" in updates
        or "selected_platforms" in updates
        or (updates.get("is_active") is True)
    )
    if should_poll:
        term.last_polled_at = None
    if not auth.is_admin:
        await _require_verified_notification_device(db, term)
    if updates.get("notify_on_new") is False or updates.get("is_active") is False:
        pending = db.get(PendingNotification, term.id)
        if pending is not None:
            db.delete(pending)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A watch term with this keyword already exists")
    db.refresh(term)
    if should_poll:
        queue_poll()
    return term


@router.post("/{term_id}/notify")
async def trigger_notification(
    term_id: int,
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
) -> dict:
    """Immediately push a notification for this term to the owner's devices."""
    from app.apns import apns_configured, send_new_match_notifications

    term = db.get(WatchTerm, term_id)
    if not term or (not auth.is_admin and term.owner_device_secret != auth.device_secret):
        raise HTTPException(404, "Watch term not found")
    if not term.notify_on_new:
        raise HTTPException(409, {"code": "notifications_disabled", "message": "Enable notifications for this term first"})
    if not apns_configured():
        raise HTTPException(503, "APNs is not configured")

    pending = db.get(PendingNotification, term_id)
    if pending is None:
        raise HTTPException(409, {"code": "no_pending_content", "message": "No pending notification content to deliver"})
    count = pending.new_count
    preview = pending.preview_item
    if count <= 0:
        db.delete(pending)
        db.commit()
        raise HTTPException(409, {"code": "no_pending_content", "message": "No pending notification content to deliver"})

    cleared = await send_new_match_notifications(db, term, count, preview)
    if cleared is not False:
        db.delete(pending)
    db.commit()
    return {"term_id": term_id, "keyword": term.keyword, "count": count, "cleared": cleared}


@router.delete("/{term_id}/notify", status_code=204)
def clear_notification(
    term_id: int,
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
) -> None:
    """Clear a pending notification for this term without delivering it."""
    term = db.get(WatchTerm, term_id)
    if not term or (not auth.is_admin and term.owner_device_secret != auth.device_secret):
        raise HTTPException(404, "Watch term not found")
    pending = db.get(PendingNotification, term_id)
    if pending is not None:
        db.delete(pending)
        db.commit()


@router.delete("/{term_id}", status_code=204)
def delete_term(
    term_id: int,
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
):
    term = db.get(WatchTerm, term_id)
    if not term or (not auth.is_admin and term.owner_device_secret != auth.device_secret):
        raise HTTPException(404, "Watch term not found")
    db.delete(term)
    db.flush()
    db.execute(text(
        "DELETE FROM source_items "
        "WHERE id NOT IN (SELECT source_item_id FROM matches) "
        "AND id NOT IN (SELECT source_item_id FROM muted_feed_items)"
    ))
    db.commit()
