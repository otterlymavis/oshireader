from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from app.config import settings


REDIRECT_TTL = timedelta(days=30)


def _signature(match_id: int, expires: int) -> str:
    message = f"{match_id}:{expires}".encode("utf-8")
    return hmac.new(settings.admin_api_token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def signed_match_redirect_url(
    public_base_url: str,
    match_id: int,
    source_url: str,
    *,
    issued_at: datetime,
) -> str:
    """Return a non-enumerable backend link, or the public source as a safe fallback."""
    if not settings.admin_api_token:
        return source_url
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    expires = int((issued_at + REDIRECT_TTL).timestamp())
    query = urlencode({"expires": expires, "signature": _signature(match_id, expires)})
    return f"{public_base_url.rstrip('/')}/api/feed/matches/{match_id}/redirect?{query}"


def match_redirect_signature_is_valid(match_id: int, expires: int, signature: str) -> bool:
    if not settings.admin_api_token or expires < int(datetime.now(timezone.utc).timestamp()):
        return False
    return hmac.compare_digest(signature, _signature(match_id, expires))
