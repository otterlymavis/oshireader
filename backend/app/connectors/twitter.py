"""Twitter connector using twscrape (unofficial internal API via user account login)."""
import asyncio
import logging
from datetime import datetime

from app.connectors.base import BaseConnector, SourceItemCreate

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global twscrape API singleton — shared across requests & scheduler runs.
# ---------------------------------------------------------------------------
_api = None
_api_lock = asyncio.Lock()
_logged_in_username: str | None = None


async def get_twitter_api():
    """Return the global twscrape API instance, or None if not logged in."""
    return _api


def twitter_logged_in_username() -> str | None:
    return _logged_in_username


async def init_twitter_api(username: str, password: str, email: str = "") -> bool:
    """Log in with the given Twitter credentials and store the API instance.

    Returns True on success, False on failure.
    """
    global _api, _logged_in_username

    async with _api_lock:
        try:
            from twscrape import API as TwscrapeAPI  # lazy import

            api = TwscrapeAPI()

            # twscrape requires an email; use a placeholder if not provided —
            # works for most established accounts that don't need email verification.
            effective_email = email or f"{username}@placeholder.invalid"

            await api.pool.add_account(
                username=username,
                password=password,
                email=effective_email,
                email_password="",  # not needed unless Twitter sends a code
            )
            await api.pool.login_all()

            # Verify at least one account is active
            accounts = await api.pool.get_all()
            active = [a for a in accounts if a.active]
            if not active:
                log.warning("Twitter login failed for @%s — no active accounts after login", username)
                return False

            _api = api
            _logged_in_username = username
            log.info("Twitter logged in as @%s", username)
            return True

        except Exception as exc:
            log.error("Twitter login error for @%s: %s", username, exc, exc_info=True)
            return False


async def logout_twitter() -> None:
    """Clear the stored Twitter session."""
    global _api, _logged_in_username
    async with _api_lock:
        _api = None
        _logged_in_username = None
    log.info("Twitter session cleared")


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class TwitterConnector(BaseConnector):
    PLATFORM = "twitter"
    SUPPORTS_MEDIA_FILTER = True

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        api = await get_twitter_api()
        if api is None:
            log.debug("Twitter connector: no active session, skipping")
            return []

        query = keyword
        if mode == "media_only":
            query += " has:media"

        items: list[SourceItemCreate] = []
        try:
            async for tweet in api.search(query, limit=25):
                username = tweet.user.username if tweet.user else ""
                tweet_id = str(tweet.id)

                # Extract thumbnail from media attachments
                thumb: str | None = None
                if tweet.media:
                    if tweet.media.photos:
                        thumb = tweet.media.photos[0].url
                    elif tweet.media.videos:
                        thumb = tweet.media.videos[0].thumbnailUrl

                published = tweet.date if tweet.date else datetime.utcnow()

                # Use fixupx.com for better embed / reader experience
                url = (
                    f"https://fixupx.com/{username}/status/{tweet_id}"
                    if username
                    else f"https://x.com/i/status/{tweet_id}"
                )

                items.append(
                    SourceItemCreate(
                        platform=self.PLATFORM,
                        item_id=tweet_id,
                        url=url,
                        published_at=published,
                        media_type="video" if thumb else "text",
                        author=f"@{username}" if username else None,
                        title=None,
                        content_text=tweet.rawContent,
                        thumbnail_url=thumb,
                        raw_payload={"id": tweet_id, "author": username},
                    )
                )
        except Exception as exc:
            log.warning("Twitter search failed for query=%r: %s", query, exc)

        return items
