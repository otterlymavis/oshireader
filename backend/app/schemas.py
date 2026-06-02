from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class WatchTermCreate(BaseModel):
    keyword: str
    aliases: list[str] = []
    language_hint: Optional[str] = None
    collection_mode: Literal["all_info", "media_only"] = "all_info"


class WatchTermUpdate(BaseModel):
    keyword: Optional[str] = None
    aliases: Optional[list[str]] = None
    language_hint: Optional[str] = None
    collection_mode: Optional[Literal["all_info", "media_only"]] = None
    is_active: Optional[bool] = None


class WatchTermOut(BaseModel):
    id: int
    keyword: str
    aliases: list[str]
    language_hint: Optional[str]
    collection_mode: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


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




class FeedItemOut(BaseModel):
    match_id: int
    watch_term_id: int
    watch_term_keyword: str
    item: SourceItemOut
    matched_at: datetime
