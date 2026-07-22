from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from app import models as _models  # noqa: F401
from app.database import Base, SessionLocal
from app.relevance import prune_irrelevant_matches

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


PURGE_YOUTUBE_BAD_DATES_SLUG = "purge_youtube_fetch_time_dates_v2"
PURGE_YOUTUBE_GOOGLE_NEWS_SLUG = "purge_youtube_google_news_fallback_v1"


def run_youtube_bad_date_cleanup(engine: Engine) -> bool:
    """Run the YouTube bad-date cleanup once. Returns True when it ran."""
    if _migration_applied(PURGE_YOUTUBE_BAD_DATES_SLUG):
        return False
    _purge_bad_date_items(engine, platforms=("youtube",))
    _record_migration(PURGE_YOUTUBE_BAD_DATES_SLUG)
    return True


def run_youtube_google_news_cleanup(engine: Engine) -> bool:
    """Run the YouTube Google News fallback cleanup once. Returns True when it ran."""
    if _migration_applied(PURGE_YOUTUBE_GOOGLE_NEWS_SLUG):
        return False
    _purge_youtube_google_news_items(engine)
    _record_migration(PURGE_YOUTUBE_GOOGLE_NEWS_SLUG)
    return True


def force_youtube_google_news_cleanup(engine: Engine) -> int:
    """Remove YouTube Google News fallback rows even if the migration was already recorded."""
    return _purge_youtube_google_news_items(engine)


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


def _backfill_missing_defaults(engine: Engine) -> None:
    aliases_default = _json_default(engine)
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE watch_terms SET aliases = {aliases_default} WHERE aliases IS NULL"))
        conn.execute(text("UPDATE watch_terms SET collection_mode = 'all_info' WHERE collection_mode IS NULL"))
        conn.execute(text("UPDATE watch_terms SET is_active = TRUE WHERE is_active IS NULL"))
        conn.execute(text("UPDATE watch_terms SET notify_on_new = TRUE WHERE notify_on_new IS NULL"))
        conn.execute(text("UPDATE watch_terms SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
        conn.execute(text("UPDATE matches SET confidence = 1.0 WHERE confidence IS NULL"))
        conn.execute(text("""
            UPDATE matches
            SET created_at = COALESCE(
                (SELECT source_items.published_at FROM source_items WHERE source_items.id = matches.source_item_id),
                CURRENT_TIMESTAMP
            )
            WHERE created_at IS NULL
        """))


def _sqlite_has_unique_keyword_index(engine: Engine) -> bool:
    with engine.connect() as conn:
        indexes = conn.execute(text("PRAGMA index_list('watch_terms')")).fetchall()
        for index in indexes:
            mapping = index._mapping
            if not mapping.get("unique"):
                continue
            index_name = mapping["name"]
            columns = [
                row._mapping["name"]
                for row in conn.execute(text(f"PRAGMA index_info('{index_name}')")).fetchall()
            ]
            if columns == ["keyword"]:
                return True
    return False


def _rebuild_sqlite_watch_terms_without_keyword_unique(engine: Engine) -> None:
    """SQLite cannot drop a UNIQUE column constraint in-place, so rebuild the table."""
    log.info("Rebuilding watch_terms table without global keyword uniqueness")
    columns = [
        "id",
        "keyword",
        "aliases",
        "language_hint",
        "collection_mode",
        "is_active",
        "notify_on_new",
        "owner_device_secret",
        "created_at",
    ]
    existing_columns = _column_names(engine, "watch_terms")
    copy_columns = [column for column in columns if column in existing_columns]
    copy_sql = ", ".join(copy_columns)

    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        with conn.begin():
            conn.execute(text("""
                CREATE TABLE watch_terms_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword VARCHAR NOT NULL,
                    aliases JSON DEFAULT '[]',
                    language_hint VARCHAR,
                    collection_mode VARCHAR DEFAULT 'all_info',
                    is_active BOOLEAN DEFAULT TRUE,
                    notify_on_new BOOLEAN DEFAULT TRUE,
                    owner_device_secret VARCHAR,
                    created_at TIMESTAMP
                )
            """))
            conn.execute(text(
                f"INSERT INTO watch_terms_new ({copy_sql}) "
                f"SELECT {copy_sql} FROM watch_terms"
            ))
            conn.execute(text("DROP TABLE watch_terms"))
            conn.execute(text("ALTER TABLE watch_terms_new RENAME TO watch_terms"))
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()


def _drop_global_watch_term_keyword_uniqueness(engine: Engine) -> None:
    if "watch_terms" not in inspect(engine).get_table_names():
        return
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("ALTER TABLE watch_terms DROP CONSTRAINT IF EXISTS watch_terms_keyword_key"))
            conn.execute(text("DROP INDEX IF EXISTS ix_watch_terms_keyword"))
        elif engine.dialect.name == "sqlite":
            conn.execute(text("DROP INDEX IF EXISTS ix_watch_terms_keyword"))

    if engine.dialect.name == "sqlite" and _sqlite_has_unique_keyword_index(engine):
        _rebuild_sqlite_watch_terms_without_keyword_unique(engine)


def _create_watch_term_keyword_indexes(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_watch_terms_keyword"
            " ON watch_terms (keyword)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_watch_terms_owner_device_secret"
            " ON watch_terms (owner_device_secret)"
        ))
        if engine.dialect.name in {"postgresql", "sqlite"}:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_watch_terms_admin_keyword"
                " ON watch_terms (keyword)"
                " WHERE owner_device_secret IS NULL"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_watch_terms_owner_keyword"
                " ON watch_terms (owner_device_secret, keyword)"
                " WHERE owner_device_secret IS NOT NULL"
            ))


def _muted_feed_items_table_ddl(engine: Engine) -> str:
    id_column = "id INTEGER PRIMARY KEY"
    if engine.dialect.name == "postgresql":
        id_column = "id SERIAL PRIMARY KEY"
    elif engine.dialect.name == "sqlite":
        id_column = "id INTEGER PRIMARY KEY AUTOINCREMENT"
    return f"""
            CREATE TABLE IF NOT EXISTS muted_feed_items (
                {id_column},
                watch_term_id INTEGER NOT NULL REFERENCES watch_terms(id) ON DELETE CASCADE,
                source_item_id VARCHAR NOT NULL REFERENCES source_items(id),
                created_at TIMESTAMP,
                UNIQUE (watch_term_id, source_item_id)
            )
        """


def _repair_postgres_muted_feed_items_id_default(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as conn:
        conn.execute(text("""
            DO $$
            DECLARE
                next_id BIGINT;
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'muted_feed_items'
                      AND column_name = 'id'
                      AND column_default IS NULL
                      AND is_identity = 'NO'
                ) THEN
                    CREATE SEQUENCE IF NOT EXISTS muted_feed_items_id_seq;
                    ALTER SEQUENCE muted_feed_items_id_seq OWNED BY muted_feed_items.id;
                    SELECT COALESCE(MAX(id), 0) + 1 INTO next_id FROM muted_feed_items;
                    EXECUTE format('ALTER SEQUENCE muted_feed_items_id_seq RESTART WITH %s', next_id);
                    ALTER TABLE muted_feed_items
                        ALTER COLUMN id SET DEFAULT nextval('muted_feed_items_id_seq'::regclass);
                END IF;
            END $$;
        """))


def apply_startup_migrations(engine: Engine, *, run_cleanups: bool = True) -> None:
    Base.metadata.create_all(bind=engine)

    _add_missing_columns(
        engine,
        "watch_terms",
        {
            "aliases": f"JSON DEFAULT {_json_default(engine)}",
            "language_hint": "VARCHAR",
            "collection_mode": "VARCHAR DEFAULT 'all_info'",
            "is_active": "BOOLEAN DEFAULT TRUE",
            "notify_on_new": "BOOLEAN DEFAULT TRUE",
            "owner_device_secret": "VARCHAR",
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
        "apns_device_tokens",
        {
            "device_secret": "VARCHAR",
            "is_verified": "BOOLEAN DEFAULT FALSE",
            "verified_at": "TIMESTAMP",
            "verification_attempted_at": "TIMESTAMP",
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
    _backfill_missing_defaults(engine)
    _drop_global_watch_term_keyword_uniqueness(engine)
    _create_watch_term_keyword_indexes(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_matches_source_item_id"
            " ON matches (source_item_id)"
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
        conn.execute(text(_muted_feed_items_table_ddl(engine)))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_muted_feed_items_watch_term_id"
            " ON muted_feed_items (watch_term_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_muted_feed_items_source_item_id"
            " ON muted_feed_items (source_item_id)"
        ))
    _repair_postgres_muted_feed_items_id_default(engine)

    if not run_cleanups:
        return

    # One-time cleanup (guarded): remove source_items where published_at ≈ matched_at
    # (within 60 s) for platforms whose date parsers previously fell back to
    # datetime.now().  They will be re-fetched with real dates on next poll.
    PURGE_SLUG = "purge_bad_dates_v1"
    if not _migration_applied(PURGE_SLUG):
        _purge_bad_date_items(engine, platforms=("tver", "togetter", "youtube"))
        _record_migration(PURGE_SLUG)

    # YouTube's scrape fallback used to assign datetime.now() when a search result
    # omitted its publish time, pinning old videos to the top of the feed. Re-run
    # the bad-date purge for YouTube under a new slug so existing deployments clean
    # up rows that were inserted after the first broad cleanup ran.
    run_youtube_bad_date_cleanup(engine)

    # YouTube's Google News fallback used Google News discovery time, not the
    # video's real upload/update time. Remove those rows so old videos stop
    # appearing as newly updated feed items.
    run_youtube_google_news_cleanup(engine)

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

    RELEVANCE_SLUG = "purge_irrelevant_matches_v1"
    if not _migration_applied(RELEVANCE_SLUG):
        _purge_irrelevant_matches()
        _record_migration(RELEVANCE_SLUG)

    PURGE_5CH_SLUG = "purge_legacy_5ch_mirror_and_stale_v1"
    if not _migration_applied(PURGE_5CH_SLUG):
        _purge_legacy_5ch_items(engine)
        _record_migration(PURGE_5CH_SLUG)

def _purge_irrelevant_matches() -> None:
    db: Session = SessionLocal()
    try:
        removed = prune_irrelevant_matches(db)
        db.commit()
        if removed:
            log.info("Purged %d irrelevant legacy match records", removed)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _purge_legacy_5ch_items(engine: Engine) -> None:
    """Delete old 5ch mirror rows.

    5ch previously used 2ch.sc mirrors and sometimes stored long-lived stale
    threads. The connector now prefers real itest.5ch rows and filters to the
    recent window, so legacy mirror/stale rows should be removed from feeds.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=31)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "SELECT COUNT(*) FROM source_items"
                " WHERE platform = '5ch'"
                " AND (url LIKE '%2ch.sc/%' OR id LIKE '5ch:2ch.sc:%' OR published_at < :cutoff)"
            ),
            {"cutoff": cutoff},
        )
        bad_count = result.scalar() or 0
        if bad_count == 0:
            return
        log.info("Purging %d legacy/stale 5ch source items", bad_count)
        conn.execute(
            text(
                "DELETE FROM matches WHERE source_item_id IN ("
                "  SELECT id FROM source_items"
                "  WHERE platform = '5ch'"
                "  AND (url LIKE '%2ch.sc/%' OR id LIKE '5ch:2ch.sc:%' OR published_at < :cutoff)"
                ")"
            ),
            {"cutoff": cutoff},
        )
        conn.execute(
            text(
                "DELETE FROM source_items"
                " WHERE platform = '5ch'"
                " AND (url LIKE '%2ch.sc/%' OR id LIKE '5ch:2ch.sc:%' OR published_at < :cutoff)"
            ),
            {"cutoff": cutoff},
        )
        conn.execute(text(
            "DELETE FROM source_items "
            "WHERE id NOT IN (SELECT source_item_id FROM matches) "
            "AND id NOT IN (SELECT source_item_id FROM muted_feed_items)"
        ))


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
            "DELETE FROM source_items "
            "WHERE id NOT IN (SELECT source_item_id FROM matches) "
            "AND id NOT IN (SELECT source_item_id FROM muted_feed_items)"
        ))


def _purge_youtube_google_news_items(engine: Engine) -> int:
    if engine.dialect.name == "postgresql":
        source_predicate = "si.raw_payload ->> 'source' = 'google_news'"
    else:
        source_predicate = "json_extract(si.raw_payload, '$.source') = 'google_news'"

    with engine.begin() as conn:
        result = conn.execute(text(f"""
            SELECT COUNT(*) FROM source_items si
            WHERE si.platform = 'youtube'
            AND {source_predicate}
        """))
        bad_count = result.scalar() or 0
        if bad_count == 0:
            return 0
        log.info("Purging %d YouTube Google News fallback source items", bad_count)
        conn.execute(text(f"""
            DELETE FROM matches WHERE source_item_id IN (
                SELECT si.id FROM source_items si
                WHERE si.platform = 'youtube'
                AND {source_predicate}
            )
        """))
        conn.execute(text(
            "DELETE FROM source_items "
            "WHERE id NOT IN (SELECT source_item_id FROM matches) "
            "AND id NOT IN (SELECT source_item_id FROM muted_feed_items)"
        ))
        return int(bad_count)
