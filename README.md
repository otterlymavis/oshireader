# OshiReader (Otterpia)

OshiReader is a native SwiftUI iOS app backed by a FastAPI ingestion service. It tracks favorite creators, idols, and topics across supported sources, stores matched feed items in the backend, and presents them in the Swift app.

## Project Structure

```text
oshireader/
├── backend/            # FastAPI service and ingestion scheduler
└── ios-swift/          # Native iOS SwiftUI application
```

## Backend

The backend exposes a REST API for watch terms, feed items, credentials, admin stats, and manual polling. It also runs a scheduled ingestion job.

### Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

The API runs at `http://127.0.0.1:8000`, with interactive docs at `http://127.0.0.1:8000/docs`.
The sample `.env` enables unauthenticated admin/write access for private local
development only. Set `ADMIN_API_TOKEN` and disable
`ALLOW_UNAUTHENTICATED_ADMIN` before using a shared or deployed backend.

### Configuration

Backend settings are loaded from environment variables or `backend/.env`.

| Environment Variable | Default | Description |
|---|---:|---|
| `DATABASE_URL` | `sqlite:///./otterpia.db` | SQLAlchemy database URL. Use a PostgreSQL URL for production. |
| `YOUTUBE_API_KEY` | empty | Optional YouTube Data API key. The connector falls back to scraping when absent. |
| `POLL_INTERVAL_MINUTES` | `15` | Scheduler interval for automatic ingestion. |
| `CONNECTOR_FETCH_TIMEOUT_SECONDS` | `25.0` | Per-source fetch timeout. A timed-out connector is skipped for that term so one stalled source cannot hold the poll lock forever. |
| `ADMIN_API_TOKEN` | empty | Bearer token required for admin, credential, and watch-term write endpoints. Set this before running any shared or deployed backend. |
| `ALLOW_UNAUTHENTICATED_ADMIN` | `false` | Local-development escape hatch. Set to `true` only for a private local backend when you intentionally want admin/write endpoints open. |
| `CORS_ALLOW_ORIGINS` | empty | Comma-separated browser origins allowed by CORS. Native iOS calls do not need CORS. |
| `APNS_TEAM_ID` | empty | Apple Developer Team ID for remote push notifications. |
| `APNS_KEY_ID` | empty | Apple APNs auth key ID. |
| `APNS_PRIVATE_KEY` | empty | APNs `.p8` private key text. Use escaped `\n` line breaks if storing in one environment variable. |
| `APNS_PRIVATE_KEY_PATH` | empty | Alternative path to the APNs `.p8` private key file. |
| `APNS_TOPIC` | `com.otterpia.oshireader.plus` | APNs topic, usually the iOS bundle identifier. |
| `APNS_USE_SANDBOX` | `false` | Fallback APNs host when a stored token has no environment. Device registrations include their own environment, so production and sandbox tokens can coexist. |
| `BACKEND_PUBLIC_URL` | `https://oshireader.onrender.com` | Public backend origin used for compact notification redirect links. |

Admin and credential requests must include:

```text
Authorization: Bearer <token>
```

Watch-term writes also accept the registered app device credentials
(`X-Device-Token` and `X-Device-Secret`), allowing the iOS app to synchronize
keywords without exposing the admin token in the app.

## Native iOS App

The SwiftUI app lives in `ios-swift/` and targets iOS 17+.

### Setup

```bash
cd ios-swift
xcodegen generate
open OshiReader.xcodeproj
```

Select a simulator or device in Xcode and run the `OshiReader` scheme.

The app is a universal iPhone and iPad build. Use schemes to choose the backend environment:

| Scheme | Configuration | Backend |
|---|---|---|
| `OshiReader Local` | `Debug` | `http://127.0.0.1:8000` |
| `OshiReader Staging` | `Staging` | production URL until a staging backend is deployed |
| `OshiReader Production` | `Release` | `https://oshireader.onrender.com` |

UI tests that hit live source endpoints or APNs are skipped by default. Run them
only when you intentionally want external-network checks:

```bash
OSHI_READER_RUN_LIVE_UI_TESTS=1 xcodebuild test -project ios-swift/OshiReader.xcodeproj -scheme "OshiReader Local" -destination 'platform=iOS Simulator,name=iPhone 17' -only-testing:OshiReaderUITests
```

The backend URL is injected through `OshiReaderAPIBaseURL` in `Info.plist`, with values generated from `ios-swift/project.yml`.

Remote push notifications use APNs. The Swift app registers an APNs device token
at launch and posts that token, APNs environment, device identifier, and
per-install `device_secret` to `/api/devices/apns-token`; this device credential
also authenticates watch-term synchronization. Notification permission controls
whether alert notifications are presented. The backend sends remote
notifications for new matches only when the watch term has notifications
enabled.

Background refresh uses two paths:

- Scheduled iOS refresh submits a device-scoped `/api/devices/background-refresh` request authenticated with the registered APNs token or device identifier plus `device_secret`, then fetches feed items after the backend poll has finished.
- New-match APNs payloads include `content-available: 1`, so iOS can wake the app and fetch the already-created matches without triggering a duplicate backend poll.

### Push Notification Release Checklist

Before shipping a build that relies on background/rich notifications:

1. Set production backend environment variables: `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_PRIVATE_KEY` or `APNS_PRIVATE_KEY_PATH`, `APNS_TOPIC=com.otterpia.oshireader.plus`, `APNS_USE_SANDBOX=false`, and `BACKEND_PUBLIC_URL` to the deployed backend origin.
2. Deploy backend migrations before testing pushes. The APNs device table must include `device_secret`.
3. Launch the updated iOS app once after deployment so the app re-registers the APNs token with `device_secret` and its APNs environment; then allow notifications before validating visible alerts.
4. In Settings, send a test notification on a physical device or TestFlight build. The notification should use the rich preview category, and repeated tests should collapse into one diagnostic notification.
5. Trigger a real poll that creates a new match. Confirm the notification arrives while the app is backgrounded, expands with preview UI, wakes the app to refresh the feed, opens the result on tap, and saves via the notification action.
6. Check `/api/admin/stats` for APNs configuration and token environment counts when diagnosing delivery issues.

## Supported Backend Sources

The backend scheduler currently registers these connectors:

| Platform | Source |
|---|---|
| YouTube | YouTube Data API or search scrape fallback |
| NicoNico | NicoNico snapshot search API |
| TVer | TVer keyword search APIs |
| Note | Public tag RSS feeds |
| 5channel | Thread index scraping |
| Girls Channel | Forum topic scraping |
| Togetter | Curation page scraping |
| ModelPress | ModelPress article search |
| Oricon | Oricon article RSS/search |
| YahooNews | Yahoo News article search through a text mirror for EEA-safe access |
| News/RSS | Curated Japanese entertainment RSS feeds |
| SmartNews | Google News site-restricted article search |
| Ameblo | Google News site-restricted article search |
| AERA | Google News site-restricted article search |
| Hochi | Google News site-restricted article search |
| Sponichi | Google News site-restricted article search |
| Livedoor | Google News site-restricted article search |
| Mantan Web | Google News site-restricted article search |
| BARKS | Direct RSS with Google News fallback |
| Real Sound | Google News site-restricted article search |
| CinemaCafe | Google News site-restricted article search |
| Twitter/X | Optional Twitter API connector when credentials are configured |

Watch terms support aliases. The scheduler searches the primary keyword and each alias while storing matches against the original watch term.

### Source Relevance Smoke Check

When a source appears to return unrelated articles, run the smoke helper against the suspicious platform. It fetches raw connector output, applies the same primary-text relevance rule used by ingestion, and shows which items would be kept or dropped.

```bash
python3 backend/scripts/smoke_sources.py --keyword '吉沢亮' --platform livedoor --platform realsound --samples 2
```

`DROP` rows mean the raw source returned broad results, but ingestion would filter those items before they reach the feed. `ERROR` rows mean the connector itself failed.
