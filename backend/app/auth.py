from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, status

from app.config import settings


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
