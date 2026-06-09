from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin_auth
from app.database import get_db
from app.models import PlatformCredential
from app.schemas import CredentialOut, CredentialUpsert

router = APIRouter(prefix="/api/credentials", tags=["credentials"])

SUPPORTED_PLATFORMS = ["youtube", "twitter"]


@router.get("/", response_model=list[CredentialOut])
def list_credentials(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)):
    stored = {c.platform: c for c in db.query(PlatformCredential).all()}
    result = []
    for platform in SUPPORTED_PLATFORMS:
        cred = stored.get(platform)
        result.append(
            CredentialOut(
                platform=platform,
                has_bearer_token=bool(cred and cred.bearer_token),
                has_api_key=bool(cred and cred.api_key),
                has_api_secret=bool(cred and cred.api_secret),
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
        cred.bearer_token = body.bearer_token or None
    if body.api_key is not None:
        cred.api_key = body.api_key or None
    if body.api_secret is not None:
        cred.api_secret = body.api_secret or None
    cred.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(cred)
    return CredentialOut(
        platform=cred.platform,
        has_bearer_token=bool(cred.bearer_token),
        has_api_key=bool(cred.api_key),
        has_api_secret=bool(cred.api_secret),
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
