import type { SQLiteDatabase } from 'expo-sqlite'
import { getActiveWatchTerms } from '../db/watchTerms'
import { storeItems } from '../db/feed'
import { fetchYouTube } from '../connectors/youtube'
import type { Settings } from '../hooks/useSettings'

export async function pollAll(
  db: SQLiteDatabase,
  settings: Settings,
): Promise<{ total: number; errors: string[] }> {
  const terms = await getActiveWatchTerms(db)
  let total = 0
  const errors: string[] = []

  for (const term of terms) {
    if (settings.youtubeApiKey) {
      try {
        const items = await fetchYouTube(term.keyword, term.collection_mode, settings.youtubeApiKey)
        total += await storeItems(db, term.id, items)
      } catch (e) {
        errors.push(`YouTube: ${(e as Error).message}`)
      }
    }
  }

  return { total, errors }
}
