"""Tests for the /api/feed and /api/watch-terms endpoints."""
from __future__ import annotations

import os
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.config import settings
from app.models import APNSDeviceToken, Match, MutedFeedItem, SourceItem, WatchTerm

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


def _make_item(
    db,
    platform="news",
    item_id="item1",
    days_ago=1,
    media_type="article",
    title=None,
    content_text=None,
    author=None,
    raw_payload=None,
):
    published = datetime.now(timezone.utc) - timedelta(days=days_ago)
    item = SourceItem(
        id=f"{platform}:{item_id}",
        platform=platform,
        item_id=item_id,
        url=f"https://example.com/{item_id}",
        published_at=published,
        media_type=media_type,
        title=title,
        content_text=content_text,
        author=author,
        raw_payload=raw_payload,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _make_match(db, term, item):
    if not item.title and not item.content_text:
        item.title = f"{term.keyword} item"
        db.commit()
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

    def test_create_duplicate_keyword_returns_409(self, client):
        client.post("/api/watch-terms/", json={"keyword": "dup"}, headers=_AUTH)
        resp = client.post("/api/watch-terms/", json={"keyword": "dup"}, headers=_AUTH)
        assert resp.status_code == 409

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

    def test_delete_term_cascades_to_matches(self, client, db_session):
        """Deleting a WatchTerm must cascade-delete its Matches so the feed is empty."""
        term = _make_term(db_session, keyword="cascade-test")
        item = _make_item(db_session, item_id="c1")
        _make_match(db_session, term, item)

        assert len(client.get("/api/feed/").json()) == 1

        client.delete(f"/api/watch-terms/{term.id}", headers=_AUTH)

        # No matches should survive the cascade
        db_session.expire_all()
        remaining = db_session.query(Match).filter(Match.watch_term_id == term.id).count()
        assert remaining == 0
        # Feed should be empty
        assert client.get("/api/feed/").json() == []

    def test_update_missing_term_returns_404(self, client):
        resp = client.patch("/api/watch-terms/9999", json={"keyword": "x"}, headers=_AUTH)
        assert resp.status_code == 404

    def test_created_at_is_utc_aware(self, client):
        client.post("/api/watch-terms/", json={"keyword": "tz"}, headers=_AUTH)
        data = client.get("/api/watch-terms/").json()
        ts = data[0]["created_at"]
        assert ts.endswith("Z") or "+" in ts, f"created_at not UTC-aware: {ts!r}"

    def test_create_blank_keyword_returns_422(self, client):
        r = client.post("/api/watch-terms/", json={"keyword": "   "}, headers=_AUTH)
        assert r.status_code == 422

    def test_create_empty_keyword_returns_422(self, client):
        r = client.post("/api/watch-terms/", json={"keyword": ""}, headers=_AUTH)
        assert r.status_code == 422

    def test_create_keyword_too_long_returns_422(self, client):
        r = client.post("/api/watch-terms/", json={"keyword": "a" * 201}, headers=_AUTH)
        assert r.status_code == 422

    def test_create_strips_whitespace_from_keyword(self, client):
        r = client.post("/api/watch-terms/", json={"keyword": "  Aiko  "}, headers=_AUTH)
        assert r.status_code == 201
        assert r.json()["keyword"] == "Aiko"

    def test_create_too_many_aliases_returns_422(self, client):
        r = client.post(
            "/api/watch-terms/",
            json={"keyword": "test", "aliases": [f"alias{i}" for i in range(21)]},
            headers=_AUTH,
        )
        assert r.status_code == 422

    def test_create_filters_blank_aliases(self, client):
        r = client.post(
            "/api/watch-terms/",
            json={"keyword": "Miku", "aliases": ["  ", "Hatsune Miku", ""]},
            headers=_AUTH,
        )
        assert r.status_code == 201
        assert r.json()["aliases"] == ["Hatsune Miku"]


# ---------------------------------------------------------------------------
# /api/feed
# ---------------------------------------------------------------------------

class TestFeedQueryValidation:
    def test_limit_zero_returns_422(self, client):
        assert client.get("/api/feed/?limit=0").status_code == 422

    def test_limit_negative_returns_422(self, client):
        assert client.get("/api/feed/?limit=-1").status_code == 422

    def test_limit_over_200_returns_422(self, client):
        assert client.get("/api/feed/?limit=201").status_code == 422

    def test_offset_negative_returns_422(self, client):
        assert client.get("/api/feed/?offset=-1").status_code == 422

    def test_days_over_365_returns_422(self, client):
        assert client.get("/api/feed/?days=366").status_code == 422


class TestFeedAPI:
    def test_feed_empty(self, client):
        resp = client.get("/api/feed/")
        assert resp.status_code == 200
        assert resp.json() == []
        assert resp.headers["server-timing"].startswith("feed;dur=")

    def test_feed_compresses_large_response_when_client_supports_gzip(self, client, db_session):
        term = _make_term(db_session, keyword="Aiko")
        for index in range(20):
            item = _make_item(
                db_session,
                item_id=f"gzip-{index}",
                days_ago=index,
                title=f"Aiko item {index}",
                content_text="Aiko " + ("long feed preview " * 20),
            )
            _make_match(db_session, term, item)

        resp = client.get(
            "/api/feed/?limit=20",
            headers={"Accept-Encoding": "gzip"},
        )

        assert resp.status_code == 200
        assert resp.headers["content-encoding"] == "gzip"
        assert len(resp.json()) == 20

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

    def test_registered_device_feed_is_scoped_to_owned_terms(self, client, db_session):
        token = "a" * 64
        secret = "device-secret-value"
        other_secret = "other-device-secret-value"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        other_owner_secret = hashlib.sha256(other_secret.encode()).hexdigest()
        db_session.add_all([
            APNSDeviceToken(
                token=token,
                environment="sandbox",
                device_secret=owner_secret,
                is_verified=True,
            ),
            APNSDeviceToken(
                token="b" * 64,
                environment="sandbox",
                device_secret=other_owner_secret,
                is_verified=True,
            ),
        ])
        owned_term = _make_term(db_session, keyword="Owned", owner_device_secret=owner_secret)
        other_term = _make_term(db_session, keyword="Other", owner_device_secret=other_owner_secret)
        owned_item = _make_item(db_session, item_id="owned", title="Owned item")
        other_item = _make_item(db_session, item_id="other", title="Other item")
        _make_match(db_session, owned_term, owned_item)
        _make_match(db_session, other_term, other_item)

        with patch.object(settings, "admin_api_token", "admin-secret"):
            resp = client.get(
                "/api/feed/",
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["item"]["id"] == owned_item.id
        assert rows[0]["watch_term_keyword"] == "Owned"

    def test_mute_feed_item_removes_only_that_match_and_keeps_term(self, client, db_session):
        term = _make_term(db_session, keyword="Discussion")
        muted_item = _make_item(db_session, item_id="muted", title="Discussion noisy thread")
        kept_item = _make_item(db_session, item_id="kept", title="Discussion quiet thread")
        _make_match(db_session, term, muted_item)
        _make_match(db_session, term, kept_item)

        resp = client.post(
            "/api/feed/muted-items",
            json={
                "source_item_id": muted_item.id,
                "watch_term_id": term.id,
            },
            headers=_AUTH,
        )

        assert resp.status_code == 204
        db_session.expire_all()
        assert db_session.query(WatchTerm).filter_by(id=term.id).one().keyword == "Discussion"
        assert (
            db_session.query(Match)
            .filter_by(watch_term_id=term.id, source_item_id=muted_item.id)
            .count()
            == 0
        )
        assert (
            db_session.query(Match)
            .filter_by(watch_term_id=term.id, source_item_id=kept_item.id)
            .count()
            == 1
        )
        assert (
            db_session.query(MutedFeedItem)
            .filter_by(watch_term_id=term.id, source_item_id=muted_item.id)
            .count()
            == 1
        )

        rows = client.get("/api/feed/").json()
        assert [row["item"]["id"] for row in rows] == [kept_item.id]

    def test_mute_feed_item_rejects_non_string_payload(self, client):
        resp = client.post(
            "/api/feed/muted-items",
            json={
                "source_item_id": 123,
                "watch_term_id": 1,
            },
            headers=_AUTH,
        )

        assert resp.status_code == 422
        assert "source_item_id" in resp.text

    def test_registered_device_can_only_mute_owned_item(self, client, db_session):
        token = "a" * 64
        secret = "device-secret-value"
        other_secret = "other-device-secret-value"
        owner_secret = hashlib.sha256(secret.encode()).hexdigest()
        other_owner_secret = hashlib.sha256(other_secret.encode()).hexdigest()
        db_session.add_all([
            APNSDeviceToken(
                token=token,
                environment="sandbox",
                device_secret=owner_secret,
                is_verified=True,
            ),
            APNSDeviceToken(
                token="b" * 64,
                environment="sandbox",
                device_secret=other_owner_secret,
                is_verified=True,
            ),
        ])
        owned_term = _make_term(db_session, keyword="Owned", owner_device_secret=owner_secret)
        other_term = _make_term(db_session, keyword="Other", owner_device_secret=other_owner_secret)
        owned_item = _make_item(db_session, item_id="owned-mute", title="Owned item")
        other_item = _make_item(db_session, item_id="other-mute", title="Other item")
        _make_match(db_session, owned_term, owned_item)
        _make_match(db_session, other_term, other_item)

        with patch.object(settings, "admin_api_token", "admin-secret"):
            other_resp = client.post(
                "/api/feed/muted-items",
                json={
                    "source_item_id": other_item.id,
                    "watch_term_id": other_term.id,
                },
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )
            owned_resp = client.post(
                "/api/feed/muted-items",
                json={
                    "source_item_id": owned_item.id,
                    "watch_term_id": owned_term.id,
                },
                headers={"X-Device-Token": token, "X-Device-Secret": secret},
            )

        assert other_resp.status_code == 404
        assert owned_resp.status_code == 204
        assert db_session.query(MutedFeedItem).filter_by(source_item_id=other_item.id).count() == 0
        assert db_session.query(MutedFeedItem).filter_by(source_item_id=owned_item.id).count() == 1

    def test_mute_feed_item_targets_exact_term_id_for_duplicate_keywords(self, client, db_session):
        older = _make_term(db_session, keyword="Duplicate")
        newer = _make_term(db_session, keyword="Duplicate")
        older_item = _make_item(db_session, item_id="duplicate-older", title="Duplicate older")
        newer_item = _make_item(db_session, item_id="duplicate-newer", title="Duplicate newer")
        _make_match(db_session, older, older_item)
        _make_match(db_session, newer, newer_item)

        resp = client.post(
            "/api/feed/muted-items",
            json={
                "source_item_id": older_item.id,
                "watch_term_id": older.id,
            },
            headers=_AUTH,
        )

        assert resp.status_code == 204
        assert (
            db_session.query(MutedFeedItem)
            .filter_by(watch_term_id=older.id, source_item_id=older_item.id)
            .count()
            == 1
        )
        assert (
            db_session.query(MutedFeedItem)
            .filter_by(watch_term_id=newer.id, source_item_id=older_item.id)
            .count()
            == 0
        )
        assert (
            db_session.query(Match)
            .filter_by(watch_term_id=newer.id, source_item_id=newer_item.id)
            .count()
            == 1
        )

    def test_match_redirect_opens_source_url(self, client, db_session):
        term = _make_term(db_session)
        item = _make_item(db_session, item_id="redirect")
        match = _make_match(db_session, term, item)

        resp = client.get(f"/api/feed/matches/{match.id}/redirect", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == item.url

    def test_match_redirect_404_for_unknown_match(self, client):
        resp = client.get("/api/feed/matches/999999/redirect", follow_redirects=False)
        assert resp.status_code == 404

    def test_match_redirect_rejects_non_http_url(self, client, db_session):
        term = _make_term(db_session)
        item = _make_item(db_session, item_id="unsafe-redirect")
        item.url = "javascript:alert(1)"
        db_session.commit()
        match = _make_match(db_session, term, item)

        resp = client.get(f"/api/feed/matches/{match.id}/redirect", follow_redirects=False)
        assert resp.status_code == 404

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

    def test_feed_hides_youtube_google_news_fallback_items(self, client, db_session):
        term = _make_term(db_session, keyword="Aiko")
        google_news_youtube = _make_item(
            db_session,
            platform="youtube",
            item_id="gnews",
            media_type="video",
            title="Aiko old video resurfaced",
            raw_payload={"source": "google_news", "date_parsed": True},
        )
        direct_youtube = _make_item(
            db_session,
            platform="youtube",
            item_id="direct",
            media_type="video",
            title="Aiko real upload",
            raw_payload={"source": "youtube_scrape", "date_parsed": True},
        )
        _make_match(db_session, term, google_news_youtube)
        _make_match(db_session, term, direct_youtube)

        ids = [r["item"]["id"] for r in client.get("/api/feed/?days=0&platform=youtube").json()]
        assert direct_youtube.id in ids
        assert google_news_youtube.id not in ids
        direct = next(r for r in client.get("/api/feed/?days=0&platform=youtube").json() if r["item"]["id"] == direct_youtube.id)
        assert direct["item"]["source"] == "youtube_scrape"

    def test_timeless_platform_bypasses_days_only_when_filtered(self, client, db_session):
        # A 200-day-old non-5ch forum thread must NOT flood the unfiltered "all"
        # feed, but can remain reachable when the user opens that source's filter.
        term = _make_term(db_session)
        old_togetter = _make_item(db_session, platform="togetter", item_id="t1", days_ago=200)
        _make_match(db_session, term, old_togetter)

        all_ids = [r["item"]["id"] for r in client.get("/api/feed/?days=30").json()]
        assert old_togetter.id not in all_ids, "old forum item should be windowed out of 'all'"

        filtered_ids = [r["item"]["id"] for r in client.get("/api/feed/?days=30&platform=togetter").json()]
        assert old_togetter.id in filtered_ids, "forum filter should show all threads regardless of age"

    def test_5ch_platform_filter_bypasses_days_window(self, client, db_session):
        term = _make_term(db_session, keyword="Aiko")
        old_5ch = _make_item(db_session, platform="5ch", item_id="old", days_ago=180, title="Aiko old thread")
        fresh_5ch = _make_item(db_session, platform="5ch", item_id="fresh", days_ago=1, title="Aiko fresh thread")
        _make_match(db_session, term, old_5ch)
        _make_match(db_session, term, fresh_5ch)

        all_ids = [r["item"]["id"] for r in client.get("/api/feed/?days=30").json()]
        assert old_5ch.id not in all_ids

        filtered_ids = [r["item"]["id"] for r in client.get("/api/feed/?days=30&platform=5ch").json()]
        assert fresh_5ch.id in filtered_ids
        assert old_5ch.id in filtered_ids

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

    def test_feed_ordered_newest_first(self, client, db_session):
        term = _make_term(db_session)
        old = _make_item(db_session, item_id="old_ord", days_ago=5)
        new = _make_item(db_session, item_id="new_ord", days_ago=1)
        _make_match(db_session, term, old)
        _make_match(db_session, term, new)

        rows = client.get("/api/feed/?days=30").json()
        ids = [r["item"]["id"] for r in rows]
        assert ids.index(new.id) < ids.index(old.id), "newer item must appear before older item"

    def test_feed_platform_filter(self, client, db_session):
        term = _make_term(db_session)
        yt = _make_item(db_session, platform="youtube", item_id="y1")
        tw = _make_item(db_session, platform="twitter", item_id="t1")
        _make_match(db_session, term, yt)
        _make_match(db_session, term, tw)

        resp = client.get("/api/feed/?platform=youtube")
        ids = [r["item"]["id"] for r in resp.json()]
        assert yt.id in ids
        assert tw.id not in ids

    def test_since_overrides_days_filter(self, client, db_session):
        """When `since` is provided, `days` is ignored entirely."""
        from urllib.parse import quote

        term = _make_term(db_session)
        old_item = _make_item(db_session, item_id="old", days_ago=60)
        new_item = _make_item(db_session, item_id="new", days_ago=1)
        old_match = _make_match(db_session, term, old_item)
        _make_match(db_session, term, new_item)

        # Backdate old match so since filter would exclude it
        db_session.query(Match).filter(Match.id == old_match.id).update(
            {"created_at": datetime.now(timezone.utc) - timedelta(days=50)},
            synchronize_session=False,
        )
        db_session.commit()

        # days=0 would normally mean "no cutoff", but since takes precedence.
        # With since=yesterday, only the new match should appear.
        since_ts = quote((datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
        resp = client.get(f"/api/feed/?since={since_ts}&days=0")
        ids = [r["item"]["id"] for r in resp.json()]
        assert new_item.id in ids
        assert old_item.id not in ids

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

    def test_feed_hides_summary_only_article_match(self, client, db_session):
        term = _make_term(db_session, keyword="吉沢亮")
        item = _make_item(
            db_session,
            platform="realsound",
            item_id="summary-only",
            title="杉野遥亮、『世にも奇妙な物語』で初主演",
            content_text="吉沢亮の関連記事も紹介",
        )
        _make_match(db_session, term, item)

        assert client.get("/api/feed/?days=0").json() == []

    def test_feed_keeps_article_matching_alias(self, client, db_session):
        term = _make_term(db_session, keyword="Aiko")
        term.aliases = ["相川愛子"]
        db_session.commit()
        item = _make_item(
            db_session,
            platform="oricon",
            item_id="alias",
            title="相川愛子の最新インタビュー",
        )
        _make_match(db_session, term, item)

        rows = client.get("/api/feed/?days=0").json()
        assert [row["item"]["id"] for row in rows] == [item.id]

    def test_feed_keeps_video_matching_description(self, client, db_session):
        term = _make_term(db_session, keyword="Aiko")
        item = _make_item(
            db_session,
            platform="tver",
            item_id="description-match",
            media_type="video",
            title="Tonight's drama",
            content_text="Aiko appears as a guest",
        )
        _make_match(db_session, term, item)

        rows = client.get("/api/feed/?days=0").json()
        assert [row["item"]["id"] for row in rows] == [item.id]

    def test_feed_pagination_scans_past_irrelevant_rows(self, client, db_session):
        term = _make_term(db_session, keyword="Aiko")
        for i in range(4):
            stale = _make_item(
                db_session,
                platform="note",
                item_id=f"stale{i}",
                days_ago=i,
                title=f"Unrelated article {i}",
                content_text="Aiko appears only in the summary",
            )
            _make_match(db_session, term, stale)
        for i in range(3):
            relevant = _make_item(
                db_session,
                platform="note",
                item_id=f"relevant{i}",
                days_ago=10 + i,
                title=f"Aiko article {i}",
            )
            _make_match(db_session, term, relevant)

        rows = client.get("/api/feed/?limit=2&offset=0&days=0").json()
        assert len(rows) == 2
        assert all("relevant" in row["item"]["id"] for row in rows)

    def test_feed_since_filter_returns_only_new_matches(self, client, db_session):
        """The iOS app passes `since=<last_sync_time>` for incremental updates.
        Items matched before that timestamp must be excluded."""
        from urllib.parse import quote

        term = _make_term(db_session)
        old_item = _make_item(db_session, item_id="old_item", days_ago=5)
        new_item = _make_item(db_session, item_id="new_item", days_ago=1)

        old_match = _make_match(db_session, term, old_item)
        _make_match(db_session, term, new_item)

        # Backdate the old match's created_at so the since filter can exclude it.
        db_session.query(Match).filter(Match.id == old_match.id).update(
            {"created_at": datetime.now(timezone.utc) - timedelta(days=3)},
            synchronize_session=False,
        )
        db_session.commit()

        # since = 2 days ago — old match (3 days) excluded, new match included.
        # URL-encode "+" so the timezone offset "+00:00" is not decoded as a space.
        since_ts = quote((datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
        resp = client.get(f"/api/feed/?since={since_ts}")
        assert resp.status_code == 200
        ids = [r["item"]["id"] for r in resp.json()]
        assert new_item.id in ids
        assert old_item.id not in ids

    def test_since_filter_timeless_platforms_always_included(self, client, db_session):
        """Togetter/girlschannel items survive the since filter regardless of match age."""
        term = _make_term(db_session)
        old_togetter = _make_item(db_session, platform="togetter", item_id="tg1", days_ago=180)
        match = _make_match(db_session, term, old_togetter)

        # Backdate the match so the since filter would normally exclude it
        db_session.query(Match).filter(Match.id == match.id).update(
            {"created_at": datetime.now(timezone.utc) - timedelta(days=90)},
            synchronize_session=False,
        )
        db_session.commit()

        from urllib.parse import quote
        since_ts = quote((datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        resp = client.get(f"/api/feed/?since={since_ts}")
        assert resp.status_code == 200
        ids = [r["item"]["id"] for r in resp.json()]
        assert old_togetter.id in ids, "timeless platform item must bypass the since filter"

    def test_5ch_since_filter_bypasses_match_age(self, client, db_session):
        from urllib.parse import quote

        term = _make_term(db_session, keyword="Aiko")
        old_5ch = _make_item(db_session, platform="5ch", item_id="old-since", days_ago=1, title="Aiko old matched thread")
        match = _make_match(db_session, term, old_5ch)
        db_session.query(Match).filter(Match.id == match.id).update(
            {"created_at": datetime.now(timezone.utc) - timedelta(days=90)},
            synchronize_session=False,
        )
        db_session.commit()

        since_ts = quote((datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        resp = client.get(f"/api/feed/?since={since_ts}&platform=5ch")
        assert resp.status_code == 200
        ids = [r["item"]["id"] for r in resp.json()]
        assert old_5ch.id in ids
