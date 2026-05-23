"""Twitter connector using twikit (unofficial internal API via user account login)."""
import asyncio
import json
import logging
from datetime import datetime

from app.connectors.base import BaseConnector, SourceItemCreate

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global twikit client singleton
# ---------------------------------------------------------------------------
_client = None
_client_lock = asyncio.Lock()
_logged_in_username: str | None = None


def twitter_logged_in_username() -> str | None:
    return _logged_in_username


async def init_twitter_api(
    username: str,
    password: str,
    email: str = "",
    cookies_json: str | None = None,
) -> tuple[bool, str | None]:
    """
    Authenticate with Twitter using twikit.

    Tries cookie-based restore first (fast, avoids re-login).
    Falls back to full username/password login if cookies are missing/stale.

    Returns (success, cookies_json_to_persist).
    The caller should save the returned cookies_json to the DB.
    """
    global _client, _logged_in_username

    async with _client_lock:
        try:
            from twikit import Client  # lazy import

            # ── 1. Try cookie restore ────────────────────────────────────────
            if cookies_json:
                try:
                    client = Client("en-US")
                    cookies = json.loads(cookies_json)
                    client.http.cookies.update(cookies)

                    # Quick smoke-test: run a trivial search to confirm session
                    await client.search_tweet("test", "Latest", count=1)

                    _client = client
                    _logged_in_username = username
                    log.info("Twitter session restored from cookies for @%s", username)
                    return True, cookies_json
                except Exception as e:
                    log.warning("Cookie restore failed for @%s (%s) — doing full login", username, e)

            # ── 2. Full login ────────────────────────────────────────────────
            client = Client("en-US")

            # auth_info_2 is the email associated with the account (helps avoid
            # Twitter's bot-detection for new login attempts).
            auth_info_2 = email.strip() if email.strip() else username

            await client.login(
                auth_info_1=username.strip(),
                auth_info_2=auth_info_2,
                password=password,
            )

            # Persist cookies so restarts skip the slow login step
            new_cookies_json = json.dumps(dict(client.http.cookies.items()))

            _client = client
            _logged_in_username = username.strip()
            log.info("Twitter logged in as @%s", username)
            return True, new_cookies_json

        except Exception as exc:
            log.error("Twitter login error for @%s: %s", username, exc, exc_info=True)
            return False, None


async def logout_twitter() -> None:
    global _client, _logged_in_username
    async with _client_lock:
        _client = None
        _logged_in_username = None
    log.info("Twitter session cleared")


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

class TwitterConnector(BaseConnector):
    PLATFORM = "twitter"
    SUPPORTS_MEDIA_FILTER = True

    async def fetch(self, keyword: str, mode: str) -> list[SourceItemCreate]:
        client = _client
        if client is None:
            log.debug("Twitter connector: no active session, skipping")
            return []

        query = keyword
        if mode == "media_only":
            query += " filter:media"

        items: list[SourceItemCreate] = []
        try:
            result = await client.search_tweet(query, "Latest", count=25)
            for tweet in result:
                try:
                    tweet_id = str(tweet.id)
                    user = getattr(tweet, "user", None)
                    screen_name = getattr(user, "screen_name", "") or ""

                    # Tweet text (twikit uses full_text when available)
                    text = (
                        getattr(tweet, "full_text", None)
                        or getattr(tweet, "text", None)
                        or ""
                    )

                    # Parse created_at (Twitter format: "Wed Jun 19 17:49:33 +0000 2024")
                    created_at_raw = getattr(tweet, "created_at", None)
                    if isinstance(created_at_raw, str):
                        try:
                            published = datetime.strptime(
                                created_at_raw, "%a %b %d %H:%M:%S +0000 %Y"
                            )
                        except ValueError:
                            published = datetime.utcnow()
                    elif created_at_raw is not None:
                        # Already a datetime
                        published = created_at_raw
                    else:
                        published = datetime.utcnow()

                    # Extract thumbnail from media
                    thumb: str | None = None
                    media_list = getattr(tweet, "media", None) or []
                    for m in media_list:
                        mtype = getattr(m, "type", "")
                        if mtype == "photo":
                            thumb = getattr(m, "media_url_https", None)
                        elif mtype in ("video", "animated_gif"):
                            # Try thumbnail URL from video_info
                            video_info = getattr(m, "video_info", None)
                            if video_info:
                                thumb = getattr(video_info, "thumbnail_url", None)
                            thumb = thumb or getattr(m, "media_url_https", None)
                        if thumb:
                            break

                    url = (
                        f"https://fixupx.com/{screen_name}/status/{tweet_id}"
                        if screen_name
                        else f"https://x.com/i/status/{tweet_id}"
                    )

                    items.append(
                        SourceItemCreate(
                            platform=self.PLATFORM,
                            item_id=tweet_id,
                            url=url,
                            published_at=published,
                            media_type="video" if thumb else "text",
                            author=f"@{screen_name}" if screen_name else None,
                            title=None,
                            content_text=text or None,
                            thumbnail_url=thumb,
                            raw_payload={"id": tweet_id, "author": screen_name},
                        )
                    )
                except Exception as inner:
                    log.debug("Skipping tweet due to parse error: %s", inner)

        except Exception as exc:
            log.warning("Twitter search failed for query=%r: %s", query, exc)

        return items
