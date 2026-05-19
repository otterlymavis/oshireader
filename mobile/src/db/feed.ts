import type { SQLiteDatabase } from 'expo-sqlite'
import type { SourceItem } from '../connectors/base'

export interface FeedRow {
  match_id: number
  watch_term_id: number
  watch_term_keyword: string
  matched_at: string
  item_id: string
  platform: string
  url: string
  published_at: string
  author: string | null
  title: string | null
  content_text: string | null
  media_type: string | null
  thumbnail_url: string | null
}

export async function getFeed(
  db: SQLiteDatabase,
  opts: { termId?: number | null; platform?: string | null; limit?: number },
): Promise<FeedRow[]> {
  const conds: string[] = []
  const args: (string | number)[] = []

  if (opts.termId != null) { conds.push('m.watch_term_id = ?'); args.push(opts.termId) }
  if (opts.platform)       { conds.push('fi.platform = ?');      args.push(opts.platform) }

  const where = conds.length ? `WHERE ${conds.join(' AND ')}` : ''
  args.push(opts.limit ?? 50)

  return db.getAllAsync<FeedRow>(
    `SELECT m.id AS match_id, m.watch_term_id, m.created_at AS matched_at,
            wt.keyword AS watch_term_keyword,
            fi.id AS item_id, fi.platform, fi.url, fi.published_at,
            fi.author, fi.title, fi.content_text, fi.media_type, fi.thumbnail_url
     FROM matches m
     JOIN feed_items fi ON m.feed_item_id = fi.id
     JOIN watch_terms wt ON m.watch_term_id = wt.id
     ${where}
     ORDER BY fi.published_at DESC
     LIMIT ?`,
    ...args,
  )
}

export async function storeItems(
  db: SQLiteDatabase,
  termId: number,
  items: SourceItem[],
): Promise<number> {
  let newCount = 0
  await db.withTransactionAsync(async () => {
    for (const item of items) {
      const compositeId = `${item.platform}:${item.item_id}`
      await db.runAsync(
        `INSERT OR IGNORE INTO feed_items
           (id, platform, item_id, url, published_at, media_type, author, title, content_text, thumbnail_url)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        compositeId, item.platform, item.item_id, item.url,
        item.published_at.toISOString(), item.media_type,
        item.author ?? null, item.title ?? null, item.content_text ?? null, item.thumbnail_url ?? null,
      )
      const { changes } = await db.runAsync(
        'INSERT OR IGNORE INTO matches (watch_term_id, feed_item_id) VALUES (?, ?)',
        termId, compositeId,
      )
      if (changes > 0) newCount++
    }
  })
  return newCount
}
