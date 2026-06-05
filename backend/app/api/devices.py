from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin_auth
from app.database import get_db
from app.models import APNSDeviceToken
from app.schemas import APNSDeviceTokenOut, APNSDeviceTokenUpsert

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _normalize_token(token: str) -> str:
    return "".join(token.strip().lower().split())


@router.post("/apns-token", response_model=APNSDeviceTokenOut, status_code=201)
def upsert_apns_token(body: APNSDeviceTokenUpsert, db: Session = Depends(get_db)):
    token = _normalize_token(body.token)
    if not token or any(ch not in "0123456789abcdef" for ch in token):
        raise HTTPException(400, "Invalid APNs device token")

    stored = db.get(APNSDeviceToken, token)
    if not stored:
        stored = APNSDeviceToken(token=token)
        db.add(stored)

    stored.environment = body.environment
    stored.device_id = body.device_id
    stored.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(stored)
    return stored


@router.delete("/apns-token/{token}", status_code=204)
def delete_apns_token(token: str, db: Session = Depends(get_db)):
    stored = db.get(APNSDeviceToken, _normalize_token(token))
    if stored:
        db.delete(stored)
        db.commit()


@router.get("/apns-tokens", response_model=list[APNSDeviceTokenOut])
def list_apns_tokens(_: None = Depends(require_admin_auth), db: Session = Depends(get_db)):
    return db.query(APNSDeviceToken).order_by(APNSDeviceToken.last_seen_at.desc()).all()
