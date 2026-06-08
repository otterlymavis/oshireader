"""Tests for apply_startup_migrations — idempotency and column backfill."""
from __future__ import annotations

import pytest
from unittest.mock import patch
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.migrations import _add_missing_columns, apply_startup_migrations
from app.models import MigrationLog


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
        assert "migration_log" in tables

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
