from __future__ import annotations

from sqlalchemy import text

from app.connectors.base import contains_keyword
from app.models import Match, SourceItem, WatchTerm


def _matches_note_hashtag(search_term: str, item) -> bool:
    if getattr(item, "platform", None) != "note":
        return False
    raw_payload = getattr(item, "raw_payload", None) or {}
    if not isinstance(raw_payload, dict):
        return False
    return contains_keyword(search_term, raw_payload.get("matched_hashtag"))


def primary_text_matches(search_term: str, item) -> bool:
    if _matches_note_hashtag(search_term, item):
        return True
    title = (item.title or "").strip()
    if item.media_type in {"video", "image"}:
        return contains_keyword(search_term, title, item.content_text, item.author)
    return contains_keyword(search_term, title or item.content_text)


def watch_term_matches(term, item) -> bool:
    return any(
        primary_text_matches(search_term, item)
        for search_term in [term.keyword, *(term.aliases or [])]
        if search_term and search_term.strip()
    )


def prune_irrelevant_matches(db, terms: list[WatchTerm] | None = None) -> int:
    terms = terms if terms is not None else db.query(WatchTerm).all()
    if not terms:
        return 0

    terms_by_id = {term.id: term for term in terms}
    rows = (
        db.query(Match, SourceItem)
        .join(SourceItem, Match.source_item_id == SourceItem.id)
        .filter(Match.watch_term_id.in_(terms_by_id))
        .all()
    )
    removed = 0
    for match, item in rows:
        term = terms_by_id.get(match.watch_term_id)
        if term and watch_term_matches(term, item):
            continue
        db.delete(match)
        removed += 1

    if removed:
        db.flush()
        db.execute(text(
            "DELETE FROM source_items "
            "WHERE id NOT IN (SELECT source_item_id FROM matches) "
            "AND id NOT IN (SELECT source_item_id FROM muted_feed_items)"
        ))
    return removed
