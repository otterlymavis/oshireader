from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Match, SourceItem, WatchTerm
from app.schemas import FeedItemOut, SourceItemOut

router = APIRouter(prefix="/api/feed", tags=["feed"])


@router.get("/", response_model=list[FeedItemOut])
def get_feed(
    term_id: Optional[int] = Query(None),
    platform: Optional[str] = Query(None),
    media_type: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    days: int = Query(30, ge=0, le=365),
    since: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    q = (
        db.query(Match, SourceItem, WatchTerm)
        .join(SourceItem, Match.source_item_id == SourceItem.id)
        .join(WatchTerm, Match.watch_term_id == WatchTerm.id)
        .order_by(SourceItem.published_at.desc())
    )
    if since is not None:
        # Caller knows exactly what it has — only return genuinely newer items
        aware_since = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
        q = q.filter(SourceItem.published_at > aware_since)
    elif days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        q = q.filter(SourceItem.published_at >= cutoff)
    if term_id is not None:
        q = q.filter(Match.watch_term_id == term_id)
    if platform:
        q = q.filter(SourceItem.platform == platform)
    if media_type:
        q = q.filter(SourceItem.media_type == media_type)

    rows = q.offset(offset).limit(limit).all()

    return [
        FeedItemOut(
            match_id=match.id,
            watch_term_id=term.id,
            watch_term_keyword=term.keyword,
            item=SourceItemOut.model_validate(item),
            matched_at=match.created_at,
        )
        for match, item, term in rows
    ]
