"""Shared fixtures for backend tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from app.database import Base, get_db
from app.main import app
from app.config import settings

# StaticPool forces all checkouts to share one connection — required for
# SQLite in-memory so that tables created by create_all remain visible
# to sessions opened later from the same engine.
_TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture()
def db_engine():
    engine = create_engine(
        _TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    """TestClient with the DB dependency overridden to use the in-memory session."""
    def _override_db():
        try:
            yield db_session
        finally:
            pass

    original_allow_unauthenticated = settings.allow_unauthenticated_admin
    original_admin_api_token = settings.admin_api_token
    settings.allow_unauthenticated_admin = True
    settings.admin_api_token = ""
    app.dependency_overrides[get_db] = _override_db
    try:
        # Startup maintenance uses the app's default SessionLocal; API tests
        # use an overridden session and keep startup side effects deterministic.
        with patch("app.main._mark_abandoned_poll_events"), TestClient(
            app,
            raise_server_exceptions=False,
        ) as c:
            yield c
    finally:
        settings.allow_unauthenticated_admin = original_allow_unauthenticated
        settings.admin_api_token = original_admin_api_token
        app.dependency_overrides.clear()
