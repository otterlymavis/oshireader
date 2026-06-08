"""Tests for the /api/watch-terms CRUD endpoints."""
from __future__ import annotations

import pytest
from unittest.mock import patch

from app.models import WatchTerm


class TestListWatchTerms:
    def test_empty_returns_empty_list(self, client):
        resp = client.get("/api/watch-terms/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_terms(self, client, db_session):
        db_session.add_all([WatchTerm(keyword="Aiko"), WatchTerm(keyword="Haruka")])
        db_session.commit()

        resp = client.get("/api/watch-terms/")
        assert resp.status_code == 200
        keywords = {t["keyword"] for t in resp.json()}
        assert keywords == {"Aiko", "Haruka"}

    def test_returns_term_fields(self, client, db_session):
        db_session.add(WatchTerm(keyword="Miku", aliases=["初音ミク"], notify_on_new=True))
        db_session.commit()

        terms = client.get("/api/watch-terms/").json()
        assert len(terms) == 1
        t = terms[0]
        assert t["keyword"] == "Miku"
        assert "初音ミク" in t["aliases"]
        assert t["notify_on_new"] is True
        assert t["is_active"] is True


class TestCreateWatchTerm:
    def test_creates_term_returns_201(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            resp = client.post("/api/watch-terms/", json={"keyword": "Aiko"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["keyword"] == "Aiko"
        assert body["id"] is not None

    def test_creates_term_with_aliases(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            resp = client.post(
                "/api/watch-terms/",
                json={"keyword": "Miku", "aliases": ["初音ミク", "Hatsune Miku"]},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert "初音ミク" in body["aliases"]

    def test_keyword_too_long_returns_422(self, client):
        resp = client.post("/api/watch-terms/", json={"keyword": "x" * 201})
        assert resp.status_code == 422

    def test_blank_keyword_returns_422(self, client):
        resp = client.post("/api/watch-terms/", json={"keyword": "   "})
        assert resp.status_code == 422

    def test_too_many_aliases_returns_422(self, client):
        aliases = [f"alias{i}" for i in range(21)]
        resp = client.post("/api/watch-terms/", json={"keyword": "Aiko", "aliases": aliases})
        assert resp.status_code == 422

    def test_triggers_poll_on_create(self, client):
        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.post("/api/watch-terms/", json={"keyword": "Aiko"})
        mock_poll.assert_called_once()

    def test_duplicate_keyword_returns_409(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            client.post("/api/watch-terms/", json={"keyword": "Aiko"})
            resp = client.post("/api/watch-terms/", json={"keyword": "Aiko"})
        assert resp.status_code == 409


class TestUpdateWatchTerm:
    def test_update_is_active(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"is_active": False})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_update_notify_on_new(self, client, db_session):
        term = WatchTerm(keyword="Aiko", notify_on_new=False)
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"notify_on_new": True})
        assert resp.status_code == 200
        assert resp.json()["notify_on_new"] is True

    def test_update_aliases_triggers_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko", aliases=[])
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.patch(f"/api/watch-terms/{term.id}", json={"aliases": ["愛子"]})
        mock_poll.assert_called_once()

    def test_update_without_aliases_does_not_trigger_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.patch(f"/api/watch-terms/{term.id}", json={"is_active": False})
        mock_poll.assert_not_called()

    def test_update_collection_mode_triggers_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko", collection_mode="all_info")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"collection_mode": "media_only"})
        assert resp.status_code == 200
        assert resp.json()["collection_mode"] == "media_only"
        mock_poll.assert_called_once()

    def test_update_collection_mode(self, client, db_session):
        term = WatchTerm(keyword="Aiko", collection_mode="all_info")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"collection_mode": "media_only"})
        assert resp.status_code == 200
        assert resp.json()["collection_mode"] == "media_only"

    def test_reactivating_term_triggers_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=False)
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.patch(f"/api/watch-terms/{term.id}", json={"is_active": True})
        mock_poll.assert_called_once()

    def test_deactivating_term_does_not_trigger_poll(self, client, db_session):
        term = WatchTerm(keyword="Aiko", is_active=True)
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll") as mock_poll:
            client.patch(f"/api/watch-terms/{term.id}", json={"is_active": False})
        mock_poll.assert_not_called()

    def test_update_invalid_collection_mode_returns_422(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch(f"/api/watch-terms/{term.id}", json={"collection_mode": "unknown_mode"})
        assert resp.status_code == 422

    def test_update_nonexistent_returns_404(self, client):
        with patch("app.api.watch_terms.queue_poll"):
            resp = client.patch("/api/watch-terms/999999", json={"is_active": False})
        assert resp.status_code == 404


class TestDeleteWatchTerm:
    def test_delete_existing_returns_204(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        resp = client.delete(f"/api/watch-terms/{term.id}")
        assert resp.status_code == 204

    def test_delete_removes_from_list(self, client, db_session):
        term = WatchTerm(keyword="Aiko")
        db_session.add(term)
        db_session.commit()

        client.delete(f"/api/watch-terms/{term.id}")

        resp = client.get("/api/watch-terms/")
        assert resp.json() == []

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/watch-terms/999999")
        assert resp.status_code == 404
