from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

from app import models as _models  # noqa: F401
from app.database import Base

log = logging.getLogger(__name__)


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
