from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.diagnostics import record_backend_event
from app.models import APNSDeviceToken, WatchTerm

log = logging.getLogger(__name__)

_PREVIEW_FIELD_LIMITS = {
    "match_id": 40,
    "platform": 80,
    "title": 180,
    "content_text": 320,
    "author": 120,
    "media_type": 40,
    "published_at": 80,
}
_APNS_PAYLOAD_SOFT_LIMIT_BYTES = 3500
_APNS_PAYLOAD_HARD_LIMIT_BYTES = 4096
_APNS_MAX_ATTEMPTS = 3
_APNS_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_APNS_EVENT_DEVICE_RESULT_LIMIT = 25
_DEVICE_REVALIDATION_INTERVAL = timedelta(hours=6)


@dataclass(frozen=True)
class APNSSendResult:
    delivered: bool = False
    should_delete_token: bool = False
    retryable_failure: bool = False
    notification_id: str | None = field(default=None, compare=False)


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


def _host(environment: str | None = None) -> str:
    # Route by the token's own environment so production (TestFlight/App Store) and
    # sandbox (Xcode debug) tokens each hit the correct APNs host. Falls back to the
    # global apns_use_sandbox setting only when a token has no environment recorded.
    env = environment or ("sandbox" if settings.apns_use_sandbox else "production")
    if env == "sandbox":
        return "https://api.sandbox.push.apple.com"
    return "https://api.push.apple.com"


def _preview_text(preview_item: dict | None) -> str | None:
    if not preview_item:
        return None
    value = preview_item.get("title") or preview_item.get("content_text") or preview_item.get("url")
    if not value:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text if len(text) <= 140 else f"{text[:137]}..."


def _payload_value(preview_item: dict, key: str) -> str | None:
    if key == "url":
        value = preview_item.get("redirect_url") or preview_item.get("url")
    else:
        value = preview_item.get(key)
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    limit = _PREVIEW_FIELD_LIMITS.get(key)
    if limit and len(text) > limit:
        return f"{text[:limit - 3]}..."
    return text


def _payload_size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _drop_path(payload: dict, *path: str) -> None:
    current = payload
    for key in path[:-1]:
        value = current.get(key)
        if not isinstance(value, dict):
            return
        current = value
    current.pop(path[-1], None)


def _shrink_payload(payload: dict) -> dict:
    # Preserve item_id/item_url for tap-through and keep the thumbnail whenever
    # possible so the expanded notification can render its media preview.
    drop_order = [
        ("item_content_text",),
        ("preview_item", "content_text"),
        ("item_author",),
        ("preview_item", "author"),
        ("preview_item", "title"),
        ("item_published_at",),
        ("preview_item", "published_at"),
        ("item_media_type",),
        ("preview_item", "media_type"),
        ("item_platform",),
        ("preview_item", "platform"),
        ("aps", "target-content-id"),
        ("thumbnail_url",),
        ("preview_item", "thumbnail_url"),
    ]
    for path in drop_order:
        if _payload_size(payload) <= _APNS_PAYLOAD_SOFT_LIMIT_BYTES:
            break
        _drop_path(payload, *path)
    if isinstance(payload.get("preview_item"), dict) and not payload["preview_item"]:
        payload.pop("preview_item", None)
    return payload


def _payload_exceeds_apns_limit(payload: dict) -> bool:
    return _payload_size(payload) > _APNS_PAYLOAD_HARD_LIMIT_BYTES


def _diagnostics_url() -> str:
    return f"{settings.backend_public_url.rstrip('/')}/api/client-diagnostics"


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    headers: dict[str, str],
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(_APNS_MAX_ATTEMPTS):
        try:
            response = await client.post(url, json=payload, headers=headers)
        except Exception as exc:
            last_error = exc
        else:
            if response.status_code not in _APNS_TRANSIENT_STATUS_CODES:
                return response
            last_error = None

        if attempt + 1 < _APNS_MAX_ATTEMPTS:
            await asyncio.sleep(0.25 * (2 ** attempt))

    if last_error is not None:
        raise last_error
    return response


async def validate_device_registration(device: APNSDeviceToken) -> bool:
    """Return True only when APNs accepts the registered token.

    Registration remains unauthenticated so the iOS app can bootstrap a device
    credential, but device-scoped write auth is only enabled after this server-side
    APNs check succeeds.
    """
    if not apns_configured():
        return False
    host = _host(device.environment)
    headers = {
        "authorization": f"bearer {_cached_auth_token()}",
        "apns-topic": settings.apns_topic,
        "apns-push-type": "background",
        "apns-priority": "5",
        "apns-collapse-id": "oshireader-device-registration",
    }
    payload = {"aps": {"content-available": 1}, "registration_check": 1}
    try:
        async with httpx.AsyncClient(timeout=10.0, http2=True) as client:
            resp = await _post_with_retry(
                client,
                f"{host}/3/device/{device.token}",
                payload,
                headers,
            )
    except Exception as exc:
        log.warning("APNs registration validation failed token=%s: %s", device.token[-8:], exc)
        return False
    if resp.status_code in {200, 201}:
        return True
    try:
        reason = resp.json().get("reason")
    except Exception:
        reason = resp.text
    log.warning(
        "APNs registration validation rejected token=%s status=%d reason=%s",
        device.token[-8:],
        resp.status_code,
        reason,
    )
    return False


async def revalidate_unverified_devices(db: Session) -> int:
    """Periodically repair registrations whose initial APNs check was transiently unsuccessful."""
    if not apns_configured():
        return 0
    now = datetime.now(timezone.utc)
    retry_before = now - _DEVICE_REVALIDATION_INTERVAL
    devices = (
        db.query(APNSDeviceToken)
        .filter(APNSDeviceToken.is_verified == False)  # noqa: E712
        .filter(
            (APNSDeviceToken.verification_attempted_at.is_(None))
            | (APNSDeviceToken.verification_attempted_at <= retry_before)
        )
        .all()
    )
    if not devices:
        return 0

    results = await asyncio.gather(
        *[validate_device_registration(device) for device in devices],
        return_exceptions=True,
    )
    verified = 0
    for device, result in zip(devices, results):
        device.verification_attempted_at = now
        if result is True:
            device.is_verified = True
            device.verified_at = now
            verified += 1
    record_backend_event(
        db,
        "apns_registration",
        "revalidated",
        "Retried unverified APNs device registrations",
        {"attempted": len(devices), "verified": verified},
    )
    db.commit()
    return verified


def _payload(term: WatchTerm, count: int, preview_item: dict | None = None) -> dict:
    preview = _preview_text(preview_item)
    body = preview or (f"{count}件の新着があります。" if count != 1 else "1件の新着があります。")
    if preview and count > 1:
        body = f"{body}\nほか{count - 1}件"
    alert = {
        "title": f"{term.keyword} の新着",
        "body": body,
    }

    payload = {
        "aps": {
            "alert": alert,
            "sound": "default",
            "content-available": 1,
            "mutable-content": 1,
            "category": "OSHI_RESULT_PREVIEW",
            "thread-id": f"oshireader-{term.id}",
        },
        "watch_term_id": term.id,
        "watch_term_keyword": term.keyword,
        "new_count": count,
    }
    if preview_item:
        payload["preview_item"] = {
            key: value
            for key in (
                "id",
                "match_id",
                "platform",
                "url",
                "title",
                "content_text",
                "author",
                "thumbnail_url",
                "media_type",
                "published_at",
            )
            if (value := _payload_value(preview_item, key))
        }
        top_level_preview_keys = {
            "id": "item_id",
            "match_id": "match_id",
            "url": "item_url",
            "platform": "item_platform",
            "title": "item_title",
            "content_text": "item_content_text",
            "author": "item_author",
            "media_type": "item_media_type",
            "published_at": "item_published_at",
        }
        for source_key, payload_key in top_level_preview_keys.items():
            if payload["preview_item"].get(source_key):
                payload[payload_key] = payload["preview_item"][source_key]
        if payload["preview_item"].get("id"):
            payload["aps"]["target-content-id"] = payload["preview_item"]["id"]
        if payload["preview_item"].get("thumbnail_url"):
            payload["thumbnail_url"] = payload["preview_item"]["thumbnail_url"]
    return _shrink_payload(payload)


def _collapse_id(term: WatchTerm) -> str:
    return f"oshireader-{term.id}"[:64]


def _device_identity_key(device: APNSDeviceToken) -> tuple[str, str, str]:
    if device.device_id:
        return (device.environment or "", "device_id", device.device_id)
    if device.device_secret:
        return (device.environment or "", "secret", device.device_secret)
    return (device.environment or "", "token", device.token)


def _device_recency(device: APNSDeviceToken) -> float:
    candidate = device.last_seen_at or device.verified_at
    if candidate is None:
        return 0
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    return candidate.timestamp()


def _dedupe_devices(devices: list[APNSDeviceToken]) -> list[APNSDeviceToken]:
    newest_by_identity: dict[tuple[str, str, str], APNSDeviceToken] = {}
    for device in devices:
        key = _device_identity_key(device)
        existing = newest_by_identity.get(key)
        if existing is None or _device_recency(device) >= _device_recency(existing):
            newest_by_identity[key] = device
    return list(newest_by_identity.values())


def _test_payload() -> dict:
    term = WatchTerm(keyword="OshiReader")
    term.id = 0
    preview_image_url = f"{settings.backend_public_url.rstrip('/')}/api/notification-preview.png"
    return _payload(
        term,
        1,
        {
            "id": "oshireader:test-preview",
            "platform": "OshiReader",
            "url": settings.backend_public_url.rstrip("/"),
            "title": "通知プレビューのテスト",
            "content_text": "新着結果のタイトル、本文、リンクが通知内に表示されます。",
            "author": "OshiReader",
            "thumbnail_url": preview_image_url,
            "media_type": "article",
        },
    )


async def _send_one(
    client: httpx.AsyncClient,
    device: APNSDeviceToken,
    term: WatchTerm,
    count: int,
    preview_item: dict | None = None,
) -> APNSSendResult:
    url = f"{_host(device.environment)}/3/device/{device.token}"
    headers = {
        "authorization": f"bearer {_cached_auth_token()}",
        "apns-topic": settings.apns_topic,
        "apns-push-type": "alert",
        "apns-priority": "10",
        "apns-collapse-id": _collapse_id(term),
    }
    payload = _payload(term, count, preview_item)
    notification_id = uuid.uuid4().hex
    payload["notification_id"] = notification_id
    payload["apns_token_suffix"] = device.token[-8:]
    payload["diagnostics_url"] = _diagnostics_url()
    payload = _shrink_payload(payload)
    if _payload_exceeds_apns_limit(payload):
        log.warning(
            "APNs payload too large; skipping send token=%s term=%s bytes=%d",
            device.token[-8:],
            term.id,
            _payload_size(payload),
        )
        return APNSSendResult(notification_id=notification_id)

    try:
        resp = await _post_with_retry(client, url, payload, headers)
    except Exception as exc:
        log.warning("APNs send failed token=%s: %s", device.token[-8:], exc)
        return APNSSendResult(retryable_failure=True, notification_id=notification_id)

    if resp.status_code in {200, 201}:
        return APNSSendResult(delivered=True, notification_id=notification_id)

    reason = None
    try:
        reason = resp.json().get("reason")
    except Exception:
        reason = resp.text
    log.warning("APNs rejected token=%s status=%d reason=%s", device.token[-8:], resp.status_code, reason)
    should_delete = reason in {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"} or resp.status_code == 410
    retryable = resp.status_code in _APNS_TRANSIENT_STATUS_CODES
    return APNSSendResult(
        should_delete_token=should_delete,
        retryable_failure=retryable,
        notification_id=notification_id,
    )


async def send_new_match_notifications(
    db: Session,
    term: WatchTerm,
    count: int,
    preview_item: dict | None = None,
) -> bool:
    if count <= 0:
        return True
    if not term.notify_on_new:
        record_backend_event(
            db,
            "apns",
            "skipped",
            "Watch term notifications are disabled",
            {"term_id": term.id, "keyword": term.keyword, "new_count": count},
        )
        db.commit()
        return True
    if not apns_configured():
        log.info("APNs not configured; skipping remote notification term=%r count=%d", term.keyword, count)
        record_backend_event(
            db,
            "apns",
            "skipped",
            "APNs is not configured",
            {"term_id": term.id, "keyword": term.keyword, "new_count": count},
        )
        db.commit()
        return False

    # Send to every registered device; _send_one routes each token to the APNs host
    # matching its own environment. Previously this filtered to a single global
    # environment, so a server set to "sandbox" never delivered to production
    # (TestFlight) tokens — and vice versa.
    query = db.query(APNSDeviceToken).filter(APNSDeviceToken.is_verified == True)  # noqa: E712
    if term.owner_device_secret:
        query = query.filter(APNSDeviceToken.device_secret == term.owner_device_secret)
    candidate_devices: list[APNSDeviceToken] = query.all()
    devices = _dedupe_devices(candidate_devices)
    if not devices:
        total_devices = db.query(APNSDeviceToken).count()
        total_verified_devices = (
            db.query(APNSDeviceToken)
            .filter(APNSDeviceToken.is_verified == True)  # noqa: E712
            .count()
        )
        owner_devices = None
        owner_verified_devices = None
        if term.owner_device_secret:
            owner_query = db.query(APNSDeviceToken).filter(
                APNSDeviceToken.device_secret == term.owner_device_secret
            )
            owner_devices = owner_query.count()
            owner_verified_devices = owner_query.filter(
                APNSDeviceToken.is_verified == True  # noqa: E712
            ).count()
        record_backend_event(
            db,
            "apns",
            "skipped",
            "No registered APNs device tokens",
            {
                "term_id": term.id,
                "keyword": term.keyword,
                "new_count": count,
                "owner_scoped": bool(term.owner_device_secret),
                "total_devices": total_devices,
                "total_verified_devices": total_verified_devices,
                "owner_devices": owner_devices,
                "owner_verified_devices": owner_verified_devices,
            },
        )
        db.commit()
        # An owner-scoped device can become verified after registration or a
        # temporary APNs/configuration failure. Keep its outbox entry so the next
        # poll can retry instead of permanently discarding the alert.
        return not bool(term.owner_device_secret and owner_devices)
    async with httpx.AsyncClient(timeout=10.0, http2=True) as client:
        results = await asyncio.gather(
            *[_send_one(client, device, term, count, preview_item) for device in devices]
        )
    pruned_tokens = 0
    delivered_count = 0
    retryable_failures = 0
    device_results = []
    for device, result in zip(devices, results):
        if isinstance(result, bool):
            result = APNSSendResult(should_delete_token=result)
        if result.delivered:
            delivered_count += 1
        if result.retryable_failure:
            retryable_failures += 1
        if result.should_delete_token:
            db.delete(device)
            pruned_tokens += 1
        if len(device_results) < _APNS_EVENT_DEVICE_RESULT_LIMIT:
            device_result = {
                "token": device.token[-8:],
                "environment": device.environment,
                "delivered": result.delivered,
                "retryable_failure": result.retryable_failure,
                "pruned": result.should_delete_token,
            }
            if result.notification_id:
                device_result["notification_id"] = result.notification_id
            device_results.append(device_result)
    record_backend_event(
        db,
        "apns",
        "attempted",
        "APNs notification attempted",
        {
            "term_id": term.id,
            "keyword": term.keyword,
            "new_count": count,
            "device_count": len(devices),
            "candidate_device_count": len(candidate_devices),
            "delivered_count": delivered_count,
            "retryable_failures": retryable_failures,
            "pruned_tokens": pruned_tokens,
            "device_results": device_results,
            "device_results_truncated": max(0, len(devices) - len(device_results)),
        },
    )
    db.commit()
    return delivered_count > 0


async def send_test_push(db: Session) -> dict:
    """Send a diagnostic notification to every registered device immediately and
    report the per-device APNs result, so push setup can be verified without
    waiting for a new feed item."""
    if not apns_configured():
        return {"configured": False, "results": []}
    candidate_devices: list[APNSDeviceToken] = (
        db.query(APNSDeviceToken)
        .filter(APNSDeviceToken.is_verified == True)  # noqa: E712
        .all()
    )
    devices = _dedupe_devices(candidate_devices)
    if not devices:
        return {"configured": True, "results": [], "note": "no device tokens registered"}

    payload = _test_payload()
    results: list[dict] = []
    dead: list[APNSDeviceToken] = []
    collapse_id = f"oshireader-test-{uuid.uuid4().hex[:12]}"
    async with httpx.AsyncClient(timeout=10.0, http2=True) as client:
        for device in devices:
            host = _host(device.environment)
            headers = {
                "authorization": f"bearer {_cached_auth_token()}",
                "apns-topic": settings.apns_topic,
                "apns-push-type": "alert",
                "apns-priority": "10",
                "apns-collapse-id": collapse_id,
            }
            entry = {"token": device.token[-8:], "environment": device.environment, "host": host}
            try:
                resp = await _post_with_retry(
                    client,
                    f"{host}/3/device/{device.token}",
                    payload,
                    headers,
                )
                entry["status"] = resp.status_code
                if resp.status_code not in (200, 201):
                    reason = None
                    try:
                        reason = resp.json().get("reason")
                    except Exception:
                        reason = resp.text
                    entry["reason"] = reason
                    if reason in {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"} or resp.status_code == 410:
                        dead.append(device)
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
            results.append(entry)
    if dead:
        for device in dead:
            db.delete(device)
        db.commit()
    return {
        "configured": True,
        "results": results,
        "pruned_tokens": len(dead),
        "candidate_device_count": len(candidate_devices),
    }


async def send_test_push_to_device(db: Session, device: APNSDeviceToken) -> dict:
    if not apns_configured():
        return {"configured": False, "results": []}

    payload = _test_payload()
    host = _host(device.environment)
    headers = {
        "authorization": f"bearer {_cached_auth_token()}",
        "apns-topic": settings.apns_topic,
        "apns-push-type": "alert",
        "apns-priority": "10",
        "apns-collapse-id": f"oshireader-test-{uuid.uuid4().hex[:12]}",
    }
    entry = {"token": device.token[-8:], "environment": device.environment, "host": host}
    should_delete = False
    async with httpx.AsyncClient(timeout=10.0, http2=True) as client:
        try:
            resp = await _post_with_retry(
                client,
                f"{host}/3/device/{device.token}",
                payload,
                headers,
            )
            entry["status"] = resp.status_code
            if resp.status_code not in (200, 201):
                try:
                    reason = resp.json().get("reason")
                except Exception:
                    reason = resp.text
                entry["reason"] = reason
                should_delete = reason in {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"} or resp.status_code == 410
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"

    if should_delete:
        db.delete(device)
        db.commit()
    return {"configured": True, "results": [entry], "pruned_tokens": 1 if should_delete else 0}
