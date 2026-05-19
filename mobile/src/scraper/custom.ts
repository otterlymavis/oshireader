import type { FeedItem } from '../localDb'
import { getCustomUrls } from '../localDb'
import { fetchHtml, now } from './utils'

function extractTitle(html: string, fallback: string): string {
  const m = html.match(/<title[^>]*>([^<]{1,200})<\/title>/i)
  return m ? m[1].trim().replace(/\s+/g, ' ') : fallback
}

function extractDescription(html: string): string {
  const m = html.match(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']{1,300})["']/i)
    ?? html.match(/<meta[^>]+content=["']([^"']{1,300})["'][^>]+name=["']description["']/i)
  return m ? m[1].trim() : ''
}

export async function scrapeCustomUrls(): Promise<FeedItem[]> {
  const urls = await getCustomUrls()
  if (urls.length === 0) return []

  const results: FeedItem[] = []

  await Promise.allSettled(
    urls.map(async entry => {
      try {
        const html = await fetchHtml(entry.url)
        const title = extractTitle(html, entry.title ?? entry.url)
        const description = extractDescription(html)
        results.push({
          id: `custom:${entry.id}`,
          platform: 'custom',
          url: entry.url,
          title,
          content_text: description || null,
          author: null,
          thumbnail_url: null,
          media_type: 'article',
          published_at: entry.added_at,
          watch_term_keyword: '',
          fetched_at: now(),
        })
        console.log(`[custom] fetched: ${title.slice(0, 40)}`)
      } catch (e) {
        results.push({
          id: `custom:${entry.id}`,
          platform: 'custom',
          url: entry.url,
          title: entry.title ?? entry.url,
          content_text: null,
          author: null,
          thumbnail_url: null,
          media_type: 'article',
          published_at: entry.added_at,
          watch_term_keyword: '',
          fetched_at: now(),
        })
        console.log(`[custom] failed (using stored title):`, e)
      }
    }),
  )

  return results
}
