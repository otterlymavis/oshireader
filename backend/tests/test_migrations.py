"""Tests for apply_startup_migrations — idempotency and column backfill."""
from __future__ import annotations

import pytest
from unittest.mock import patch
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from datetime import datetime, timedelta, timezone

from app.database import Base
import app.migrations as _migrations_mod
from app.migrations import _add_missing_columns, _purge_bad_date_items, apply_startup_migrations
from app.models import APNSDeviceToken, Match, MigrationLog, MutedFeedItem, SourceItem, WatchTerm


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

    def test_muted_feed_item_id_autogenerates_after_startup_migration(self, fresh_engine):
        Session = sessionmaker(bind=fresh_engine)
        with patch("app.migrations.SessionLocal", Session):
            apply_startup_migrations(fresh_engine)

        db = Session()
        try:
            term = WatchTerm(keyword="Muted", aliases=[])
            item = SourceItem(
                id="news:muted",
                platform="news",
                item_id="muted",
                url="https://example.com/muted",
                published_at=datetime.now(timezone.utc),
                media_type="article",
                title="Muted",
            )
            db.add_all([term, item])
            db.flush()
            mute = MutedFeedItem(watch_term_id=term.id, source_item_id=item.id)
            db.add(mute)
            db.commit()

            assert mute.id is not None
        finally:
            db.close()

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

    def test_can_skip_cleanup_migrations_on_serving_startup(self, fresh_engine):
        Session = sessionmaker(bind=fresh_engine)
        with patch("app.migrations.SessionLocal", Session), \
             patch("app.migrations._purge_bad_date_items") as purge_bad_dates, \
             patch("app.migrations._purge_girlschannel_googlenews_items") as purge_girlschannel, \
             patch("app.migrations._purge_irrelevant_matches") as purge_irrelevant, \
             patch("app.migrations._purge_legacy_5ch_items") as purge_5ch:
            apply_startup_migrations(fresh_engine, run_cleanups=False)

        assert "owner_device_secret" in _column_names(fresh_engine, "watch_terms")
        assert "updated_at" in _column_names(fresh_engine, "platform_credentials")
        purge_bad_dates.assert_not_called()
        purge_girlschannel.assert_not_called()
        purge_irrelevant.assert_not_called()
        purge_5ch.assert_not_called()

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

    def test_startup_preserves_terms_created_before_apns_registration(self, fresh_engine):
        Base.metadata.create_all(bind=fresh_engine)
        Session = sessionmaker(bind=fresh_engine)
        db = Session()
        reachable_secret = "reachable-secret"
        orphaned_secret = "orphaned-secret"
        reachable = WatchTerm(keyword="Reachable", owner_device_secret=reachable_secret)
        orphaned = WatchTerm(keyword="Orphaned", owner_device_secret=orphaned_secret)
        admin = WatchTerm(keyword="Admin")
        item = SourceItem(
            id="news:orphaned",
            platform="news",
            item_id="orphaned",
            url="https://example.com/orphaned",
            published_at=datetime.now(timezone.utc),
            media_type="article",
            title="Orphaned",
        )
        db.add_all([
            APNSDeviceToken(
                token="a" * 64,
                environment="sandbox",
                device_secret=reachable_secret,
                is_verified=True,
            ),
            reachable,
            orphaned,
            admin,
            item,
        ])
        db.flush()
        db.add(Match(watch_term_id=orphaned.id, source_item_id=item.id))
        db.commit()
        db.close()

        with patch("app.migrations.SessionLocal", Session):
            apply_startup_migrations(fresh_engine)

        db = Session()
        try:
            keywords = {term.keyword for term in db.query(WatchTerm).all()}
            assert keywords == {"Reachable", "Orphaned", "Admin"}
            assert db.query(Match).count() == 1
            assert db.query(SourceItem).count() == 1
        finally:
            db.close()

    def test_backfills_required_columns_for_legacy_rows(self, fresh_engine):
        with fresh_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE watch_terms (
                    id INTEGER PRIMARY KEY,
                    keyword VARCHAR NOT NULL UNIQUE
                )
            """))
            conn.execute(text("""
                CREATE TABLE source_items (
                    id VARCHAR PRIMARY KEY,
                    platform VARCHAR NOT NULL,
                    item_id VARCHAR NOT NULL,
                    url VARCHAR NOT NULL,
                    published_at TIMESTAMP NOT NULL,
                    author VARCHAR,
                    title VARCHAR,
                    content_text TEXT,
                    media_type VARCHAR
                )
            """))
            conn.execute(text("""
                CREATE TABLE matches (
                    id INTEGER PRIMARY KEY,
                    watch_term_id INTEGER NOT NULL,
                    source_item_id VARCHAR NOT NULL
                )
            """))
            published_at = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
            conn.execute(text("INSERT INTO watch_terms (id, keyword) VALUES (1, 'Legacy')"))
            conn.execute(
                text(
                    "INSERT INTO source_items "
                    "(id, platform, item_id, url, published_at, title, media_type) "
                    "VALUES ('news:legacy', 'news', 'legacy', 'https://example.com/legacy', :published_at, 'Legacy news', 'article')"
                ),
                {"published_at": published_at.isoformat()},
            )
            conn.execute(text("INSERT INTO matches (id, watch_term_id, source_item_id) VALUES (1, 1, 'news:legacy')"))

        Session = sessionmaker(bind=fresh_engine)
        with patch("app.migrations.SessionLocal", Session):
            apply_startup_migrations(fresh_engine)

        db = Session()
        try:
            term = db.get(WatchTerm, 1)
            match = db.get(Match, 1)
            assert term.aliases == []
            assert term.collection_mode == "all_info"
            assert term.is_active is True
            assert term.notify_on_new is True
            assert term.created_at is not None
            assert match.confidence == 1.0
            assert match.created_at is not None
        finally:
            db.close()

    def test_replaces_global_keyword_unique_with_owner_scoped_uniqueness(self, fresh_engine):
        with fresh_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE watch_terms (
                    id INTEGER PRIMARY KEY,
                    keyword VARCHAR NOT NULL UNIQUE,
                    owner_device_secret VARCHAR
                )
            """))
            conn.execute(text("INSERT INTO watch_terms (id, keyword) VALUES (1, 'Legacy')"))

        Session = sessionmaker(bind=fresh_engine)
        with patch("app.migrations.SessionLocal", Session):
            apply_startup_migrations(fresh_engine)

        with fresh_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO watch_terms (keyword, owner_device_secret)"
                " VALUES ('Aiko', 'owner-a')"
            ))
            conn.execute(text(
                "INSERT INTO watch_terms (keyword, owner_device_secret)"
                " VALUES ('Aiko', 'owner-b')"
            ))

        with pytest.raises(IntegrityError):
            with fresh_engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO watch_terms (keyword, owner_device_secret)"
                    " VALUES ('Aiko', 'owner-a')"
                ))


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
