from __future__ import annotations

import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import APNSDeviceToken, WatchTerm

log = logging.getLogger(__name__)


def _private_key() -> str:
    if settings.apns_private_key:
        return settings.apns_private_key.replace("\\n", "\n")
    if settings.apns_private_key_path:
        return Path(settings.apns_private_key_path).read_text()
    return ""


def apns_configured() -> bool:
    return bool(settings.apns_team_id and settings.apns_key_id and _private_key() and settings.apns_topic)


@lru_cache(maxsize=1)
def _auth_token() -> tuple[str, int]:
    import jwt

    issued_at = int(time.time())
    token = jwt.encode(
        {"iss": settings.apns_team_id, "iat": issued_at},
        _private_key(),
        algorithm="ES256",
        headers={"alg": "ES256", "kid": settings.apns_key_id},
    )
    return token, issued_at


def _cached_auth_token() -> str:
    token, issued_at = _auth_token()
    if int(time.time()) - issued_at > 50 * 60:
        _auth_token.cache_clear()
        token, _ = _auth_token()
    return token


def _host() -> str:
    if settings.apns_use_sandbox:
        return "https://api.sandbox.push.apple.com"
    return "https://api.push.apple.com"


def _payload(term: WatchTerm, count: int) -> dict:
    return {
        "aps": {
            "alert": {
                "title": f"New items for {term.keyword}",
                "body": f"{count} new item{'' if count == 1 else 's'} found.",
            },
            "sound": "default",
        },
        "watch_term_id": term.id,
        "watch_term_keyword": term.keyword,
        "new_count": count,
    }


async def _send_one(client: httpx.AsyncClient, device: APNSDeviceToken, term: WatchTerm, count: int) -> bool:
    url = f"{_host()}/3/device/{device.token}"
    headers = {
        "authorization": f"bearer {_cached_auth_token()}",
        "apns-topic": settings.apns_topic,
        "apns-push-type": "alert",
        "apns-priority": "10",
    }
    try:
        resp = await client.post(url, json=_payload(term, count), headers=headers)
    except Exception as exc:
        log.warning("APNs send failed token=%s: %s", device.token[-8:], exc)
        return False

    if resp.status_code in {200, 201}:
        return True

    reason = None
    try:
        reason = resp.json().get("reason")
    except Exception:
        reason = resp.text
    log.warning("APNs rejected token=%s status=%d reason=%s", device.token[-8:], resp.status_code, reason)
    return reason in {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"} or resp.status_code == 410


async def send_new_match_notifications(db: Session, term: WatchTerm, count: int) -> None:
    if count <= 0 or not term.notify_on_new:
        return
    if not apns_configured():
        log.info("APNs not configured; skipping remote notification term=%r count=%d", term.keyword, count)
        return

    environment = "sandbox" if settings.apns_use_sandbox else "production"
    devices: Iterable[APNSDeviceToken] = (
        db.query(APNSDeviceToken)
        .filter(APNSDeviceToken.environment == environment)
        .all()
    )
    async with httpx.AsyncClient(timeout=10.0, http2=True) as client:
        for device in devices:
            should_delete = await _send_one(client, device, term, count)
            if should_delete:
                db.delete(device)
    db.commit()
