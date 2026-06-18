from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import APNSDeviceToken


def _require_bearer_token(expected_token: str, authorization: Optional[str]) -> None:
    if not expected_token:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_admin_auth(authorization: Optional[str] = Header(default=None)) -> None:
    _require_bearer_token(settings.admin_api_token, authorization)


def require_admin_or_device_auth(
    authorization: Optional[str] = Header(default=None),
    device_token: Optional[str] = Header(default=None, alias="X-Device-Token"),
    device_secret: Optional[str] = Header(default=None, alias="X-Device-Secret"),
    db: Session = Depends(get_db),
) -> None:
    if not settings.admin_api_token:
        return

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and secrets.compare_digest(token, settings.admin_api_token):
        return

    normalized_token = "".join((device_token or "").strip().lower().split())
    if normalized_token and device_secret:
        stored = db.get(APNSDeviceToken, normalized_token)
        if stored and stored.device_secret:
            digest = hashlib.sha256(device_secret.encode("utf-8")).hexdigest()
            if secrets.compare_digest(stored.device_secret, digest) or secrets.compare_digest(
                stored.device_secret, device_secret
            ):
                return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing admin or device credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
