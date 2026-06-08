from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint

from app.database import Base


class CollectionMode(str, Enum):
    ALL_INFO = "all_info"
    MEDIA_ONLY = "media_only"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WatchTerm(Base):
    __tablename__ = "watch_terms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String, nullable=False, unique=True)
    aliases = Column(JSON, default=list)
    language_hint = Column(String)
    collection_mode = Column(String, default="all_info")  # all_info | media_only
    is_active = Column(Boolean, default=True)
    notify_on_new = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)


class SourceItem(Base):
    __tablename__ = "source_items"

    id = Column(String, primary_key=True)  # "{platform}:{item_id}"
    platform = Column(String, nullable=False, index=True)
    item_id = Column(String, nullable=False)
    url = Column(String, nullable=False)
    published_at = Column(DateTime, nullable=False, index=True)
    author = Column(String)
    title = Column(String)
    content_text = Column(Text)
    media_type = Column(String, index=True)  # video | image | text | article
    thumbnail_url = Column(String)
    raw_payload = Column(JSON)
    fetched_at = Column(DateTime, default=_utcnow)


class PlatformCredential(Base):
    __tablename__ = "platform_credentials"

    platform = Column(String, primary_key=True)
    bearer_token = Column(String)
    api_key = Column(String)
    api_secret = Column(String)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class APNSDeviceToken(Base):
    __tablename__ = "apns_device_tokens"

    token = Column(String, primary_key=True)
    environment = Column(String, default="sandbox", index=True)
    device_id = Column(String, index=True)
    last_seen_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    watch_term_id = Column(Integer, ForeignKey("watch_terms.id", ondelete="CASCADE"), nullable=False)
    source_item_id = Column(String, ForeignKey("source_items.id"), nullable=False, index=True)
    confidence = Column(Float, default=1.0)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("watch_term_id", "source_item_id"),)


class MigrationLog(Base):
    """Tracks one-time migrations so they never re-run on subsequent boots."""
    __tablename__ = "migration_log"
    id = Column(String, primary_key=True)  # migration name / slug
    applied_at = Column(DateTime, default=_utcnow)
