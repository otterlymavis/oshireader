import type { SQLiteDatabase } from 'expo-sqlite'

export interface WatchTerm {
  id: number
  keyword: string
  collection_mode: 'all_info' | 'media_only'
  is_active: number
  created_at: string
}

export const getWatchTerms = (db: SQLiteDatabase): Promise<WatchTerm[]> =>
  db.getAllAsync<WatchTerm>('SELECT * FROM watch_terms ORDER BY created_at DESC')

export const getActiveWatchTerms = (db: SQLiteDatabase): Promise<WatchTerm[]> =>
  db.getAllAsync<WatchTerm>('SELECT * FROM watch_terms WHERE is_active = 1')

export async function addWatchTerm(
  db: SQLiteDatabase,
  keyword: string,
  mode: 'all_info' | 'media_only',
): Promise<WatchTerm> {
  const { lastInsertRowId } = await db.runAsync(
    'INSERT INTO watch_terms (keyword, collection_mode) VALUES (?, ?)',
    keyword,
    mode,
  )
  return (await db.getFirstAsync<WatchTerm>('SELECT * FROM watch_terms WHERE id = ?', lastInsertRowId))!
}

export const toggleWatchTerm = (db: SQLiteDatabase, id: number, active: boolean): Promise<void> =>
  db.runAsync('UPDATE watch_terms SET is_active = ? WHERE id = ?', active ? 1 : 0, id).then(() => {})

export const removeWatchTerm = (db: SQLiteDatabase, id: number): Promise<void> =>
  db.runAsync('DELETE FROM watch_terms WHERE id = ?', id).then(() => {})
