# 🦦 OshiReader (Otterpia)

[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB)](https://reactjs.org/)
[![Vite](https://img.shields.io/badge/Vite-646CFF?style=for-the-badge&logo=vite&logoColor=FFD62B)](https://vitejs.dev/)
[![Expo](https://img.shields.io/badge/Expo-000020?style=for-the-badge&logo=expo&logoColor=white)](https://expo.dev/)
[![SwiftUI](https://img.shields.io/badge/SwiftUI-F54A2A?style=for-the-badge&logo=swift&logoColor=white)](https://developer.apple.com/xcode/swiftui/)
[![SQLite](https://img.shields.io/badge/SQLite-07405E?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org/)

**OshiReader (Otterpia)** is a multi-platform tracker and content aggregator designed to help you follow your favorite creators, idols, or topics (your *"Oshi"* 🌟) across the web. It polls, parses, and centralizes updates from social media, forums, TV services, and video platforms into a unified dashboard and mobile application.

---

## 🗺️ Project Structure

The project is structured as a monorepo containing the following components:

```text
oshireader/
├── backend/            # FastAPI Python service & data ingestion engine
├── frontend/           # React + Vite web dashboard
├── mobile/             # React Native (Expo) app for iOS & Android
└── ios-swift/          # Native iOS SwiftUI application
```

---

## 🌟 Key Features

* **Multi-Platform Scraping & Ingestion**: Monitors 5channel, Girls Channel, RSS, Togetter, TVer, Twitter/X, YouTube, Note, NicoNico, and general news channels for matching watch terms.
* **Smart Keyword Matching**: Supports tracking terms with customizable aliases, language hints, and collection modes (`all_info` or `media_only`).
* **Cross-Platform Clients**:
  * **Web Client**: A lightweight dashboard for system stats, administering crawler runs, and managing tracked keywords.
  * **React Native (Expo) App**: Cross-platform mobile client featuring local SQLite caching, local content scrapers, custom themes, background tasks, and translation files.
  * **Native SwiftUI App**: A high-performance iOS application featuring customized page designs (Feeds, Saved, Reader), an Avatar/Canvas Editor, a robust Local DB cache, and unique UI themes (Light, Dark, and Sepia).

---

## 🚀 Component Setup & Run Instructions

### 1. ⚙️ Backend (Python + FastAPI)

The backend acts as the data hub, running background tasks to scrape external sources and exposing a REST API.

#### Requirements
* Python 3.10+
* Virtual Environment setup (`.venv`)

#### Setup & Start
1. Navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy the environment template and configure settings:
   ```bash
   cp .env.example .env
   ```
5. Run the server locally:
   ```bash
   uvicorn app.main:app --reload
   ```
   * *The backend API will run at `http://127.0.0.1:8000`*
   * *Swagger interactive API documentation will be available at `http://127.0.0.1:8000/docs`*

---

### 2. 💻 Web Frontend (React + Vite)

The web dashboard allows you to view the latest ingested feed, manage watch terms, and monitor API performance metrics.

#### Requirements
* Node.js (v18+) & `npm`

#### Setup & Start
1. Navigate to the frontend directory:
   ```bash
   cd frontend
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the Vite dev server:
   ```bash
   npm run dev
   ```
   * *The web interface will run at `http://localhost:5173`*

---

### 3. 📱 Mobile App (React Native + Expo)

A cross-platform app providing notifications and customized feeds.

#### Requirements
* Expo CLI (`npm install -g expo-cli` or run via `npx`)
* iOS Simulator / Android Emulator or the **Expo Go** app on your physical device

#### Setup & Start
1. Navigate to the mobile directory:
   ```bash
   cd mobile
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the Expo server:
   ```bash
   npm run start
   ```
4. Press `i` to launch in the iOS simulator, `a` for Android, or scan the QR code using the **Expo Go** app.

---

### 4. 🍎 Native iOS App (SwiftUI)

A native SwiftUI application targeting iOS 17+.

#### Requirements
* macOS with **Xcode 15+** installed
* [XcodeGen](https://github.com/yonaskolb/XcodeGen) (optional, if you want to regenerate project configurations)

#### Setup & Start
1. Navigate to the Swift iOS app directory:
   ```bash
   cd ios-swift
   ```
2. If the `.xcodeproj` needs to be updated or generated, run:
   ```bash
   xcodegen generate
   ```
3. Open `OshiReader.xcodeproj` in Xcode:
   ```bash
   open OshiReader.xcodeproj
   ```
4. Select your target device or simulator (e.g., iPhone 15) and press **⌘R** (Run) to build and start the app.

---

## 🛠️ Configuration & Environment Variables

### Backend Configuration (`backend/.env`)

Configure the backend variables inside the `.env` file at the root of the `backend/` directory:

| Environment Variable | Default Value | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./otterpia.db` | SQLAlchemy database URL (e.g., `postgresql://...` for production) |
| `YOUTUBE_API_KEY` | *None* | Required for Youtube Connector to fetch channel uploads and metadata |
| `POLL_INTERVAL_MINUTES` | `15` | Polling frequency for the background scrapers and scheduler |

### Web Frontend / Mobile Configuration

* **Frontend API Endpoint**: Configured directly in API helper files (`frontend/src/api.ts`).
* **Mobile API Endpoint**: The backend endpoint defaults to the Railway deployment production server. This is customizable in `mobile/src/config.ts`:
  ```typescript
  export const API_BASE = 'http://127.0.0.1:8000' // change for local testing
  ```

---

## 🔌 Supported Connectors & Platforms

OshiReader formats cards dynamically and tracks content across:

| Platform | Indicator | Collection Source / API |
|---|:---:|---|
| **YouTube** | 📹 | YouTube Data API (v3) / RSS feeds |
| **TVer** | 📺 | TVer Program API & schedules |
| **5channel (5ch)** | 💬 | Thread index scraping |
| **Girls Channel** | 👭 | Forum comments and topics parsing |
| **Togetter** | 🐧 | Curation thread scraper |
| **Note** | 📝 | Creator RSS & feed aggregator |
| **Twitter / X** | 🐦 | Twitter v2 API Integration (requires Bearer Token) |
| **RSS Feeds** | 📰 | Multi-purpose feedparser integration |
