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
uvicorn app.main:app --reload
```

The API runs at `http://127.0.0.1:8000`, with interactive docs at `http://127.0.0.1:8000/docs`.

### Configuration

Backend settings are loaded from environment variables or `backend/.env`.

| Environment Variable | Default | Description |
|---|---:|---|
| `DATABASE_URL` | `sqlite:///./otterpia.db` | SQLAlchemy database URL. Use a PostgreSQL URL for production. |
| `YOUTUBE_API_KEY` | empty | Optional YouTube Data API key. The connector falls back to scraping when absent. |
| `POLL_INTERVAL_MINUTES` | `15` | Scheduler interval for automatic ingestion. |
| `ADMIN_API_TOKEN` | empty | Optional bearer token required for admin, credential, and watch-term write endpoints. Set this in production. |
| `CORS_ALLOW_ORIGINS` | empty | Comma-separated browser origins allowed by CORS. Native iOS calls do not need CORS. |
| `APNS_TEAM_ID` | empty | Apple Developer Team ID for remote push notifications. |
| `APNS_KEY_ID` | empty | Apple APNs auth key ID. |
| `APNS_PRIVATE_KEY` | empty | APNs `.p8` private key text. Use escaped `\n` line breaks if storing in one environment variable. |
| `APNS_PRIVATE_KEY_PATH` | empty | Alternative path to the APNs `.p8` private key file. |
| `APNS_TOPIC` | `com.otterpia.oshireader` | APNs topic, usually the iOS bundle identifier. |
| `APNS_USE_SANDBOX` | `true` | Use APNs sandbox host for development builds. Set to `false` for production tokens. |

When `ADMIN_API_TOKEN` is set, protected requests must include:

```text
Authorization: Bearer <token>
```

## Native iOS App

The SwiftUI app lives in `ios-swift/` and targets iOS 17+.

### Setup

```bash
cd ios-swift
xcodegen generate
open OshiReader.xcodeproj
```

Select a simulator or device in Xcode and run the `OshiReader` scheme.

The backend URL is configured in `ios-swift/OshiReader/NetworkManager.swift`.

Remote push notifications use APNs. The Swift app registers its APNs device token after notification permission is granted, then posts that token to `/api/devices/apns-token`. The backend sends remote notifications for new matches only when the watch term has notifications enabled.

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
| YahooNews | Yahoo News article search through a text mirror for EEA-safe access |
| News/RSS | Curated Japanese entertainment RSS feeds |

Watch terms support aliases. The scheduler searches the primary keyword and each alias while storing matches against the original watch term.
