"""Tests for apply_startup_migrations — idempotency and column backfill."""
from __future__ import annotations

import pytest
from unittest.mock import patch
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from datetime import datetime, timedelta, timezone

from app.database import Base
import app.migrations as _migrations_mod
from app.migrations import _add_missing_columns, _purge_bad_date_items, apply_startup_migrations
from app.models import Match, MigrationLog, SourceItem, WatchTerm


@pytest.fixture()
def fresh_engine():
    """Bare SQLite in-memory engine with no tables — simulates a first boot."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


def _column_names(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


class TestApplyStartupMigrations:
    def test_creates_all_tables(self, fresh_engine):
        with patch("app.migrations.SessionLocal", sessionmaker(bind=fresh_engine)):
            apply_startup_migrations(fresh_engine)

        tables = inspect(fresh_engine).get_table_names()
        assert "watch_terms" in tables
        assert "source_items" in tables
        assert "matches" in tables
        assert "platform_credentials" in tables
        assert "apns_device_tokens" in tables
        assert "backend_events" in tables
        assert "migration_log" in tables
        assert "device_secret" in _column_names(fresh_engine, "apns_device_tokens")

    def test_idempotent_second_call_succeeds(self, fresh_engine):
        Session = sessionmaker(bind=fresh_engine)
        with patch("app.migrations.SessionLocal", Session):
            apply_startup_migrations(fresh_engine)
            apply_startup_migrations(fresh_engine)  # must not raise

    def test_purge_migration_recorded_after_first_run(self, fresh_engine):
        Session = sessionmaker(bind=fresh_engine)
        with patch("app.migrations.SessionLocal", Session):
            apply_startup_migrations(fresh_engine)

        db = Session()
        try:
            log_entry = db.get(MigrationLog, "purge_bad_dates_v1")
            assert log_entry is not None
        finally:
            db.close()

    def test_purge_migration_runs_only_once(self, fresh_engine):
        Session = sessionmaker(bind=fresh_engine)
        purge_call_count = 0
        real_purge = __import__("app.migrations", fromlist=["_purge_bad_date_items"])._purge_bad_date_items

        def counting_purge(engine, **kw):
            nonlocal purge_call_count
            purge_call_count += 1
            real_purge(engine, **kw)

        with patch("app.migrations.SessionLocal", Session), \
             patch("app.migrations._purge_bad_date_items", side_effect=counting_purge):
            apply_startup_migrations(fresh_engine)
            apply_startup_migrations(fresh_engine)

        assert purge_call_count == 1

    def test_relevance_migration_removes_summary_only_article_match(self, fresh_engine):
        Base.metadata.create_all(bind=fresh_engine)
        Session = sessionmaker(bind=fresh_engine)
        db = Session()
        term = WatchTerm(keyword="吉沢亮", aliases=[])
        db.add(term)
        db.flush()
        relevant = SourceItem(
            id="note:relevant",
            platform="note",
            item_id="relevant",
            url="https://note.com/relevant",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="吉沢亮の最新インタビュー",
        )
        stale = SourceItem(
            id="note:stale",
            platform="note",
            item_id="stale",
            url="https://note.com/stale",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="映画『国宝』の感想",
            content_text="吉沢亮について本文で触れています",
        )
        db.add_all([relevant, stale])
        db.flush()
        relevant_id = relevant.id
        stale_id = stale.id
        db.add_all([
            Match(watch_term_id=term.id, source_item_id=relevant_id),
            Match(watch_term_id=term.id, source_item_id=stale_id),
        ])
        db.commit()
        db.close()

        with patch("app.migrations.SessionLocal", Session):
            apply_startup_migrations(fresh_engine)

        db = Session()
        try:
            assert db.query(Match).count() == 1
            assert db.get(SourceItem, relevant_id) is not None
            assert db.get(SourceItem, stale_id) is None
            assert db.get(MigrationLog, "purge_irrelevant_matches_v1") is not None
        finally:
            db.close()


class TestAddMissingColumns:
    def test_adds_column_when_absent(self, fresh_engine):
        Base.metadata.create_all(bind=fresh_engine)
        # Remove a known column from the live table via ALTER isn't easily reversible;
        # instead create a minimal table and test adding a new column into it.
        with fresh_engine.begin() as conn:
            conn.execute(text("CREATE TABLE _test_tbl (id INTEGER PRIMARY KEY)"))

        _add_missing_columns(fresh_engine, "_test_tbl", {"extra_col": "TEXT"})
        assert "extra_col" in _column_names(fresh_engine, "_test_tbl")

    def test_skips_existing_column(self, fresh_engine):
        with fresh_engine.begin() as conn:
            conn.execute(text("CREATE TABLE _test_tbl2 (id INTEGER PRIMARY KEY, existing_col TEXT)"))

        # Should not raise even though existing_col is already there
        _add_missing_columns(fresh_engine, "_test_tbl2", {"existing_col": "TEXT"})
        assert "existing_col" in _column_names(fresh_engine, "_test_tbl2")

    def test_column_names_returns_empty_set_for_missing_table(self, fresh_engine):
        result = _migrations_mod._column_names(fresh_engine, "nonexistent_table")
        assert result == set()


class TestPurgeBadDateItems:
    def _setup(self, fresh_engine):
        Base.metadata.create_all(bind=fresh_engine)
        Session = sessionmaker(bind=fresh_engine)
        return Session()

    def test_purges_item_with_bad_date(self, fresh_engine):
        """An item whose published_at is within 60s of matched_at must be deleted."""
        db = self._setup(fresh_engine)
        now = datetime.now(timezone.utc)

        with fresh_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO source_items (id, platform, item_id, url, published_at, media_type)"
                " VALUES ('youtube:bad', 'youtube', 'bad', 'https://youtu.be/bad', :pub, 'video')"
            ), {"pub": now.isoformat()})
            conn.execute(text(
                "INSERT INTO matches (source_item_id, watch_term_id, created_at)"
                " VALUES ('youtube:bad', 1, :mat)"
            ), {"mat": now.isoformat()})

        _purge_bad_date_items(fresh_engine, platforms=("youtube",))

        with fresh_engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM source_items WHERE id='youtube:bad'")).scalar()
        assert count == 0, "Bad-date item should have been purged"
        db.close()

    def test_keeps_item_with_good_date(self, fresh_engine):
        """An item whose published_at is hours before matched_at must be kept."""
        db = self._setup(fresh_engine)
        now = datetime.now(timezone.utc)
        old_pub = (now - timedelta(hours=6)).isoformat()

        with fresh_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO source_items (id, platform, item_id, url, published_at, media_type)"
                " VALUES ('youtube:good', 'youtube', 'good', 'https://youtu.be/good', :pub, 'video')"
            ), {"pub": old_pub})
            conn.execute(text(
                "INSERT INTO matches (source_item_id, watch_term_id, created_at)"
                " VALUES ('youtube:good', 1, :mat)"
            ), {"mat": now.isoformat()})

        _purge_bad_date_items(fresh_engine, platforms=("youtube",))

        with fresh_engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM source_items WHERE id='youtube:good'")).scalar()
        assert count == 1, "Good-date item must survive the purge"
        db.close()

    def test_only_purges_named_platforms(self, fresh_engine):
        """Platforms not in the list must never be touched."""
        db = self._setup(fresh_engine)
        now = datetime.now(timezone.utc)

        with fresh_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO source_items (id, platform, item_id, url, published_at, media_type)"
                " VALUES ('news:safe', 'news', 'safe', 'https://news.example.com/s', :pub, 'article')"
            ), {"pub": now.isoformat()})
            conn.execute(text(
                "INSERT INTO matches (source_item_id, watch_term_id, created_at)"
                " VALUES ('news:safe', 1, :mat)"
            ), {"mat": now.isoformat()})

        # Only purge 'youtube' — 'news' items must be untouched
        _purge_bad_date_items(fresh_engine, platforms=("youtube",))

        with fresh_engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM source_items WHERE id='news:safe'")).scalar()
        assert count == 1, "Non-targeted platform item must not be purged"
        db.close()
