import type { FeedItem } from '../localDb'
import { fetchHtml, now } from './utils'

async function fromSnapshotApi(keyword: string, k: string): Promise<FeedItem[]> {
  const params = new URLSearchParams({
    q: keyword,
    targets: 'title,description,tags',
    fields: 'contentId,title,description,thumbnailUrl,startTime',
    _sort: '-startTime',
    _limit: '20',
    _context: 'Otterpia',
  })
  const res = await fetch(
    `https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search?${params}`,
    { headers: { 'Accept': 'application/json', 'User-Agent': 'Otterpia/1.0' } },
  )
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json() as { data?: any[] }
  return (data.data ?? []).map(v => ({
    id: `niconico:${v.contentId}`,
    platform: 'niconico',
    url: `https://nicovideo.jp/watch/${v.contentId}`,
    title: v.title ?? null,
    content_text: v.description ?? null,
    author: null,
    thumbnail_url: v.thumbnailUrl ?? null,
    media_type: 'video',
    published_at: now(),
    watch_term_keyword: k,
    fetched_at: now(),
  }))
}

async function fromHtmlSearch(keyword: string, k: string): Promise<FeedItem[]> {
  const html = await fetchHtml(
    `https://www.nicovideo.jp/search/${encodeURIComponent(keyword)}`,
    { sort: 'f', order: 'd' },
  )
  const results: FeedItem[] = []
  const seen = new Set<string>()
  const re = /href="\/watch\/((?:sm|nm|so)\d+)"/g
  let m: RegExpExecArray | null
  while (results.length < 20 && (m = re.exec(html)) !== null) {
    const cid = m[1]
    if (seen.has(cid)) continue
    seen.add(cid)
    // grab title text from a nearby aria-label or title attribute
    const nearby = html.slice(Math.max(0, m.index - 100), m.index + 400)
    const titleM = nearby.match(/aria-label="([^"]{4,})"/) ?? nearby.match(/title="([^"]{4,})"/)
    results.push({
      id: `niconico:${cid}`,
      platform: 'niconico',
      url: `https://nicovideo.jp/watch/${cid}`,
      title: titleM?.[1]?.trim() ?? null,
      content_text: null, author: null, thumbnail_url: null,
      media_type: 'video', published_at: now(),
      watch_term_keyword: k, fetched_at: now(),
    })
  }
  return results
}

export async function scrapeNicoNico(keyword: string, _mode: string): Promise<FeedItem[]> {
  try {
    const items = await fromSnapshotApi(keyword, keyword)
    console.log(`[niconico] snapshot=${items.length}`)
    if (items.length > 0) return items
  } catch (e) {
    console.log(`[niconico] snapshot failed:`, e)
  }
  try {
    const items = await fromHtmlSearch(keyword, keyword)
    console.log(`[niconico] html=${items.length}`)
    return items
  } catch (e) {
    console.log(`[niconico] html failed:`, e)
    return []
  }
}
