from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from app import models as _models  # noqa: F401
from app.database import Base, SessionLocal

log = logging.getLogger(__name__)


def _migration_applied(slug: str) -> bool:
    db: Session = SessionLocal()
    try:
        return db.get(_models.MigrationLog, slug) is not None
    finally:
        db.close()


def _record_migration(slug: str) -> None:
    db: Session = SessionLocal()
    try:
        db.add(_models.MigrationLog(id=slug, applied_at=datetime.now(timezone.utc)))
        db.commit()
    finally:
        db.close()


def _column_names(engine: Engine, table_name: str) -> set[str]:
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _json_default(engine: Engine) -> str:
    if engine.dialect.name == "postgresql":
        return "'[]'::json"
    return "'[]'"


def _add_missing_columns(engine: Engine, table_name: str, columns: dict[str, str]) -> None:
    existing = _column_names(engine, table_name)
    with engine.begin() as conn:
        for name, ddl in columns.items():
            if name in existing:
                continue
            log.info("Adding missing column %s.%s", table_name, name)
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))


def apply_startup_migrations(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)

    _add_missing_columns(
        engine,
        "watch_terms",
        {
            "aliases": f"JSON DEFAULT {_json_default(engine)}",
            "language_hint": "VARCHAR",
            "collection_mode": "VARCHAR DEFAULT 'all_info'",
            "is_active": "BOOLEAN DEFAULT TRUE",
            "notify_on_new": "BOOLEAN DEFAULT FALSE",
            "created_at": "TIMESTAMP",
        },
    )
    _add_missing_columns(
        engine,
        "source_items",
        {
            "thumbnail_url": "VARCHAR",
            "raw_payload": "JSON",
            "fetched_at": "TIMESTAMP",
        },
    )
    _add_missing_columns(
        engine,
        "matches",
        {
            "confidence": "FLOAT DEFAULT 1.0",
            "created_at": "TIMESTAMP",
        },
    )
    _add_missing_columns(
        engine,
        "platform_credentials",
        {
            "updated_at": "TIMESTAMP",
        },
    )
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_matches_source_item_id"
            " ON matches (source_item_id)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_watch_terms_keyword"
            " ON watch_terms (keyword)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_matches_created_at"
            " ON matches (created_at)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_matches_watch_term_id"
            " ON matches (watch_term_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_source_items_published_at"
            " ON source_items (published_at DESC)"
        ))

    # One-time cleanup (guarded): remove source_items where published_at ≈ matched_at
    # (within 60 s) for platforms whose date parsers previously fell back to
    # datetime.now().  They will be re-fetched with real dates on next poll.
    PURGE_SLUG = "purge_bad_dates_v1"
    if not _migration_applied(PURGE_SLUG):
        _purge_bad_date_items(engine, platforms=("tver", "togetter", "youtube"))
        _record_migration(PURGE_SLUG)

    # One-time cleanup: remove GirlsChannel items stored via Google News (item_id starts
    # with "http").  The connector now scrapes GirlsChannel directly using numeric topic
    # IDs, so old entries have wrong URLs, wrong dates, and will never be healed.
    PURGE_GC_SLUG = "purge_girlschannel_googlenews_v1"
    if not _migration_applied(PURGE_GC_SLUG):
        _purge_girlschannel_googlenews_items(engine)
        _record_migration(PURGE_GC_SLUG)

    # One-time: enable new-item notifications on existing terms. The flag previously
    # defaulted to False, so terms created before this release never notified even
    # though users expect to be alerted on new feed items.
    NOTIFY_SLUG = "enable_notify_on_new_v1"
    if not _migration_applied(NOTIFY_SLUG):
        with engine.begin() as conn:
            conn.execute(text("UPDATE watch_terms SET notify_on_new = TRUE WHERE notify_on_new = FALSE"))
        _record_migration(NOTIFY_SLUG)


def _purge_girlschannel_googlenews_items(engine: Engine) -> None:
    """Delete GirlsChannel source_items whose item_id is a URL (from the old Google News
    connector). The new direct scraper uses numeric topic IDs so these will never be healed."""
    with engine.begin() as conn:
        result = conn.execute(text(
            "SELECT COUNT(*) FROM source_items"
            " WHERE platform = 'girlschannel' AND id LIKE 'girlschannel:http%'"
        ))
        bad_count = result.scalar() or 0
        if bad_count == 0:
            return
        log.info("Purging %d old GirlsChannel Google-News items", bad_count)
        conn.execute(text(
            "DELETE FROM matches WHERE source_item_id IN ("
            "  SELECT id FROM source_items"
            "  WHERE platform = 'girlschannel' AND id LIKE 'girlschannel:http%'"
            ")"
        ))
        conn.execute(text(
            "DELETE FROM source_items"
            " WHERE platform = 'girlschannel' AND id LIKE 'girlschannel:http%'"
        ))


def _purge_bad_date_items(engine: Engine, platforms: tuple[str, ...]) -> None:
    """Delete source_items (and their matches) whose published_at was set to
    the fetch time rather than the real article date.  Identified by
    |published_at - match.created_at| < 60 seconds."""
    placeholders = ", ".join(f"'{p}'" for p in platforms)
    if engine.dialect.name == "postgresql":
        epoch_diff = "ABS(EXTRACT(EPOCH FROM (si.published_at - m.created_at)))"
    else:
        epoch_diff = "ABS((JULIANDAY(si.published_at) - JULIANDAY(m.created_at)) * 86400)"

    with engine.begin() as conn:
        result = conn.execute(text(f"""
            SELECT COUNT(*) FROM source_items si
            JOIN matches m ON m.source_item_id = si.id
            WHERE si.platform IN ({placeholders})
            AND {epoch_diff} < 60
        """))
        bad_count = result.scalar() or 0
        if bad_count == 0:
            return
        log.info("Purging %d bad-date source items for %s", bad_count, platforms)
        conn.execute(text(f"""
            DELETE FROM matches WHERE source_item_id IN (
                SELECT si.id FROM source_items si
                JOIN matches m ON m.source_item_id = si.id
                WHERE si.platform IN ({placeholders})
                AND {epoch_diff} < 60
            )
        """))
        conn.execute(text(
            "DELETE FROM source_items WHERE id NOT IN (SELECT source_item_id FROM matches)"
        ))
