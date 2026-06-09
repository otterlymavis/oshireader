from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import get_db
from app.models import Match, SourceItem, WatchTerm


class TestGetDb:
    def test_yields_session_and_closes(self):
        gen = get_db()
        session = next(gen)
        assert session is not None
        try:
            next(gen)
        except StopIteration:
            pass  # generator closed normally after yielding


class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestAdminStats:
    def test_stats_empty_db(self, client):
        r = client.get("/api/admin/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["items_total"] == 0
        assert data["matches_total"] == 0
        assert data["watch_terms"] == []
        assert data["items_by_platform"] == {}

    def test_stats_counts_reflect_db_content(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.flush()

        item = SourceItem(
            id="youtube:abc",
            platform="youtube",
            item_id="abc",
            url="https://youtube.com/watch?v=abc",
            published_at=datetime.now(timezone.utc),
        )
        db_session.add(item)
        db_session.flush()

        match = Match(watch_term_id=term.id, source_item_id=item.id)
        db_session.add(match)
        db_session.commit()

        r = client.get("/api/admin/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["items_total"] == 1
        assert data["matches_total"] == 1
        assert len(data["watch_terms"]) == 1
        assert data["watch_terms"][0]["keyword"] == "Aiko"
        assert data["items_by_platform"] == {"youtube": 1}

    def test_stats_groups_items_by_platform(self, client, db_session):
        now = datetime.now(timezone.utc)
        for i, platform in enumerate(["youtube", "youtube", "twitter"]):
            db_session.add(SourceItem(
                id=f"{platform}:{i}",
                platform=platform,
                item_id=str(i),
                url=f"https://example.com/{i}",
                published_at=now,
            ))
        db_session.commit()

        r = client.get("/api/admin/stats")
        data = r.json()
        assert data["items_by_platform"]["youtube"] == 2
        assert data["items_by_platform"]["twitter"] == 1


class TestAdminPoll:
    def test_poll_returns_started_when_not_running(self, client):
        with patch("app.main.queue_poll", return_value=True) as mock_poll:
            r = client.post("/api/admin/poll")
        assert r.status_code == 200
        assert r.json() == {"status": "poll started"}
        mock_poll.assert_called_once()

    def test_poll_returns_already_running_when_busy(self, client):
        with patch("app.main.queue_poll", return_value=False):
            r = client.post("/api/admin/poll")
        assert r.status_code == 200
        assert r.json() == {"status": "poll already running"}


class TestAdminAuth:
    def test_stats_requires_auth_when_token_set(self, client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.admin_api_token = "secret123"
            r = client.get("/api/admin/stats")
        assert r.status_code == 401

    def test_stats_accepts_correct_token(self, client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.admin_api_token = "secret123"
            r = client.get(
                "/api/admin/stats",
                headers={"Authorization": "Bearer secret123"},
            )
        assert r.status_code == 200

    def test_stats_rejects_wrong_token(self, client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.admin_api_token = "secret123"
            r = client.get(
                "/api/admin/stats",
                headers={"Authorization": "Bearer wrongtoken"},
            )
        assert r.status_code == 401

    def test_poll_requires_auth_when_token_set(self, client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.admin_api_token = "secret123"
            r = client.post("/api/admin/poll")
        assert r.status_code == 401

    def test_poll_accepts_correct_token(self, client):
        with patch("app.auth.settings") as mock_settings, \
             patch("app.main.queue_poll", return_value=True):
            mock_settings.admin_api_token = "secret123"
            r = client.post(
                "/api/admin/poll",
                headers={"Authorization": "Bearer secret123"},
            )
        assert r.status_code == 200


class TestAdminTestFetch:
    def test_test_fetch_returns_platform_counts(self, client):
        from app.connectors.base import SourceItemCreate

        mock_item = SourceItemCreate(
            platform="youtube", item_id="v1",
            url="https://yt.be/v1",
            published_at=datetime.now(timezone.utc),
            media_type="video",
        )
        mock_connector = MagicMock()
        mock_connector.PLATFORM = "youtube"

        with patch("app.ingestion.scheduler._build_connectors", return_value=[mock_connector]), \
             patch("app.ingestion.scheduler._fetch_one", new=AsyncMock(return_value=[mock_item])):
            r = client.get("/api/admin/test-fetch")

        assert r.status_code == 200
        data = r.json()
        assert "youtube" in data
        assert data["youtube"] == 1

    def test_test_fetch_requires_auth_when_token_set(self, client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.admin_api_token = "secret"
            r = client.get("/api/admin/test-fetch")
        assert r.status_code == 401

    def test_test_fetch_empty_connectors_returns_empty_dict(self, client):
        with patch("app.ingestion.scheduler._build_connectors", return_value=[]), \
             patch("app.ingestion.scheduler._fetch_one", new=AsyncMock(return_value=[])):
            r = client.get("/api/admin/test-fetch")
        assert r.status_code == 200
        assert r.json() == {}
