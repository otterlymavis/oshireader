from __future__ import annotations

import unicodedata

from sqlalchemy import text

from app.models import Match, SourceItem, WatchTerm


def contains_keyword(keyword: str, *values: object) -> bool:
    needle = unicodedata.normalize("NFKC", keyword.strip()).casefold()
    if not needle:
        return False
    return any(
        needle in unicodedata.normalize("NFKC", str(value)).casefold()
        for value in values
        if value
    )


def primary_text_matches(search_term: str, item) -> bool:
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
            "DELETE FROM source_items WHERE id NOT IN (SELECT source_item_id FROM matches)"
        ))
    return removed
