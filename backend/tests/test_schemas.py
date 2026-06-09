"""Tests for schema UTC-stamping validators and CollectionMode enum validation."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import CollectionMode
from app.schemas import (
    APNSDeviceTokenOut,
    CredentialOut,
    FeedItemOut,
    SourceItemOut,
    WatchTermCreate,
    WatchTermOut,
    WatchTermUpdate,
)


def _naive(year=2024, month=1, day=1, hour=12):
    return datetime(year, month, day, hour, 0, 0)  # no tzinfo


def _aware(year=2024, month=1, day=1, hour=12):
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)


class TestSourceItemOut:
    def test_naive_published_at_becomes_utc(self):
        obj = SourceItemOut(
            id="news:1", platform="news", url="https://x.com",
            published_at=_naive(), author=None, title=None,
            content_text=None, media_type=None, thumbnail_url=None,
        )
        assert obj.published_at.tzinfo == timezone.utc

    def test_aware_published_at_unchanged(self):
        dt = _aware()
        obj = SourceItemOut(
            id="news:1", platform="news", url="https://x.com",
            published_at=dt, author=None, title=None,
            content_text=None, media_type=None, thumbnail_url=None,
        )
        assert obj.published_at == dt


class TestWatchTermOut:
    def test_naive_created_at_becomes_utc(self):
        obj = WatchTermOut(
            id=1, keyword="test", aliases=[], language_hint=None,
            collection_mode="all_info", is_active=True, notify_on_new=False,
            created_at=_naive(),
        )
        assert obj.created_at.tzinfo == timezone.utc

    def test_aware_created_at_unchanged(self):
        dt = _aware()
        obj = WatchTermOut(
            id=1, keyword="test", aliases=[], language_hint=None,
            collection_mode="all_info", is_active=True, notify_on_new=False,
            created_at=dt,
        )
        assert obj.created_at == dt


class TestCredentialOut:
    def test_naive_updated_at_becomes_utc(self):
        obj = CredentialOut(
            platform="youtube", has_bearer_token=False,
            has_api_key=True, updated_at=_naive(),
        )
        assert obj.updated_at.tzinfo == timezone.utc

    def test_none_updated_at_stays_none(self):
        obj = CredentialOut(
            platform="youtube", has_bearer_token=False,
            has_api_key=False, updated_at=None,
        )
        assert obj.updated_at is None


class TestAPNSDeviceTokenOut:
    def test_naive_last_seen_at_becomes_utc(self):
        obj = APNSDeviceTokenOut(
            token="a" * 64, environment="production",
            device_id=None, last_seen_at=_naive(),
        )
        assert obj.last_seen_at.tzinfo == timezone.utc

    def test_none_last_seen_at_stays_none(self):
        obj = APNSDeviceTokenOut(
            token="a" * 64, environment="production",
            device_id=None, last_seen_at=None,
        )
        assert obj.last_seen_at is None


class TestFeedItemOut:
    def _item(self):
        return SourceItemOut(
            id="news:1", platform="news", url="https://x.com",
            published_at=_aware(), author=None, title=None,
            content_text=None, media_type=None, thumbnail_url=None,
        )

    def test_naive_matched_at_becomes_utc(self):
        obj = FeedItemOut(
            match_id=1, watch_term_id=1, watch_term_keyword="k",
            item=self._item(), matched_at=_naive(),
        )
        assert obj.matched_at.tzinfo == timezone.utc

    def test_aware_matched_at_unchanged(self):
        dt = _aware()
        obj = FeedItemOut(
            match_id=1, watch_term_id=1, watch_term_keyword="k",
            item=self._item(), matched_at=dt,
        )
        assert obj.matched_at == dt


class TestCollectionModeValidation:
    def test_create_accepts_all_info_string(self):
        obj = WatchTermCreate(keyword="k", collection_mode="all_info")
        assert obj.collection_mode == CollectionMode.ALL_INFO

    def test_create_accepts_media_only_string(self):
        obj = WatchTermCreate(keyword="k", collection_mode="media_only")
        assert obj.collection_mode == CollectionMode.MEDIA_ONLY

    def test_create_rejects_unknown_mode(self):
        with pytest.raises(ValidationError):
            WatchTermCreate(keyword="k", collection_mode="unknown_mode")

    def test_create_default_is_all_info(self):
        obj = WatchTermCreate(keyword="k")
        assert obj.collection_mode == CollectionMode.ALL_INFO

    def test_update_accepts_none(self):
        obj = WatchTermUpdate(collection_mode=None)
        assert obj.collection_mode is None

    def test_update_accepts_media_only(self):
        obj = WatchTermUpdate(collection_mode="media_only")
        assert obj.collection_mode == CollectionMode.MEDIA_ONLY

    def test_collection_mode_equals_its_value_string(self):
        # str enum: CollectionMode.MEDIA_ONLY == "media_only" must be True
        assert CollectionMode.MEDIA_ONLY == "media_only"
        assert CollectionMode.ALL_INFO == "all_info"


class TestAliasValidation:
    def test_alias_too_long_raises(self):
        with pytest.raises(ValidationError):
            WatchTermCreate(keyword="k", aliases=["a" * 201])

    def test_alias_at_max_length_accepted(self):
        obj = WatchTermCreate(keyword="k", aliases=["a" * 200])
        assert len(obj.aliases[0]) == 200

    def test_blank_aliases_stripped(self):
        obj = WatchTermCreate(keyword="k", aliases=["valid", "  ", "", "  spaces  "])
        assert obj.aliases == ["valid", "spaces"]

    def test_too_many_aliases_raises(self):
        with pytest.raises(ValidationError):
            WatchTermCreate(keyword="k", aliases=["alias"] * 21)


class TestWatchTermUpdateValidation:
    """WatchTermUpdate validators handle None gracefully — all fields are optional."""

    def test_none_keyword_accepted(self):
        obj = WatchTermUpdate(keyword=None)
        assert obj.keyword is None

    def test_none_aliases_accepted(self):
        obj = WatchTermUpdate(aliases=None)
        assert obj.aliases is None

    def test_update_alias_too_long_raises(self):
        with pytest.raises(ValidationError):
            WatchTermUpdate(keyword="k", aliases=["a" * 201])

    def test_update_too_many_aliases_raises(self):
        with pytest.raises(ValidationError):
            WatchTermUpdate(keyword="k", aliases=["alias"] * 21)
