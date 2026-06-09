"""Tests for scheduler pruning logic and search-term building."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.ingestion.scheduler import _prune_old_items, _search_terms_for
from app.models import Match, SourceItem, WatchTerm


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _add_items(db, term, platform, count, start_days_ago=200):
    """Insert `count` items and matches for a given platform."""
    for i in range(count):
        pub = datetime.now(timezone.utc) - timedelta(days=start_days_ago - i)
        item = SourceItem(
            id=f"{platform}:item{i}",
            platform=platform,
            item_id=f"item{i}",
            url=f"https://example.com/{platform}/{i}",
            published_at=pub,
            media_type="article",
        )
        db.add(item)
        db.flush()
        db.add(Match(watch_term_id=term.id, source_item_id=item.id))
    db.commit()


class TestPruneOldItems:
    def test_prune_removes_excess_matches(self, db):
        term = WatchTerm(keyword="k1", aliases=[])
        db.add(term)
        db.commit()

        _add_items(db, term, "youtube", 250)

        _prune_old_items(db)

        remaining = db.query(Match).filter(Match.watch_term_id == term.id).count()
        assert remaining == 200

    def test_prune_deletes_orphan_source_items(self, db):
        term = WatchTerm(keyword="k2", aliases=[])
        db.add(term)
        db.commit()

        _add_items(db, term, "news", 250)

        _prune_old_items(db)

        orphans = (
            db.query(SourceItem)
            .filter(
                ~SourceItem.id.in_(
                    db.query(Match.source_item_id)
                )
            )
            .count()
        )
        assert orphans == 0

    def test_prune_skips_community_platforms(self, db):
        term = WatchTerm(keyword="k3", aliases=[])
        db.add(term)
        db.commit()

        for platform in ("5ch", "girlschannel", "togetter"):
            _add_items(db, term, platform, 250)

        _prune_old_items(db)

        for platform in ("5ch", "girlschannel", "togetter"):
            count = (
                db.query(Match)
                .join(SourceItem, SourceItem.id == Match.source_item_id)
                .filter(SourceItem.platform == platform)
                .count()
            )
            assert count == 250, f"{platform} should not be pruned"

    def test_prune_is_idempotent(self, db):
        term = WatchTerm(keyword="k4", aliases=[])
        db.add(term)
        db.commit()

        _add_items(db, term, "niconico", 210)

        _prune_old_items(db)
        count_after_first = db.query(Match).filter(Match.watch_term_id == term.id).count()

        _prune_old_items(db)
        count_after_second = db.query(Match).filter(Match.watch_term_id == term.id).count()

        assert count_after_first == count_after_second == 200

    def test_prune_keeps_newest_items(self, db):
        term = WatchTerm(keyword="k5", aliases=[])
        db.add(term)
        db.commit()

        _add_items(db, term, "news", 210, start_days_ago=210)

        _prune_old_items(db)

        kept_items = (
            db.query(SourceItem)
            .join(Match, Match.source_item_id == SourceItem.id)
            .filter(SourceItem.platform == "news")
            .order_by(SourceItem.published_at.desc())
            .all()
        )
        assert len(kept_items) == 200
        # The most recent 200 should have item_ids 209 down to 10
        newest_id = kept_items[0].item_id
        oldest_id = kept_items[-1].item_id
        assert int(newest_id.replace("item", "")) > int(oldest_id.replace("item", ""))


class TestSearchTermsFor:
    def _term(self, keyword: str, aliases: list[str]) -> WatchTerm:
        return WatchTerm(keyword=keyword, aliases=aliases)

    def test_returns_primary_keyword(self):
        term = self._term("Aiko", [])
        result = _search_terms_for(term)
        assert result == ["Aiko"]

    def test_includes_aliases(self):
        term = self._term("Aiko", ["相川愛子", "Aiko Chan"])
        result = _search_terms_for(term)
        assert result == ["Aiko", "相川愛子", "Aiko Chan"]

    def test_deduplicates_case_insensitive(self):
        # "aiko" and "Aiko" are the same after casefold — only the first survives
        term = self._term("Aiko", ["aiko", "AIKO", "相川愛子"])
        result = _search_terms_for(term)
        assert result == ["Aiko", "相川愛子"]

    def test_strips_whitespace_from_aliases(self):
        term = self._term("Miku", ["  Miku Hatsune  "])
        result = _search_terms_for(term)
        assert "Miku Hatsune" in result

    def test_ignores_empty_aliases(self):
        term = self._term("Test", ["", "  ", "Valid"])
        result = _search_terms_for(term)
        assert "" not in result
        assert "   " not in result
        assert "Valid" in result

    def test_primary_not_duplicated_when_present_as_alias(self):
        term = self._term("Oshi", ["Oshi", "oshi", "Other"])
        result = _search_terms_for(term)
        assert result.count("Oshi") == 1

    def test_none_aliases_treated_as_empty(self):
        term = WatchTerm(keyword="Miku")
        term.aliases = None  # Simulate a None aliases value (e.g. from a legacy DB row)
        result = _search_terms_for(term)
        assert result == ["Miku"]
