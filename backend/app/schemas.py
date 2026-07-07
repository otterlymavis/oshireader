from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.models import CollectionMode


def _clean_nonblank_string(v: object, field_name: str) -> str:
    if not isinstance(v, str):
        raise ValueError(f"{field_name} must be a string")
    stripped = v.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be blank")
    return stripped


def _clean_keyword(v: object) -> str:
    return _clean_nonblank_string(v, "keyword")


def _clean_aliases_list(v: list) -> list:
    cleaned = [a.strip() for a in v if isinstance(a, str) and a.strip()]
    if len(cleaned) > 20:
        raise ValueError("aliases must not exceed 20 entries")
    for alias in cleaned:
        if len(alias) > 200:
            raise ValueError("each alias must be 200 characters or fewer")
    return cleaned


class WatchTermCreate(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default=[], max_length=20)
    language_hint: Optional[str] = Field(default=None, max_length=50)
    collection_mode: CollectionMode = CollectionMode.ALL_INFO
    is_active: bool = True
    notify_on_new: bool = True

    @field_validator("keyword", mode="before")
    @classmethod
    def strip_keyword(cls, v: str) -> str:
        return _clean_keyword(v)

    @field_validator("aliases", mode="before")
    @classmethod
    def clean_aliases(cls, v: list) -> list:
        return _clean_aliases_list(v)


class WatchTermUpdate(BaseModel):
    keyword: Optional[str] = Field(default=None, min_length=1, max_length=200)
    aliases: Optional[list[str]] = Field(default=None, max_length=20)
    language_hint: Optional[str] = Field(default=None, max_length=50)
    collection_mode: Optional[CollectionMode] = None
    is_active: Optional[bool] = None
    notify_on_new: Optional[bool] = None

    @field_validator("keyword", mode="before")
    @classmethod
    def strip_keyword(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return _clean_keyword(v)

    @field_validator("aliases", mode="before")
    @classmethod
    def clean_aliases(cls, v: Optional[list]) -> Optional[list]:
        if v is None:
            return None
        return _clean_aliases_list(v)


def _utc(v: datetime) -> datetime:
    return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)


def _utc_opt(v: Optional[datetime]) -> Optional[datetime]:
    return None if v is None else _utc(v)


class WatchTermOut(BaseModel):
    id: int
    keyword: str
    aliases: list[str]
    language_hint: Optional[str]
    collection_mode: CollectionMode
    is_active: bool
    notify_on_new: bool
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("created_at", mode="before")
    @classmethod
    def stamp_utc(cls, v: datetime) -> datetime:
        return _utc(v)


class SourceItemOut(BaseModel):
    id: str
    platform: str
    url: str
    published_at: datetime
    author: Optional[str]
    title: Optional[str]
    content_text: Optional[str]
    media_type: Optional[str]
    thumbnail_url: Optional[str]

    model_config = {"from_attributes": True}

    @field_validator("published_at", mode="before")
    @classmethod
    def stamp_utc(cls, v: datetime) -> datetime:
        return _utc(v)


class CredentialUpsert(BaseModel):
    bearer_token: Optional[str] = Field(default=None, max_length=500)
    api_key: Optional[str] = Field(default=None, max_length=500)
    api_secret: Optional[str] = Field(default=None, max_length=500)


class CredentialOut(BaseModel):
    platform: str
    has_bearer_token: bool
    has_api_key: bool
    has_api_secret: bool
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True}

    @field_validator("updated_at", mode="before")
    @classmethod
    def stamp_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _utc_opt(v)


class APNSDeviceTokenUpsert(BaseModel):
    token: str
    environment: Literal["sandbox", "production"] = "sandbox"
    device_id: Optional[str] = None
    device_secret: str = Field(min_length=16, max_length=200)


class APNSDeviceTokenOut(BaseModel):
    token: str
    environment: str
    device_id: Optional[str]
    last_seen_at: Optional[datetime]

    model_config = {"from_attributes": True}

    @field_validator("last_seen_at", mode="before")
    @classmethod
    def stamp_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _utc_opt(v)


class APNSDeviceTestPush(BaseModel):
    token: Optional[str] = None
    device_id: Optional[str] = None
    environment: Literal["sandbox", "production"] = "sandbox"
    device_secret: str = Field(min_length=16, max_length=200)
    delivery_delay_seconds: float = Field(default=0, ge=0, le=10)
    return_before_delivery: bool = False


class ClientDiagnosticEvent(BaseModel):
    strategy: str = Field(max_length=80)
    status: str = Field(max_length=40)
    item_count: int = Field(default=0, ge=0, le=10000)
    added_count: int = Field(default=0, ge=0, le=10000)
    detail: Optional[str] = Field(default=None, max_length=500)


class ClientDiagnosticIn(BaseModel):
    reason: str = Field(max_length=120)
    environment: str = Field(max_length=80)
    api_base: str = Field(max_length=300)
    app_version: Optional[str] = Field(default=None, max_length=80)
    build: Optional[str] = Field(default=None, max_length=80)
    active_terms_count: int = Field(default=0, ge=0, le=1000)
    subscribed_platforms: list[str] = Field(default=[], max_length=80)
    cached_feed_count: int = Field(default=0, ge=0, le=10000)
    events: list[ClientDiagnosticEvent] = Field(default=[], max_length=80)


class FeedItemOut(BaseModel):
    match_id: int
    watch_term_id: int
    watch_term_keyword: str
    item: SourceItemOut
    matched_at: datetime

    @field_validator("matched_at", mode="before")
    @classmethod
    def stamp_utc(cls, v: datetime) -> datetime:
        return _utc(v)


class FeedItemMuteIn(BaseModel):
    source_item_id: str = Field(min_length=1, max_length=500)
    watch_term_id: int = Field(ge=1)

    @field_validator("source_item_id", mode="before")
    @classmethod
    def strip_nonblank(cls, v: object, info) -> str:
        return _clean_nonblank_string(v, info.field_name)
