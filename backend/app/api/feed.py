from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_owned_watch_term, require_admin_or_device_auth
from app.database import get_db
from app.entitlements import backend_access_allowed
from app.feed_redirects import match_redirect_signature_is_valid
from app.models import Match, MutedFeedItem, SourceItem, WatchTerm
from app.relevance import watch_term_matches
from app.schemas import FeedItemMuteIn, FeedItemOut, SourceItemOut

router = APIRouter(prefix="/api/feed", tags=["feed"])

_MAX_FEED_SCAN_ROWS = 2000

# These platforms host long-lived community content. Keep old threads out of the
# unfiltered "all" feed, but let them remain reachable when the user explicitly
# opens that source's filter.
_TIMELESS_PLATFORMS = ("5ch", "girlschannel")

# Sort key: every platform by its real published / last-updated date, never by fetch
# (match-discovery) time — discussion-source scrapers heal published_at to the real
# last-reply date, and other connectors carry real publication dates.
_FEED_SORT_KEY = SourceItem.published_at


def _require_paid_backend_access(auth: AuthContext, db: Session) -> None:
    if auth.is_admin:
        return
    if not backend_access_allowed(db, auth.device_secret):
        raise HTTPException(
            402,
            {
                "code": "paid_backend_required",
                "message": "An active purchase is required for backend feed access",
            },
        )


@router.get("/matches/{match_id}/redirect")
def redirect_match(
    match_id: int,
    expires: Optional[int] = Query(None),
    signature: Optional[str] = Query(None, min_length=64, max_length=64),
    db: Session = Depends(get_db),
):
    if expires is None or signature is None or not match_redirect_signature_is_valid(match_id, expires, signature):
        raise HTTPException(403, "feed redirect is invalid or expired")
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


@router.post("/muted-items", status_code=204)
def mute_feed_item(
    body: FeedItemMuteIn,
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
):
    _require_paid_backend_access(auth, db)
    term = get_owned_watch_term(db, body.watch_term_id, auth)

    source_item = db.get(SourceItem, body.source_item_id)
    if source_item is None:
        raise HTTPException(404, "Feed item not found")

    existing_mute = (
        db.query(MutedFeedItem)
        .filter(
            MutedFeedItem.watch_term_id == term.id,
            MutedFeedItem.source_item_id == source_item.id,
        )
        .first()
    )
    if existing_mute is None:
        db.add(MutedFeedItem(watch_term_id=term.id, source_item_id=source_item.id))

    (
        db.query(Match)
        .filter(
            Match.watch_term_id == term.id,
            Match.source_item_id == source_item.id,
        )
        .delete(synchronize_session=False)
    )
    db.commit()


@router.get(
    "/",
    response_model=list[FeedItemOut],
    responses={
        200: {
            "headers": {
                "X-OshiReader-Next-Published-At": {
                    "description": "Published date of the last scanned row; paired with the immutable match cursor.",
                    "schema": {"type": "string", "format": "date-time"},
                },
                "X-OshiReader-Next-Match-ID": {
                    "description": "Immutable continuation cursor for a bounded feed scan.",
                    "schema": {"type": "integer"},
                },
            },
        },
    },
)
def get_feed(
    response: Response,
    term_id: Optional[int] = Query(None),
    term_ids: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    media_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    days: int = Query(30, ge=0, le=365),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    scan: bool = Query(False),
    scan_before_published_at: Optional[datetime] = Query(None),
    scan_before_match_id: Optional[int] = Query(None, ge=1),
    auth: AuthContext = Depends(require_admin_or_device_auth),
    db: Session = Depends(get_db),
):
    _require_paid_backend_access(auth, db)
    q = (
        db.query(Match, SourceItem, WatchTerm)
        .join(SourceItem, Match.source_item_id == SourceItem.id)
        .join(WatchTerm, Match.watch_term_id == WatchTerm.id)
        .outerjoin(
            MutedFeedItem,
            (MutedFeedItem.watch_term_id == Match.watch_term_id)
            & (MutedFeedItem.source_item_id == Match.source_item_id),
        )
        .filter(MutedFeedItem.id.is_(None))
        .filter(or_(
            SourceItem.platform != "youtube",
            SourceItem.raw_payload["source"].as_string().is_(None),
            SourceItem.raw_payload["source"].as_string() != "google_news",
        ))
        .order_by(_FEED_SORT_KEY.desc(), Match.id.desc())
    )
    if not auth.is_admin:
        q = q.filter(WatchTerm.owner_device_secret == auth.device_secret)
    # Timeless forum platforms (5ch/girlschannel) host long-lived threads
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
        # Old forum threads remain eligible only when their last-reply timestamp
        # advanced after the checkpoint. This surfaces new activity without making
        # every incremental refresh rescan the complete unpruned forum history.
        q = q.filter(
            or_(Match.created_at > aware_since,
                and_(
                    SourceItem.platform.in_(_TIMELESS_PLATFORMS),
                    SourceItem.published_at > aware_since,
                ))
        )
    elif days > 0 and not viewing_timeless:
        # Apply the window using each row's effective sort date (created_at for 5ch,
        # published_at otherwise) so forum items are filtered consistently with how
        # they're ordered.
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        q = q.filter(_FEED_SORT_KEY >= cutoff)
    if until is not None:
        aware_until = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until
        q = q.filter(Match.created_at <= aware_until)
    if term_id is not None:
        q = q.filter(Match.watch_term_id == term_id)
    if term_ids is not None:
        try:
            parsed_term_ids = {int(value) for value in term_ids.split(",") if value}
        except ValueError as exc:
            raise HTTPException(422, "term_ids must contain comma-separated integers") from exc
        if not parsed_term_ids or len(parsed_term_ids) > 50:
            raise HTTPException(422, "term_ids must contain between 1 and 50 values")
        q = q.filter(Match.watch_term_id.in_(parsed_term_ids))
    if platform:
        q = q.filter(SourceItem.platform == platform)
    if media_type:
        q = q.filter(SourceItem.media_type == media_type)

    if scan:
        if offset != 0:
            raise HTTPException(422, "offset must be zero for continuation scans")
        if (scan_before_published_at is None) != (scan_before_match_id is None):
            raise HTTPException(422, "both continuation cursor fields are required")
        # Scan in immutable Match.id order. SourceItem.published_at can advance while
        # a discussion thread receives replies; using it as the continuation key can
        # move an unseen row ahead of the previous page and skip it. Keep accepting
        # and emitting the published-at header for deployed-client compatibility, but
        # never use that mutable value to decide which rows remain.
        q = q.order_by(None).order_by(Match.id.desc())
        if scan_before_published_at is not None and scan_before_match_id is not None:
            q = q.filter(Match.id < scan_before_match_id)

        raw_rows = q.limit(_MAX_FEED_SCAN_ROWS).all()
        rows = []
        consumed = 0
        for row in raw_rows:
            consumed += 1
            if watch_term_matches(row[2], row[1]):
                rows.append(row)
                if len(rows) == limit:
                    break
        # A full result page is ambiguous: it may be the exact terminal page.
        # Still emit its cursor so scan clients can make one final empty request
        # and distinguish a valid scan response from a legacy server that ignored
        # the continuation contract.
        if consumed and (
            len(rows) == limit
            or consumed < len(raw_rows)
            or len(raw_rows) == _MAX_FEED_SCAN_ROWS
        ):
            last_match, last_item, _ = raw_rows[consumed - 1]
            response.headers["X-OshiReader-Next-Published-At"] = last_item.published_at.isoformat()
            response.headers["X-OshiReader-Next-Match-ID"] = str(last_match.id)
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

    needed = offset + limit
    relevant_rows = []
    scan_offset = 0
    # Bounded by _MAX_FEED_SCAN_ROWS so a large offset/limit can't force a single
    # unbounded query — the loop guard below only stops *further* iterations,
    # it doesn't limit how many rows the first one asks for.
    batch_size = min(max(200, needed), _MAX_FEED_SCAN_ROWS)
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
