"""Twitter account login / status / disconnect endpoints."""
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

    Credentials and session cookies are persisted in the database so the
    session can be restored automatically on the next backend restart.
    """
    # Persist credentials
    cred = db.get(PlatformCredential, "twitter")
    if cred is None:
        cred = PlatformCredential(platform="twitter")
        db.add(cred)

    cred.username = body.username.strip()
    cred.password = body.password
    cred.email = (body.email or "").strip() or None
    db.commit()

    # Perform login — pass existing cookies so we can try a fast restore
    # (bearer_token column is repurposed to store session cookies JSON)
    success, new_cookies_json = await init_twitter_api(
        username=cred.username,
        password=cred.password,
        email=cred.email or "",
        cookies_json=cred.bearer_token,  # repurposed for session cookies
    )

    if not success:
        return TwitterStatusOut(logged_in=False, username=None)

    # Persist updated session cookies
    if new_cookies_json:
        cred.bearer_token = new_cookies_json
        db.commit()

    return TwitterStatusOut(logged_in=True, username=cred.username)


@router.post("/disconnect", response_model=TwitterStatusOut)
async def disconnect(db: Session = Depends(get_db)):
    """Remove stored Twitter credentials and clear the active session."""
    cred = db.get(PlatformCredential, "twitter")
    if cred:
        cred.username = None
        cred.password = None
        cred.email = None
        cred.bearer_token = None  # also clear stored cookies
        db.commit()

    await logout_twitter()
    return TwitterStatusOut(logged_in=False, username=None)
