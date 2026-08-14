from __future__ import annotations

import pytest

from app import source_health


@pytest.fixture(autouse=True)
def _reset_source_health():
    with source_health._lock:
        source_health._health.clear()
        source_health._cycle = 0
    yield
    with source_health._lock:
        source_health._health.clear()
        source_health._cycle = 0


def _entry(platform: str) -> dict:
    return next(item for item in source_health.snapshot() if item["platform"] == platform)


def test_failure_survives_a_later_success_in_the_same_poll_cycle():
    source_health.begin_poll_cycle()
    source_health.record_fetch_result("mdpr", succeeded=False, error="SourceUnavailableError")
    source_health.record_fetch_result("mdpr", succeeded=True, item_count=0)

    entry = _entry("mdpr")
    assert entry["status"] == "failure"
    assert entry["last_error"] == "SourceUnavailableError"
    assert entry["consecutive_failures"] == 1


def test_failure_after_success_marks_the_whole_poll_cycle_failed():
    source_health.begin_poll_cycle()
    source_health.record_fetch_result("oricon", succeeded=True, item_count=2)
    source_health.record_fetch_result("oricon", succeeded=False, error="TimeoutError")

    entry = _entry("oricon")
    assert entry["status"] == "failure"
    assert entry["last_error"] == "TimeoutError"
    assert entry["consecutive_failures"] == 1


def test_multiple_failures_count_once_per_poll_cycle_and_successful_cycle_recovers():
    source_health.begin_poll_cycle()
    source_health.record_fetch_result("twitter", succeeded=False, error="TimeoutError")
    source_health.record_fetch_result("twitter", succeeded=False, error="SourceUnavailableError")
    assert _entry("twitter")["consecutive_failures"] == 1

    source_health.begin_poll_cycle()
    source_health.record_fetch_result("twitter", succeeded=False, error="TimeoutError")
    assert _entry("twitter")["consecutive_failures"] == 2

    source_health.begin_poll_cycle()
    source_health.record_fetch_result("twitter", succeeded=True, item_count=0)
    entry = _entry("twitter")
    assert entry["status"] == "empty"
    assert entry["consecutive_failures"] == 0
    assert entry["last_error"] is None


def test_success_status_survives_a_later_empty_search_in_the_same_cycle():
    source_health.begin_poll_cycle()
    source_health.record_fetch_result("mdpr", succeeded=True, item_count=3)
    source_health.record_fetch_result("mdpr", succeeded=True, item_count=0)

    entry = _entry("mdpr")
    assert entry["status"] == "success"
    assert entry["last_item_count"] == 3
