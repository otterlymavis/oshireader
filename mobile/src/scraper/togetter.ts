import type { FeedItem } from '../localDb'
import { fetchHtml, extractLinks, now } from './utils'

export async function scrapeTogetter(keyword: string, mode: string): Promise<FeedItem[]> {
  if (mode === 'media_only') return []
  try {
    const html = await fetchHtml('https://togetter.com/search', { q: keyword })
    return extractLinks(html, /togetter\.com\/li\//).map(({ href, text }) => {
      const itemId = href.split('/li/')[1]?.split('/')[0] ?? href.slice(-10)
      return {
        id: `togetter:${itemId}`,
        platform: 'togetter',
        url: href,
        title: text || null,
        content_text: null,
        author: null,
        thumbnail_url: null,
        media_type: 'article',
        published_at: now(),
        watch_term_keyword: keyword,
        fetched_at: now(),
      }
    })
  } catch { return [] }
}
