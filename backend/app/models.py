from datetime import datetime, timezone
from enum import Enum
from typing import Optional

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
    keyword = Column(String, nullable=False, index=True)
    aliases = Column(JSON, default=list)
    language_hint = Column(String)
    collection_mode = Column(String, default="all_info")  # all_info | media_only
    source_mode = Column(String, nullable=False, default="all")  # all | selected
    selected_platforms = Column(JSON, default=list)
    is_active = Column(Boolean, default=True)
    notify_on_new = Column(Boolean, default=False)
    refresh_tier = Column(String, nullable=False, default="free", index=True)
    last_polled_at = Column(DateTime, index=True)
    owner_device_secret = Column(String, index=True)
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

    @property
    def source(self) -> Optional[str]:
        if not isinstance(self.raw_payload, dict):
            return None
        source = self.raw_payload.get("source")
        if isinstance(source, str):
            return source
        if self.platform == "youtube" and isinstance(self.raw_payload.get("snippet"), dict):
            return "youtube_api"
        return None


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
    apns_topic = Column(String, nullable=False, default="com.otterpia.oshireader.plus", index=True)
    device_id = Column(String, index=True)
    device_secret = Column(String, index=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    verified_at = Column(DateTime)
    verification_attempted_at = Column(DateTime)
    last_seen_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    watch_term_id = Column(Integer, ForeignKey("watch_terms.id", ondelete="CASCADE"), nullable=False, index=True)
    source_item_id = Column(String, ForeignKey("source_items.id"), nullable=False, index=True)
    confidence = Column(Float, default=1.0)
    created_at = Column(DateTime, default=_utcnow, index=True)

    __table_args__ = (UniqueConstraint("watch_term_id", "source_item_id"),)


class MutedFeedItem(Base):
    __tablename__ = "muted_feed_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    watch_term_id = Column(Integer, ForeignKey("watch_terms.id", ondelete="CASCADE"), nullable=False, index=True)
    source_item_id = Column(String, ForeignKey("source_items.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("watch_term_id", "source_item_id"),)


class PendingNotification(Base):
    __tablename__ = "pending_notifications"

    watch_term_id = Column(
        Integer,
        ForeignKey("watch_terms.id", ondelete="CASCADE"),
        primary_key=True,
    )
    new_count = Column(Integer, nullable=False, default=0)
    preview_item = Column(JSON)
    preview_published_at = Column(DateTime)
    preview_is_estimated = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class BackendEvent(Base):
    __tablename__ = "backend_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, index=True)
    message = Column(Text)
    payload = Column(JSON)
    created_at = Column(DateTime, default=_utcnow, index=True)


class DeviceEntitlement(Base):
    """StoreKit Plus subscription state for a device, keyed by owner_device_secret.

    One row per device — a subscription is account-wide, not per watch term.
    Written only by the transaction-verification endpoint after validating a
    StoreKit 2 signed transaction against Apple's certificate chain; never
    trust a client-supplied tier directly (see app/api/watch_terms.py).
    """
    __tablename__ = "device_entitlements"

    owner_device_secret = Column(String, primary_key=True)
    product_id = Column(String, nullable=False)
    environment = Column(String, nullable=False, default="production")  # sandbox | production
    original_transaction_id = Column(String, nullable=False, index=True)
    latest_transaction_id = Column(String, nullable=False)
    purchase_date = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, index=True)
    revoked_at = Column(DateTime)
    push_term_limit = Column(Integer, nullable=False, default=0)
    # A non-consumable can coexist with a higher-limit subscription. Keep its
    # latest verified transaction so it becomes the effective entitlement when
    # the subscription expires or is revoked.
    permanent_product_id = Column(String)
    permanent_environment = Column(String)
    permanent_original_transaction_id = Column(String, index=True)
    permanent_latest_transaction_id = Column(String)
    permanent_purchase_date = Column(DateTime)
    permanent_revoked_at = Column(DateTime)
    permanent_push_term_limit = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    @property
    def primary_is_active(self) -> bool:
        # When both slots describe the same non-consumable transaction, the
        # permanent slot is authoritative (including a later refund).
        if (
            self.permanent_original_transaction_id is not None
            and self.original_transaction_id == self.permanent_original_transaction_id
        ):
            return False
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > _utcnow()

    @property
    def permanent_is_active(self) -> bool:
        return (
            self.permanent_original_transaction_id is not None
            and self.permanent_revoked_at is None
        )

    @property
    def uses_permanent_entitlement(self) -> bool:
        if not self.permanent_is_active:
            return False
        if not self.primary_is_active:
            return True
        return self.permanent_push_term_limit > self.push_term_limit

    @property
    def is_active(self) -> bool:
        return self.primary_is_active or self.permanent_is_active

    @property
    def effective_product_id(self) -> str:
        if self.uses_permanent_entitlement and self.permanent_product_id is not None:
            return self.permanent_product_id
        return self.product_id

    @property
    def effective_expires_at(self) -> Optional[datetime]:
        return None if self.uses_permanent_entitlement else self.expires_at

    @property
    def effective_push_term_limit(self) -> int:
        if not self.is_active:
            return 0
        if self.uses_permanent_entitlement:
            return max(0, self.permanent_push_term_limit)
        return max(0, self.push_term_limit)


class MigrationLog(Base):
    """Tracks one-time migrations so they never re-run on subsequent boots."""
    __tablename__ = "migration_log"
    id = Column(String, primary_key=True)  # migration name / slug
    applied_at = Column(DateTime, default=_utcnow)
