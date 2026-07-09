"""Tests for backend diagnostics helpers."""
from __future__ import annotations

from app.diagnostics import record_backend_event
from app.models import BackendEvent


def test_record_backend_event_rolls_back_after_flush_failure(db_session):
    record_backend_event(
        db_session,
        "poll",
        "bad_payload",
        "This payload cannot be JSON encoded",
        {"bad": object()},
    )

    db_session.add(BackendEvent(kind="poll", status="usable_after_failure"))
    db_session.commit()

    event = db_session.query(BackendEvent).filter_by(status="usable_after_failure").one()
    assert event.kind == "poll"
