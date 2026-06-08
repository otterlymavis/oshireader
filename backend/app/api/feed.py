from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Match, SourceItem, WatchTerm
from app.schemas import FeedItemOut, SourceItemOut

router = APIRouter(prefix="/api/feed", tags=["feed"])

# These platforms host long-lived threads / community content — skip date filters
# so they always reach the client (mirrors the iOS app's skipCutoff logic).
_TIMELESS_PLATFORMS = ("5ch", "girlschannel", "togetter")


@router.get("/", response_model=list[FeedItemOut])
def get_feed(
    term_id: Optional[int] = Query(None),
    platform: Optional[str] = Query(None),
    media_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
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
        aware_since = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
        # Filter by when the item was *stored* (matched_at), not published_at.
        # Items with broadcast dates earlier in the day but fetched after last sync
        # must still appear (e.g. TVer episodes with midnight broadcast dates).
        q = q.filter(
            or_(Match.created_at > aware_since,
                SourceItem.platform.in_(_TIMELESS_PLATFORMS))
        )
    elif days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        q = q.filter(
            or_(SourceItem.published_at >= cutoff,
                SourceItem.platform.in_(_TIMELESS_PLATFORMS))
        )
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
