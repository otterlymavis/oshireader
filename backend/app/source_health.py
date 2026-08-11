"""In-process, per-platform poll health used to answer "is this source working
right now?" for the client. Deliberately not persisted: Render runs a single
uvicorn worker (see render.yaml), so an in-memory dict is consistent across
requests and avoids adding DB writes to the fetch hot path. State resets on
deploy/restart, which is fine for a live-status signal that self-heals within
one poll cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_lock = Lock()
_health: dict[str, dict] = {}
_cycle = 0


def begin_poll_cycle() -> None:
    """Mark the start of a new poll cycle, called once per _poll_once_unlocked run.

    Lets record_filtered tell "already recorded a real result earlier this
    same cycle" (skip, don't clobber it) apart from "recorded in some earlier
    cycle and never touched since" (a platform fetched only by MEDIA_ONLY
    terms would otherwise freeze at its first-ever status forever).
    """
    global _cycle
    with _lock:
        _cycle += 1


def record_fetch_result(platform: str, *, succeeded: bool, item_count: int = 0, error: str | None = None) -> None:
    now = _utcnow()
    with _lock:
        entry = _health.setdefault(platform, {"consecutive_failures": 0})
        entry["last_checked_at"] = now
        entry["cycle"] = _cycle
        if succeeded:
            entry["status"] = "success" if item_count else "empty"
            entry["last_success_at"] = now
            entry["last_item_count"] = item_count
            entry["last_error"] = None
            entry["consecutive_failures"] = 0
        else:
            entry["status"] = "failure"
            entry["last_error"] = error
            entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1


def record_filtered(platform: str) -> None:
    """Record that a fetch was skipped by mode filtering (e.g. MEDIA_ONLY), not attempted.

    Skips only when this same platform already got a real success/failure
    recorded earlier in the current poll cycle, so it doesn't clobber that
    result. Otherwise it always updates, including on repeat calls in later
    cycles, so a platform fetched exclusively by MEDIA_ONLY terms still gets
    a fresh last_checked_at every cycle instead of freezing at its first one.
    """
    now = _utcnow()
    with _lock:
        entry = _health.get(platform)
        if entry is not None and entry.get("cycle") == _cycle and entry.get("status") != "filtered":
            return
        _health[platform] = {
            "consecutive_failures": 0,
            "status": "filtered",
            "last_checked_at": now,
            "cycle": _cycle,
        }


def snapshot() -> list[dict]:
    with _lock:
        return [
            {
                "platform": platform,
                "status": entry.get("status"),
                "last_checked_at": entry.get("last_checked_at"),
                "last_success_at": entry.get("last_success_at"),
                "last_item_count": entry.get("last_item_count"),
                "last_error": entry.get("last_error"),
                "consecutive_failures": entry.get("consecutive_failures", 0),
            }
            for platform, entry in sorted(_health.items())
        ]
