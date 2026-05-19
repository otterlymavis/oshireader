import type { FeedItem } from '../localDb'
import { fetchXml, fetchBingNewsRSS, parseRssItems, textMatchesKeyword, now } from './utils'

// Static RSS feeds — keyword-filtered after fetch
const RSS_FEEDS = [
  { source: 'nhk', url: 'https://www3.nhk.or.jp/rss/news/cat7.xml' },
]

function cleanNewsTitle(title: string): string {
  return title
    .replace(/\s*[-|]\s*Yahoo!\u30cb\u30e5\u30fc\u30b9\s*$/i, '')
    .replace(/\s*\([^)]*\u30cb\u30e5\u30fc\u30b9\)\s*[-|]\s*Yahoo!\u30cb\u30e5\u30fc\u30b9\s*$/i, '')
    .replace(/\s*[-|]\s*(?:Bing|Google)\s*$/i, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function makeItem(
  source: string, url: string, title: string,
  description: string, pubDate: string, keyword: string,
): FeedItem {
  const displayTitle = cleanNewsTitle(title) || cleanNewsTitle(description) || url
  return {
    id: `news:${source}:${encodeURIComponent(url).slice(0, 80)}`,
    platform: 'news',
    url,
    title: displayTitle,
    content_text: description || null,
    author: null, thumbnail_url: null,
    media_type: 'article',
    published_at: pubDate,
    watch_term_keyword: keyword,
    fetched_at: now(),
  }
}

function newsMatches(title: string, description: string, keyword: string): boolean {
  return textMatchesKeyword(`${title} ${description}`, keyword)
}

export async function scrapeRSS(keyword: string, _mode: string): Promise<FeedItem[]> {
  const kw = keyword.toLowerCase()
  const results: FeedItem[] = []
  const seen = new Set<string>()

  const addItem = (
    source: string,
    url: string,
    title: string,
    description: string,
    pubDate: string,
  ) => {
    if (!url || seen.has(url)) return
    seen.add(url)
    results.push(makeItem(source, url, title, description, pubDate, keyword))
  }

  await Promise.allSettled([
    // ── Bing News RSS (structured feed, much more reliable than HTML) ─────────
    (async () => {
      try {
        const queries = [keyword, `"${keyword}"`]
        let fetched = 0
        let used = 0
        for (const query of queries) {
          const items = await fetchBingNewsRSS(query)
          fetched += items.length
          for (const { url, title, pubDate, description } of items.slice(0, 30)) {
            if (!newsMatches(title, description, keyword)) continue
            addItem('bing', url, title, description, pubDate)
            used++
          }
        }
        console.log(`[RSS:bing] items=${fetched} used=${used}`)
      } catch (e) {
        console.log('[RSS:bing] failed:', e)
      }
    })(),

    // ── Google News RSS ───────────────────────────────────────────────────────
    (async () => {
      try {
        const xml = await fetchXml(
          `https://news.google.com/rss/search?q=${encodeURIComponent(keyword)}&hl=ja&gl=JP&ceid=JP%3Aja`,
        )
        const entries = parseRssItems(xml)
        let count = 0
        for (const item of entries.slice(0, 40)) {
          // Google News links are redirect URLs — they work fine in WebView
          if (!item.link) continue
          if (!newsMatches(item.title, item.description, keyword)) continue
          let pubDate = now()
          if (item.pubDate) {
            const d = new Date(item.pubDate)
            if (!isNaN(d.getTime())) pubDate = d.toISOString()
          }
          addItem('gnews', item.link, item.title, item.description, pubDate)
          count++
          if (count >= 20) break
        }
        console.log(`[RSS:gnews] entries=${entries.length} used=${count}`)
      } catch (e) {
        console.log('[RSS:gnews] failed:', e)
      }
    })(),

    // ── Static RSS feeds ─────────────────────────────────────────────────────
    ...RSS_FEEDS.map(async ({ source, url }) => {
      try {
        const xml = await fetchXml(url)
        const entries = parseRssItems(xml)
        let matched = 0
        for (const item of entries) {
          if (
            !item.title.toLowerCase().includes(kw) &&
            !item.description.toLowerCase().includes(kw) &&
            !newsMatches(item.title, item.description, keyword)
          ) continue
          matched++
          let pubDate = now()
          if (item.pubDate) {
            const d = new Date(item.pubDate)
            if (!isNaN(d.getTime())) pubDate = d.toISOString()
          }
          addItem(source, item.link, item.title, item.description, pubDate)
        }
        console.log(`[RSS:${source}] entries=${entries.length} matched=${matched}`)
      } catch (e) {
        console.log(`[RSS:${source}] failed:`, e)
      }
    }),
  ])

  return results
}
