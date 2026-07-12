from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin_auth
from app.database import get_db
from app.models import PlatformCredential
from app.schemas import CredentialOut, CredentialUpsert

router = APIRouter(prefix="/api/credentials", tags=["credentials"])

SUPPORTED_PLATFORMS = ["youtube", "twitter"]


def _clean_secret(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _has_secret(value: str | None) -> bool:
    return bool(_clean_secret(value))


@router.get("/", response_model=list[CredentialOut])
def list_credentials(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)):
    stored = {c.platform: c for c in db.query(PlatformCredential).all()}
    result = []
    for platform in SUPPORTED_PLATFORMS:
        cred = stored.get(platform)
        result.append(
            CredentialOut(
                platform=platform,
                has_bearer_token=_has_secret(cred.bearer_token) if cred else False,
                has_api_key=_has_secret(cred.api_key) if cred else False,
                has_api_secret=_has_secret(cred.api_secret) if cred else False,
                updated_at=cred.updated_at if cred else None,
            )
        )
    return result


@router.put("/{platform}", response_model=CredentialOut)
def upsert_credential(
    platform: str,
    body: CredentialUpsert,
    _: None = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(404, f"Unknown platform: {platform!r}")
    cred = db.get(PlatformCredential, platform)
    if cred is None:
        cred = PlatformCredential(platform=platform)
        db.add(cred)
    if body.bearer_token is not None:
        cred.bearer_token = _clean_secret(body.bearer_token)
    if body.api_key is not None:
        cred.api_key = _clean_secret(body.api_key)
    if body.api_secret is not None:
        cred.api_secret = _clean_secret(body.api_secret)
    cred.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(cred)
    return CredentialOut(
        platform=cred.platform,
        has_bearer_token=_has_secret(cred.bearer_token),
        has_api_key=_has_secret(cred.api_key),
        has_api_secret=_has_secret(cred.api_secret),
        updated_at=cred.updated_at,
    )


@router.delete("/{platform}", status_code=204)
def delete_credential(
    platform: str,
    _: None = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(404, f"Unknown platform: {platform!r}")
    cred = db.get(PlatformCredential, platform)
    if cred:
        db.delete(cred)
        db.commit()
