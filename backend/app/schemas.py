from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class WatchTermCreate(BaseModel):
    keyword: str
    aliases: list[str] = []
    language_hint: Optional[str] = None
    collection_mode: Literal["all_info", "media_only"] = "all_info"
    notify_on_new: bool = False


class WatchTermUpdate(BaseModel):
    keyword: Optional[str] = None
    aliases: Optional[list[str]] = None
    language_hint: Optional[str] = None
    collection_mode: Optional[Literal["all_info", "media_only"]] = None
    is_active: Optional[bool] = None
    notify_on_new: Optional[bool] = None


def _utc(v: datetime) -> datetime:
    return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)


def _utc_opt(v: Optional[datetime]) -> Optional[datetime]:
    return None if v is None else _utc(v)


class WatchTermOut(BaseModel):
    id: int
    keyword: str
    aliases: list[str]
    language_hint: Optional[str]
    collection_mode: str
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
    bearer_token: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


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
