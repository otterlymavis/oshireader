from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_admin_or_device_auth
from app.database import get_db
from app.models import Match, SourceItem, WatchTerm
from app.relevance import watch_term_matches
from app.schemas import FeedItemOut, SourceItemOut

router = APIRouter(prefix="/api/feed", tags=["feed"])

_MAX_FEED_SCAN_ROWS = 2000

# These platforms host long-lived community content. Keep old threads out of the
# unfiltered "all" feed, but let them remain reachable when the user explicitly
# opens that source's filter.
_TIMELESS_PLATFORMS = ("5ch", "girlschannel", "togetter")

# Sort key: every platform by its real published / last-updated date, never by fetch
# (match-discovery) time — girlschannel/togetter scrapers heal published_at to the real
# last-reply date, and other connectors carry real publication dates.
_FEED_SORT_KEY = SourceItem.published_at


@router.get("/matches/{match_id}/redirect")
def redirect_match(match_id: int, db: Session = Depends(get_db)):
    row = (
        db.query(SourceItem.url)
        .join(Match, Match.source_item_id == SourceItem.id)
        .filter(Match.id == match_id)
        .first()
    )
    if not row:
        raise HTTPException(404, "Feed match not found")
    source_url = row[0]
    parsed = urlparse(source_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(404, "Feed match URL not available")
    return RedirectResponse(url=source_url, status_code=307)


@router.get("/", response_model=list[FeedItemOut])
def get_feed(
    term_id: Optional[int] = Query(None),
    platform: Optional[str] = Query(None),
    media_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    days: int = Query(30, ge=0, le=365),
    since: Optional[datetime] = Query(None),
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
):
    q = (
        db.query(Match, SourceItem, WatchTerm)
        .join(SourceItem, Match.source_item_id == SourceItem.id)
        .join(WatchTerm, Match.watch_term_id == WatchTerm.id)
        .order_by(_FEED_SORT_KEY.desc())
    )
    if not auth.is_admin:
        q = q.filter(WatchTerm.owner_device_secret == auth.device_secret)
    # Timeless forum platforms (5ch/girlschannel/togetter) host long-lived threads
    # and are exempt from pruning, so they accumulate for months. Only let them
    # bypass the date window when the user explicitly opens that source's filter —
    # in the unfiltered "all" feed apply the normal window to them too, otherwise
    # months of accumulated forum threads bury every other source.
    viewing_timeless = platform in _TIMELESS_PLATFORMS

    if since is not None:
        aware_since = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
        # Filter by when the item was *stored* (matched_at), not published_at.
        # Items with broadcast dates earlier in the day but fetched after last sync
        # must still appear (e.g. TVer episodes with midnight broadcast dates).
        q = q.filter(
            or_(Match.created_at > aware_since,
                SourceItem.platform.in_(_TIMELESS_PLATFORMS))
        )
    elif days > 0 and not viewing_timeless:
        # Apply the window using each row's effective sort date (created_at for 5ch,
        # published_at otherwise) so forum items are filtered consistently with how
        # they're ordered.
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        q = q.filter(_FEED_SORT_KEY >= cutoff)
    if term_id is not None:
        q = q.filter(Match.watch_term_id == term_id)
    if platform:
        q = q.filter(SourceItem.platform == platform)
    if media_type:
        q = q.filter(SourceItem.media_type == media_type)

    needed = offset + limit
    relevant_rows = []
    scan_offset = 0
    batch_size = max(200, needed)
    while len(relevant_rows) < needed and scan_offset < _MAX_FEED_SCAN_ROWS:
        batch = q.offset(scan_offset).limit(batch_size).all()
        if not batch:
            break
        relevant_rows.extend(
            row for row in batch if watch_term_matches(row[2], row[1])
        )
        scan_offset += len(batch)
        if len(batch) < batch_size:
            break
    rows = relevant_rows[offset:needed]

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
