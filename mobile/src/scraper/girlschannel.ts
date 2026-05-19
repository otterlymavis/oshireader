import type { FeedItem } from '../localDb'
import { fetchBingDesktop, extractBingWebLinks, fetchHtmlRef, now } from './utils'

const GC_BASE = 'https://girlschannel.net'

function gcId(url: string): string {
  return url.match(/\/topics\/(\d+)/)?.[1] ?? encodeURIComponent(url).slice(-20)
}

async function fetchTopicTitle(topicId: string): Promise<string | null> {
  try {
    const html = await fetchHtmlRef(`${GC_BASE}/topics/${topicId}/`, `${GC_BASE}/`)
    // <title> is the most reliable source; strip the " - Girls Channel" suffix
    const m = html.match(/<title[^>]*>([^<]+)<\/title>/i)
    if (!m) return null
    return m[1]
      .replace(/\s*[-–|｜]\s*Girls?\s*Channel.*/i, '')
      .replace(/\s*[-–|｜]\s*ガールズちゃんねる.*/i, '')
      .trim() || null
  } catch {
    return null
  }
}

async function fromBingWithTitles(keyword: string): Promise<FeedItem[]> {
  const html = await fetchBingDesktop(`site:girlschannel.net/topics ${keyword}`)
  const links = extractBingWebLinks(html, url => /girlschannel\.net\/topics\/\d+/.test(url))
  console.log(`[GC:bing] links=${links.length}`)
  if (links.length === 0) return []

  // Fetch real titles from each topic page in parallel (up to 10)
  const subset = links.slice(0, 10)
  const titles = await Promise.allSettled(
    subset.map(({ url }) => fetchTopicTitle(gcId(url))),
  )

  return subset.map(({ url }, i) => {
    const topicId = gcId(url)
    const titleResult = titles[i]
    const title = titleResult.status === 'fulfilled' && titleResult.value
      ? titleResult.value
      : links[i].title || null
    return {
      id: `girlschannel:${topicId}`,
      platform: 'girlschannel',
      url: `${GC_BASE}/topics/${topicId}/`,
      title,
      content_text: null, author: null, thumbnail_url: null,
      media_type: 'article', published_at: now(),
      watch_term_keyword: keyword, fetched_at: now(),
    }
  })
}

async function fromHtmlWithTitles(keyword: string): Promise<FeedItem[]> {
  const url = `${GC_BASE}/topics/search/?q=${encodeURIComponent(keyword)}`
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15_000)
  let html = ''
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,*/*',
        'Accept-Language': 'ja,en;q=0.9',
        'Referer': GC_BASE + '/',
      },
    })
    if (!res.ok) return []
    html = await res.text()
  } finally {
    clearTimeout(timer)
  }

  // Collect unique topic IDs from any /topics/ID/ link on the page
  const seen = new Set<string>()
  const topicIds: string[] = []
  const linkRe = /\/topics\/(\d+)\//g
  let m: RegExpExecArray | null
  while (topicIds.length < 10 && (m = linkRe.exec(html)) !== null) {
    const id = m[1]
    if (!seen.has(id)) { seen.add(id); topicIds.push(id) }
  }
  console.log(`[GC:html] found ${topicIds.length} topic IDs`)
  if (topicIds.length === 0) return []

  // Fetch real titles in parallel
  const titles = await Promise.allSettled(topicIds.map(fetchTopicTitle))

  return topicIds.map((id, i) => {
    const titleResult = titles[i]
    const title = titleResult.status === 'fulfilled' ? titleResult.value : null
    return {
      id: `girlschannel:${id}`,
      platform: 'girlschannel',
      url: `${GC_BASE}/topics/${id}/`,
      title,
      content_text: null, author: null, thumbnail_url: null,
      media_type: 'article', published_at: now(),
      watch_term_keyword: keyword, fetched_at: now(),
    }
  }).filter(item => item.title && item.title.length > 2)
}

export async function scrapeGirlsChannel(keyword: string, mode: string): Promise<FeedItem[]> {
  if (mode === 'media_only') return []
  try {
    const items = await fromBingWithTitles(keyword)
    if (items.length > 0) return items
  } catch (e) {
    console.log('[GC:bing] failed:', e)
  }
  try {
    return await fromHtmlWithTitles(keyword)
  } catch (e) {
    console.log('[GC:html] failed:', e)
    return []
  }
}
