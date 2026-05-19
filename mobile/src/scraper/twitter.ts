import type { FeedItem } from '../localDb'
import { fetchDDGLite, fetchBingDesktop, extractBingWebLinks, now } from './utils'

function tweetId(url: string): string {
  return url.match(/\/status\/(\d+)/)?.[1] ?? encodeURIComponent(url).slice(-20)
}

function normalizeUrl(url: string): string {
  const clean = url.replace('twitter.com', 'x.com').split('?')[0]
  const match = clean.match(/^https?:\/\/(?:x\.com|twitter\.com)\/([^/]+)\/status\/(\d+)/i)
  if (!match) return clean
  const [, user, id] = match
  return `https://fixupx.com/${user}/status/${id}`
}

function isTweetUrl(url: string): boolean {
  return /(?:x\.com|twitter\.com)\/[^/]+\/status\/\d+/.test(url)
}

function searchItem(keyword: string, mode: string): FeedItem {
  const query = mode === 'media_only' ? `${keyword} filter:media` : keyword
  const encodedQuery = encodeURIComponent(query)
  return {
    id: `twitter:search:${encodedQuery}`,
    platform: 'twitter',
    url: `https://twitterviewer.net/search?q=${encodedQuery}`,
    title: `Twitter search: ${keyword}`,
    content_text: keyword,
    author: null,
    thumbnail_url: null,
    media_type: 'article',
    published_at: now(),
    watch_term_keyword: keyword,
    fetched_at: now(),
  }
}

export async function scrapeTwitter(keyword: string, mode: string): Promise<FeedItem[]> {
  const seen = new Set<string>()
  const results: FeedItem[] = []

  const process = (links: Array<{ url: string; title: string }>) => {
    for (const { url, title } of links) {
      const norm = normalizeUrl(url)
      const id = tweetId(norm)
      if (!id || seen.has(id)) continue
      seen.add(id)
      results.push({
        id: `twitter:${id}`,
        platform: 'twitter',
        url: norm,
        title: title || null,
        content_text: null, author: null, thumbnail_url: null,
        media_type: 'article', published_at: now(),
        watch_term_keyword: keyword, fetched_at: now(),
      })
    }
  }

  // Try DDG first, then Bing
  for (const query of [
    `site:x.com inurl:/status/ ${keyword}`,
    `site:x.com ${keyword}`,
    `site:twitter.com ${keyword}`,
  ]) {
    if (results.length >= 10) break
    try {
      let html: string
      try {
        html = await fetchDDGLite(query)
      } catch {
        html = await fetchBingDesktop(query)
      }
      const links = extractBingWebLinks(html, isTweetUrl)
      console.log(`[twitter:ddg] query="${query}" links=${links.length}`)
      process(links)
    } catch (e) {
      console.log('[twitter:ddg] failed:', e)
    }
  }

  console.log(`[twitter] total=${results.length}`)
  return results
}
