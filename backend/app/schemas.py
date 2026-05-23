from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class WatchTermCreate(BaseModel):
    keyword: str
    aliases: list[str] = []
    language_hint: str | None = None
    collection_mode: Literal["all_info", "media_only"] = "all_info"


class WatchTermUpdate(BaseModel):
    keyword: str | None = None
    aliases: list[str] | None = None
    language_hint: str | None = None
    collection_mode: Literal["all_info", "media_only"] | None = None
    is_active: bool | None = None


class WatchTermOut(BaseModel):
    id: int
    keyword: str
    aliases: list[str]
    language_hint: str | None
    collection_mode: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class SourceItemOut(BaseModel):
    id: str
    platform: str
    url: str
    published_at: datetime
    author: str | None
    title: str | None
    content_text: str | None
    media_type: str | None
    thumbnail_url: str | None

    model_config = {"from_attributes": True}


class CredentialUpsert(BaseModel):
    bearer_token: str | None = None
    api_key: str | None = None
    api_secret: str | None = None


class CredentialOut(BaseModel):
    platform: str
    has_bearer_token: bool
    has_api_key: bool
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class TwitterLoginRequest(BaseModel):
    username: str
    password: str
    email: str | None = None


class TwitterStatusOut(BaseModel):
    logged_in: bool
    username: str | None


class FeedItemOut(BaseModel):
    match_id: int
    watch_term_id: int
    watch_term_keyword: str
    item: SourceItemOut
    matched_at: datetime
