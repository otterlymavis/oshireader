from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import BackendEvent

log = logging.getLogger(__name__)


def record_backend_event(
    db: Session,
    kind: str,
    status: str,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Persist a compact backend event for production diagnostics.

    This intentionally swallows failures: diagnostics must never break ingestion
    or notification delivery.
    """
    try:
        db.add(BackendEvent(kind=kind, status=status, message=message, payload=payload or {}))
        db.flush()
    except Exception as exc:
        db.rollback()
        log.warning("Failed to record backend event kind=%s status=%s: %s", kind, status, exc)


def prune_backend_events(db: Session, keep: int = 500) -> None:
    try:
        db.execute(text("""
            DELETE FROM backend_events
            WHERE id NOT IN (
                SELECT id FROM backend_events
                ORDER BY created_at DESC, id DESC
                LIMIT :keep
            )
        """), {"keep": keep})
        db.flush()
    except Exception as exc:
        db.rollback()
        log.warning("Failed to prune backend events: %s", exc)
