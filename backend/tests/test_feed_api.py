"""Tests for the /api/feed and /api/watch-terms endpoints."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Match, SourceItem, WatchTerm

# Supply a token so admin endpoints work in tests.
os.environ.setdefault("ADMIN_API_TOKEN", "test-token")
_AUTH = {"Authorization": "Bearer test-token"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_term(db, keyword="テスト", **kw):
    term = WatchTerm(keyword=keyword, aliases=[], **kw)
    db.add(term)
    db.commit()
    db.refresh(term)
    return term


def _make_item(db, platform="news", item_id="item1", days_ago=1, media_type="article"):
    published = datetime.now(timezone.utc) - timedelta(days=days_ago)
    item = SourceItem(
        id=f"{platform}:{item_id}",
        platform=platform,
        item_id=item_id,
        url=f"https://example.com/{item_id}",
        published_at=published,
        media_type=media_type,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _make_match(db, term, item):
    match = Match(watch_term_id=term.id, source_item_id=item.id)
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


# ---------------------------------------------------------------------------
# /api/watch-terms
# ---------------------------------------------------------------------------

class TestWatchTerms:
    def test_create_term(self, client):
        resp = client.post(
            "/api/watch-terms/",
            json={"keyword": "アイドル"},
            headers=_AUTH,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["keyword"] == "アイドル"
        assert data["is_active"] is True
        assert data["collection_mode"] == "all_info"

    def test_create_duplicate_keyword_fails(self, client):
        client.post("/api/watch-terms/", json={"keyword": "dup"}, headers=_AUTH)
        resp = client.post("/api/watch-terms/", json={"keyword": "dup"}, headers=_AUTH)
        assert resp.status_code in {400, 409, 422, 500}

    def test_list_terms_empty(self, client):
        resp = client.get("/api/watch-terms/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_update_term(self, client, db_session):
        term = _make_term(db_session, keyword="before")
        resp = client.patch(
            f"/api/watch-terms/{term.id}",
            json={"keyword": "after"},
            headers=_AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["keyword"] == "after"

    def test_delete_term(self, client, db_session):
        term = _make_term(db_session, keyword="gone")
        resp = client.delete(f"/api/watch-terms/{term.id}", headers=_AUTH)
        assert resp.status_code == 204
        assert client.get("/api/watch-terms/").json() == []

    def test_update_missing_term_returns_404(self, client):
        resp = client.patch("/api/watch-terms/9999", json={"keyword": "x"}, headers=_AUTH)
        assert resp.status_code == 404

    def test_created_at_is_utc_aware(self, client):
        client.post("/api/watch-terms/", json={"keyword": "tz"}, headers=_AUTH)
        data = client.get("/api/watch-terms/").json()
        ts = data[0]["created_at"]
        assert ts.endswith("Z") or "+" in ts, f"created_at not UTC-aware: {ts!r}"


# ---------------------------------------------------------------------------
# /api/feed
# ---------------------------------------------------------------------------

class TestFeedAPI:
    def test_feed_empty(self, client):
        resp = client.get("/api/feed/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_feed_returns_matched_item(self, client, db_session):
        term = _make_term(db_session)
        item = _make_item(db_session)
        _make_match(db_session, term, item)

        resp = client.get("/api/feed/")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["item"]["id"] == item.id
        assert rows[0]["watch_term_keyword"] == term.keyword

    def test_feed_filter_by_term_id(self, client, db_session):
        t1 = _make_term(db_session, keyword="one")
        t2 = _make_term(db_session, keyword="two")
        i1 = _make_item(db_session, item_id="i1")
        i2 = _make_item(db_session, item_id="i2")
        _make_match(db_session, t1, i1)
        _make_match(db_session, t2, i2)

        resp = client.get(f"/api/feed/?term_id={t1.id}")
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["watch_term_id"] == t1.id

    def test_feed_days_filter_excludes_old_items(self, client, db_session):
        term = _make_term(db_session)
        old = _make_item(db_session, item_id="old", days_ago=60)
        new = _make_item(db_session, item_id="new", days_ago=1)
        _make_match(db_session, term, old)
        _make_match(db_session, term, new)

        resp = client.get("/api/feed/?days=30")
        ids = [r["item"]["id"] for r in resp.json()]
        assert new.id in ids
        assert old.id not in ids

    def test_feed_days_zero_returns_all(self, client, db_session):
        term = _make_term(db_session)
        old = _make_item(db_session, item_id="old", days_ago=365)
        _make_match(db_session, term, old)

        resp = client.get("/api/feed/?days=0")
        assert len(resp.json()) == 1

    def test_feed_timeless_platform_bypasses_days_filter(self, client, db_session):
        term = _make_term(db_session)
        old_togetter = _make_item(db_session, platform="togetter", item_id="t1", days_ago=200)
        _make_match(db_session, term, old_togetter)

        resp = client.get("/api/feed/?days=30")
        ids = [r["item"]["id"] for r in resp.json()]
        assert old_togetter.id in ids

    def test_feed_media_type_filter(self, client, db_session):
        term = _make_term(db_session)
        vid = _make_item(db_session, item_id="v1", media_type="video")
        art = _make_item(db_session, item_id="a1", media_type="article")
        _make_match(db_session, term, vid)
        _make_match(db_session, term, art)

        resp = client.get("/api/feed/?media_type=video")
        ids = [r["item"]["id"] for r in resp.json()]
        assert vid.id in ids
        assert art.id not in ids

    def test_feed_published_at_has_timezone(self, client, db_session):
        term = _make_term(db_session)
        item = _make_item(db_session)
        _make_match(db_session, term, item)

        row = client.get("/api/feed/").json()[0]
        ts = row["item"]["published_at"]
        assert ts.endswith("Z") or "+" in ts, f"published_at not UTC-aware: {ts!r}"

    def test_feed_matched_at_has_timezone(self, client, db_session):
        term = _make_term(db_session)
        item = _make_item(db_session)
        _make_match(db_session, term, item)

        row = client.get("/api/feed/").json()[0]
        ts = row["matched_at"]
        assert ts.endswith("Z") or "+" in ts, f"matched_at not UTC-aware: {ts!r}"

    def test_feed_limit_and_offset(self, client, db_session):
        term = _make_term(db_session)
        for i in range(5):
            item = _make_item(db_session, item_id=f"item{i}", days_ago=i)
            _make_match(db_session, term, item)

        page1 = client.get("/api/feed/?limit=3&offset=0").json()
        page2 = client.get("/api/feed/?limit=3&offset=3").json()
        assert len(page1) == 3
        assert len(page2) == 2
        ids1 = {r["item"]["id"] for r in page1}
        ids2 = {r["item"]["id"] for r in page2}
        assert ids1.isdisjoint(ids2)
