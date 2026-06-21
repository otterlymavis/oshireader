from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import APNSDeviceToken


@dataclass(frozen=True)
class AuthContext:
    is_admin: bool
    device: APNSDeviceToken | None = None
    standalone_device_secret: str | None = None

    @property
    def device_secret(self) -> str | None:
        if self.device:
            return self.device.device_secret
        return self.standalone_device_secret


def _allow_unauthenticated_admin() -> bool:
    return settings.allow_unauthenticated_admin is True


def _missing_admin_token_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="ADMIN_API_TOKEN is not configured",
    )


def _require_bearer_token(expected_token: str, authorization: Optional[str]) -> None:
    if not expected_token:
        if _allow_unauthenticated_admin():
            return
        raise _missing_admin_token_error()
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _require_configured_admin_token() -> str:
    token = settings.admin_api_token
    if token:
        return token
    if _allow_unauthenticated_admin():
        return ""
    raise _missing_admin_token_error()


def require_admin_auth(authorization: Optional[str] = Header(default=None)) -> None:
    _require_bearer_token(settings.admin_api_token, authorization)


def require_admin_or_device_auth(
    authorization: Optional[str] = Header(default=None),
    device_token: Optional[str] = Header(default=None, alias="X-Device-Token"),
    device_secret: Optional[str] = Header(default=None, alias="X-Device-Secret"),
    db: Session = Depends(get_db),
) -> AuthContext:
    admin_token = _require_configured_admin_token()
    if not admin_token:
        return AuthContext(is_admin=True)

    normalized_token = "".join((device_token or "").strip().lower().split())
    if normalized_token and device_secret:
        stored = db.get(APNSDeviceToken, normalized_token)
        if stored and stored.device_secret and stored.is_verified:
            digest = hashlib.sha256(device_secret.encode("utf-8")).hexdigest()
            if secrets.compare_digest(stored.device_secret, digest) or secrets.compare_digest(
                stored.device_secret, device_secret
            ):
                return AuthContext(is_admin=False, device=stored)
    if device_secret:
        # Device identity must not depend on APNs availability. Simulators cannot
        # receive an APNs token, and APNs verification can fail transiently after
        # iOS has already handed the app a token. The secret is still a complete,
        # isolated device identity in either case. Its digest matches the value
        # stored for APNs devices, so later verification preserves ownership.
        return AuthContext(
            is_admin=False,
            standalone_device_secret=hashlib.sha256(device_secret.encode("utf-8")).hexdigest(),
        )

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and secrets.compare_digest(token, admin_token):
        return AuthContext(is_admin=True)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing admin or device credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
