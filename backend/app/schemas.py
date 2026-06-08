from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.models import CollectionMode


def _clean_keyword(v: str) -> str:
    stripped = v.strip()
    if not stripped:
        raise ValueError("keyword must not be blank")
    return stripped


class WatchTermCreate(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default=[], max_length=20)
    language_hint: Optional[str] = Field(default=None, max_length=50)
    collection_mode: CollectionMode = CollectionMode.ALL_INFO
    notify_on_new: bool = False

    @field_validator("keyword", mode="before")
    @classmethod
    def strip_keyword(cls, v: str) -> str:
        return _clean_keyword(v)

    @field_validator("aliases", mode="before")
    @classmethod
    def clean_aliases(cls, v: list) -> list:
        cleaned = [a.strip() for a in v if isinstance(a, str) and a.strip()]
        if len(cleaned) > 20:
            raise ValueError("aliases must not exceed 20 entries")
        for alias in cleaned:
            if len(alias) > 200:
                raise ValueError("each alias must be 200 characters or fewer")
        return cleaned


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
        cleaned = [a.strip() for a in v if isinstance(a, str) and a.strip()]
        if len(cleaned) > 20:
            raise ValueError("aliases must not exceed 20 entries")
        for alias in cleaned:
            if len(alias) > 200:
                raise ValueError("each alias must be 200 characters or fewer")
        return cleaned


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
