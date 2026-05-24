"""Expo push token registration endpoints."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PushToken

router = APIRouter(prefix="/api", tags=["push"])


class PushTokenBody(BaseModel):
    token: str


@router.post("/push-token", status_code=200)
def register_token(body: PushTokenBody, db: Session = Depends(get_db)):
    """Register (or refresh) an Expo push token for this device."""
    if not db.get(PushToken, body.token):
        db.add(PushToken(token=body.token))
        db.commit()
    return {"ok": True}


@router.delete("/push-token/{token}", status_code=200)
def remove_token(token: str, db: Session = Depends(get_db)):
    """Unregister a push token (e.g. when backend feed is disabled)."""
    row = db.get(PushToken, token)
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}
