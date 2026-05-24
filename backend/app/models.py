from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint

from app.database import Base


class PushToken(Base):
    __tablename__ = "push_tokens"

    token = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WatchTerm(Base):
    __tablename__ = "watch_terms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String, nullable=False)
    aliases = Column(JSON, default=list)
    language_hint = Column(String)
    collection_mode = Column(String, default="all_info")  # all_info | media_only
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


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
    fetched_at = Column(DateTime, default=datetime.utcnow)


class PlatformCredential(Base):
    __tablename__ = "platform_credentials"

    platform = Column(String, primary_key=True)  # youtube | twitter | weibo | ...
    bearer_token = Column(String)
    api_key = Column(String)
    api_secret = Column(String)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    watch_term_id = Column(Integer, ForeignKey("watch_terms.id", ondelete="CASCADE"), nullable=False)
    source_item_id = Column(String, ForeignKey("source_items.id"), nullable=False)
    confidence = Column(Float, default=1.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("watch_term_id", "source_item_id"),)
