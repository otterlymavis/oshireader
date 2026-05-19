import type { SQLiteDatabase } from 'expo-sqlite'

export async function initDb(db: SQLiteDatabase): Promise<void> {
  await db.execAsync(`
    PRAGMA journal_mode = WAL;
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS watch_terms (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      keyword         TEXT    NOT NULL,
      collection_mode TEXT    NOT NULL DEFAULT 'all_info',
      is_active       INTEGER NOT NULL DEFAULT 1,
      created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS feed_items (
      id           TEXT PRIMARY KEY,
      platform     TEXT NOT NULL,
      item_id      TEXT NOT NULL,
      url          TEXT NOT NULL,
      published_at TEXT NOT NULL,
      author       TEXT,
      title        TEXT,
      content_text TEXT,
      media_type   TEXT,
      thumbnail_url TEXT,
      fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS matches (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      watch_term_id INTEGER NOT NULL REFERENCES watch_terms(id) ON DELETE CASCADE,
      feed_item_id  TEXT    NOT NULL REFERENCES feed_items(id),
      created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
      UNIQUE(watch_term_id, feed_item_id)
    );

    CREATE INDEX IF NOT EXISTS idx_feed_published ON feed_items(published_at DESC);
    CREATE INDEX IF NOT EXISTS idx_feed_platform  ON feed_items(platform);
    CREATE INDEX IF NOT EXISTS idx_matches_term   ON matches(watch_term_id);
  `)
}
