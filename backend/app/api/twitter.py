"""Twitter account login / status / disconnect endpoints."""
import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.connectors.twitter import init_twitter_api, logout_twitter, twitter_logged_in_username
from app.database import get_db
from app.models import PlatformCredential
from app.schemas import TwitterLoginRequest, TwitterStatusOut

router = APIRouter(prefix="/api/twitter", tags=["twitter"])
log = logging.getLogger(__name__)


@router.get("/status", response_model=TwitterStatusOut)
def get_status():
    """Return whether a Twitter account is currently logged in."""
    username = twitter_logged_in_username()
    return TwitterStatusOut(logged_in=username is not None, username=username)


@router.post("/login", response_model=TwitterStatusOut)
async def login(body: TwitterLoginRequest, db: Session = Depends(get_db)):
    """
    Log in with the provided Twitter credentials.

    Credentials are persisted in the database so the session can be
    restored automatically on the next backend restart.
    """
    # Persist credentials in DB
    cred = db.get(PlatformCredential, "twitter")
    if cred is None:
        cred = PlatformCredential(platform="twitter")
        db.add(cred)

    cred.username = body.username.strip()
    cred.password = body.password
    cred.email = (body.email or "").strip() or None
    db.commit()

    # Perform the actual login (runs in background so request returns quickly)
    success = await init_twitter_api(
        username=cred.username,
        password=cred.password,
        email=cred.email or "",
    )

    if not success:
        return TwitterStatusOut(logged_in=False, username=None)

    return TwitterStatusOut(logged_in=True, username=cred.username)


@router.post("/disconnect", response_model=TwitterStatusOut)
async def disconnect(db: Session = Depends(get_db)):
    """Remove stored Twitter credentials and clear the active session."""
    cred = db.get(PlatformCredential, "twitter")
    if cred:
        cred.username = None
        cred.password = None
        cred.email = None
        db.commit()

    await logout_twitter()
    return TwitterStatusOut(logged_in=False, username=None)
