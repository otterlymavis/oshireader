from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_admin_or_device_auth
from app.database import get_db
from app.ingestion.scheduler import queue_poll
from app.models import APNSDeviceToken, WatchTerm
from app.schemas import WatchTermCreate, WatchTermOut, WatchTermUpdate

router = APIRouter(prefix="/api/watch-terms", tags=["watch-terms"])


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


def _owner_has_any_device(db: Session, owner_device_secret: str | None) -> bool:
    if owner_device_secret is None:
        return False
    return (
        db.query(APNSDeviceToken.token)
        .filter(APNSDeviceToken.device_secret == owner_device_secret)
        .first()
        is not None
    )


def _adopt_orphaned_same_keyword_term(db: Session, incoming: WatchTerm) -> WatchTerm | None:
    if incoming.owner_device_secret is None:
        return None
    candidates = (
        db.query(WatchTerm)
        .filter(
            WatchTerm.keyword == incoming.keyword,
            WatchTerm.owner_device_secret.isnot(None),
            WatchTerm.owner_device_secret != incoming.owner_device_secret,
            WatchTerm.is_active == True,  # noqa: E712
        )
        .order_by(WatchTerm.created_at.desc(), WatchTerm.id.desc())
        .all()
    )
    for candidate in candidates:
        if _owner_has_any_device(db, candidate.owner_device_secret):
            continue
        candidate.owner_device_secret = incoming.owner_device_secret
        candidate.collection_mode = incoming.collection_mode
        candidate.is_active = incoming.is_active
        candidate.notify_on_new = incoming.notify_on_new
        candidate.aliases = incoming.aliases
        return candidate
    return None


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
    term = WatchTerm(**body.model_dump())
    if not auth.is_admin:
        term.owner_device_secret = auth.device_secret
    if _term_with_keyword_exists(
        db,
        keyword=term.keyword,
        owner_device_secret=term.owner_device_secret,
    ):
        raise HTTPException(409, "A watch term with this keyword already exists")
    adopted = None if auth.is_admin else _adopt_orphaned_same_keyword_term(db, term)
    if adopted is not None:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "A watch term with this keyword already exists")
        db.refresh(adopted)
        queue_poll()
        return adopted
    db.add(term)
    try:
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
    if "keyword" in updates and _term_with_keyword_exists(
        db,
        keyword=updates["keyword"],
        owner_device_secret=term.owner_device_secret,
        exclude_id=term.id,
    ):
        raise HTTPException(409, "A watch term with this keyword already exists")
    for k, v in updates.items():
        setattr(term, k, v)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A watch term with this keyword already exists")
    db.refresh(term)
    # Re-poll when changes affect what gets fetched on the next cycle.
    should_poll = (
        "keyword" in updates
        or "aliases" in updates
        or "collection_mode" in updates
        or (updates.get("is_active") is True)
    )
    if should_poll:
        queue_poll()
    return term


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
    db.execute(text("DELETE FROM source_items WHERE id NOT IN (SELECT source_item_id FROM matches)"))
    db.commit()
