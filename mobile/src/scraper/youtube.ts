import AsyncStorage from '@react-native-async-storage/async-storage'
import type { FeedItem } from '../localDb'
import { DESKTOP_UA, now } from './utils'
import { fetchYouTube } from '../connectors/youtube'

function unescape(s: string): string {
  return s
    .replace(/\\n/g, ' ')
    .replace(/\\"/g, '"')
    .replace(/\\'/g, "'")
    .replace(/\\\\/g, '\\')
    .replace(/\\u([\da-fA-F]{4})/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/ - YouTube$/, '')
    .trim()
}

// Parse YouTube's relative-time strings ("3 months ago", "2週間前", etc.) into ISO dates.
function parseYouTubeDate(seg: string): string {
  const raw =
    seg.match(/"publishedTimeText":\{"simpleText":"([^"]+)"/)?.[1] ??
    seg.match(/"publishedTimeText":\{"runs":\[\{"text":"([^"]+)"/)?.[1] ??
    ''
  if (!raw) return now()
  const m = raw.match(/(\d+)\s*(second|minute|hour|day|week|month|year|秒|分|時間|日|週間?|ヶ月|年)/i)
  if (!m) return now()
  const n = parseInt(m[1], 10)
  const u = m[2].toLowerCase()
  let ms = 0
  if (u.startsWith('sec') || u === '秒') ms = n * 1_000
  else if (u.startsWith('min') || u === '分') ms = n * 60_000
  else if (u.startsWith('hour') || u === '時間') ms = n * 3_600_000
  else if (u.startsWith('day') || u === '日') ms = n * 86_400_000
  else if (u.startsWith('week') || u === '週') ms = n * 7 * 86_400_000
  else if (u.startsWith('month') || u === 'ヶ月') ms = n * 30 * 86_400_000
  else if (u.startsWith('year') || u === '年') ms = n * 365 * 86_400_000
  return ms > 0 ? new Date(Date.now() - ms).toISOString() : now()
}

export async function scrapeYouTube(keyword: string, mode: string): Promise<FeedItem[]> {
  // Use YouTube Data API if the user has configured a key
  const apiKey = (await AsyncStorage.getItem('@otterpia/yt_key'))?.trim() ?? ''
  if (apiKey) {
    try {
      const items = await fetchYouTube(keyword, mode, apiKey)
      if (items.length > 0) {
        return items.map(item => ({
          id: `youtube:${item.item_id}`,
          platform: 'youtube',
          url: item.url,
          title: item.title ?? null,
          content_text: item.content_text ?? null,
          author: item.author ?? null,
          thumbnail_url: item.thumbnail_url ?? null,
          media_type: item.media_type,
          published_at: item.published_at.toISOString(),
          watch_term_keyword: keyword,
          fetched_at: now(),
        }))
      }
    } catch (e) {
      console.log('[youtube:api] failed, falling back to scrape:', e)
    }
  }

  const results: FeedItem[] = []
  const seen = new Set<string>()
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15_000)
    let html: string
    try {
      const res = await fetch(
        `https://www.youtube.com/results?search_query=${encodeURIComponent(keyword)}`,
        {
          signal: controller.signal,
          headers: {
            'User-Agent': DESKTOP_UA,
            'Accept-Language': 'ja,en;q=0.9',
            'Accept': 'text/html,*/*',
          },
        },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      html = await res.text()
    } finally {
      clearTimeout(timer)
    }

    console.log(`[youtube] html=${html.length}`)

    // Anchor on "videoRenderer":{"videoId":"..." — this is always the start of a
    // real video card, unlike bare "videoId" which appears dozens of times per card
    // (in watch endpoints, thumbnails, related items, etc.)
    const re = /"videoRenderer":\{"videoId":"([A-Za-z0-9_-]{11})"/g
    let m: RegExpExecArray | null
    while (results.length < 20 && (m = re.exec(html)) !== null) {
      const vid = m[1]
      if (seen.has(vid)) continue
      seen.add(vid)

      // Title is always in the first ~800 chars of the videoRenderer object.
      // Key fix: don't require "}" after the captured text — YouTube's JSON has
      // more fields after "text":"TITLE" so it ends with "," not "}".
      const seg = html.slice(m.index, Math.min(html.length, m.index + 1200))

      let rawTitle: string | null =
        // Primary: title.runs[0].text
        seg.match(/"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"/)
          ?.[1] ??
        // Fallback: simpleText (used in some layouts)
        seg.match(/"title":\{"simpleText":"((?:[^"\\]|\\.)*)"/)
          ?.[1] ??
        // Fallback: accessibility label attached to title (strip channel/stats suffix)
        seg.match(/"title":\{"accessibility":\{"accessibilityData":\{"label":"((?:[^"\\]|\\.)*)"/)
          ?.[1]
          ?.replace(/ - (?:by |\d[\d,.]*\s*(?:回|views?|再生)).*/i, '')
          .trim() ??
        null

      results.push({
        id: `youtube:${vid}`,
        platform: 'youtube',
        url: `https://www.youtube.com/watch?v=${vid}`,
        title: rawTitle ? unescape(rawTitle) : null,
        content_text: null, author: null, thumbnail_url: null,
        media_type: 'video', published_at: parseYouTubeDate(seg),
        watch_term_keyword: keyword, fetched_at: now(),
      })
    }

    console.log(`[youtube] found=${results.length}`)
  } catch (e) {
    console.log(`[youtube] failed:`, e)
  }
  return results
}
