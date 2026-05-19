import type { FeedItem } from '../localDb'
import { fetchDDGLite, fetchBingDesktop, fetchHtml, fetchBingNewsRSS, fetchXml, parseRssItems, extractBingWebLinks, extractNextData, DESKTOP_UA, now } from './utils'

function noteId(url: string): string {
  return url.match(/\/n\/([A-Za-z0-9]+)/)?.[1] ?? encodeURIComponent(url).slice(-20)
}

async function fromNoteHashtagRSS(keyword: string): Promise<FeedItem[]> {
  // note.com publishes RSS for each hashtag — no auth, no bot detection
  const xml = await fetchXml(`https://note.com/hashtag/${encodeURIComponent(keyword)}/rss`)
  const items = parseRssItems(xml)
  console.log(`[note:hashtag] items=${items.length}`)
  return items
    .filter(i => i.link)
    .map(i => {
      return {
        id: `note:${noteId(i.link)}`,
        platform: 'note', url: i.link,
        title: i.title || null,
        content_text: null, author: null, thumbnail_url: null,
        media_type: 'article', published_at: now(),
        watch_term_keyword: keyword, fetched_at: now(),
      }
    })
}

async function fromNoteApi(keyword: string): Promise<FeedItem[]> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15_000)
  let data: unknown
  try {
    const res = await fetch(
      `https://note.com/api/v3/searches?q=${encodeURIComponent(keyword)}&context=note&size=20`,
      {
        signal: controller.signal,
        headers: {
          'User-Agent': DESKTOP_UA,
          'Accept': 'application/json',
          'Referer': 'https://note.com/',
          'Accept-Language': 'ja,en;q=0.9',
        },
      },
    )
    if (!res.ok) {
      console.log(`[note:api] HTTP ${res.status}`)
      return []
    }
    data = await res.json()
  } finally {
    clearTimeout(timer)
  }

  const notes: unknown[] =
    (data as any)?.data?.notes ??
    (data as any)?.data?.sections?.flatMap((s: any) => s.noteList ?? []) ??
    (data as any)?.notes ??
    []
  const results: FeedItem[] = []
  for (const n of notes as any[]) {
    const rawSlug: string = n.slug ?? n.id?.toString() ?? ''
    if (!rawSlug) continue
    // note.com slugs already start with 'n' (e.g. "n1a2b3c4"); avoid double-n prefix
    const slug = rawSlug.startsWith('n') ? rawSlug : `n${rawSlug}`
    const noteUrl: string =
      n.noteUrl ??
      (n.user?.urlname ? `https://note.com/${n.user.urlname}/n/${slug}` : `https://note.com/n/${slug}`)
    results.push({
      id: `note:${slug}`,
      platform: 'note',
      url: noteUrl,
      title: n.name ?? null,
      content_text: null,
      author: n.user?.nickname ?? n.user?.urlname ?? null,
      thumbnail_url: n.eyecatch ?? null,
      media_type: 'article',
      published_at: n.publishAt ? new Date(n.publishAt).toISOString() : now(),
      watch_term_keyword: keyword,
      fetched_at: now(),
    })
  }
  console.log(`[note:api] results=${results.length}`)
  return results
}

async function fromBing(keyword: string): Promise<FeedItem[]> {
  let html: string
  try {
    html = await fetchDDGLite(`site:note.com ${keyword}`)
  } catch {
    html = await fetchBingDesktop(`site:note.com ${keyword}`)
  }
  const links = extractBingWebLinks(
    html,
    url => /note\.com\/[^/]+\/n\/[A-Za-z0-9]+/.test(url),
  )
  console.log(`[note:bing] links=${links.length}`)
  return links.map(({ url, title }) => ({
    id: `note:${noteId(url)}`,
    platform: 'note', url,
    title: title || null,
    content_text: null, author: null, thumbnail_url: null,
    media_type: 'article', published_at: now(),
    watch_term_keyword: keyword, fetched_at: now(),
  }))
}

async function fromNoteSearch(keyword: string): Promise<FeedItem[]> {
  const html = await fetchHtml(
    `https://note.com/search?q=${encodeURIComponent(keyword)}&context=note`,
  )

  // Try __NEXT_DATA__ first (reliable structured data from Next.js)
  const nextData = extractNextData(html)
  if (nextData) {
    const notes: any[] =
      (nextData as any)?.props?.pageProps?.notes ??
      (nextData as any)?.props?.pageProps?.searchResult?.note_contents ??
      (nextData as any)?.props?.pageProps?.initialState?.note?.searchResult?.notes ??
      []
    if (notes.length > 0) {
      console.log(`[note:nextdata] results=${notes.length}`)
      return notes.map((n: any) => {
        const slug: string = n.slug ?? n.id?.toString() ?? ''
        const user = n.user?.urlname ?? n.userUrlname ?? ''
        const url = n.noteUrl ?? (user ? `https://note.com/${user}/n/${slug}` : `https://note.com/n/${slug}`)
        return {
          id: `note:${slug || noteId(url)}`, platform: 'note', url,
          title: n.name ?? null,
          content_text: null, author: n.user?.nickname ?? null, thumbnail_url: null,
          media_type: 'article',
          published_at: n.publishAt ? new Date(n.publishAt).toISOString() : now(),
          watch_term_keyword: keyword, fetched_at: now(),
        }
      })
    }
  }

  // Fallback: regex over HTML
  const results: FeedItem[] = []
  const seen = new Set<string>()
  const re = /href="(https?:\/\/note\.com\/[^/?"#]+\/n\/[A-Za-z0-9]+)"/gi
  let m: RegExpExecArray | null
  while (results.length < 20 && (m = re.exec(html)) !== null) {
    const url = m[1]
    const id = noteId(url)
    if (seen.has(id)) continue
    seen.add(id)
    const nearby = html.slice(Math.max(0, m.index - 20), m.index + 400)
    const titleM = nearby.match(/"name":"((?:[^"\\]|\\.)*)"/)?.[1]
      ?? nearby.match(/alt="([^"]{4,})"/)?.[1]
    results.push({
      id: `note:${id}`, platform: 'note', url,
      title: titleM?.trim() ?? null,
      content_text: null, author: null, thumbnail_url: null,
      media_type: 'article', published_at: now(),
      watch_term_keyword: keyword, fetched_at: now(),
    })
  }
  console.log(`[note:search] results=${results.length}`)
  return results
}

async function fromNoteRSS(keyword: string): Promise<FeedItem[]> {
  const rssItems = await fetchBingNewsRSS(keyword)
  const matched = rssItems.filter(i => /note\.com\/[^/]+\/n\/[A-Za-z0-9]+/.test(i.url))
  console.log(`[note:rss] matched=${matched.length}`)
  return matched.map(i => ({
    id: `note:${noteId(i.url)}`, platform: 'note', url: i.url,
    title: i.title || null,
    content_text: null, author: null, thumbnail_url: null,
    media_type: 'article', published_at: i.pubDate,
    watch_term_keyword: keyword, fetched_at: now(),
  }))
}

function noteSearchItem(keyword: string): FeedItem {
  return {
    id: `note:search:${encodeURIComponent(keyword)}`,
    platform: 'note',
    url: `https://note.com/search?q=${encodeURIComponent(keyword)}&context=note`,
    title: `note search: ${keyword}`,
    content_text: keyword,
    author: null,
    thumbnail_url: null,
    media_type: 'article',
    published_at: now(),
    watch_term_keyword: keyword,
    fetched_at: now(),
  }
}

export async function scrapeNote(keyword: string, mode: string): Promise<FeedItem[]> {
  for (const [label, fn] of [
    ['hashtag', () => fromNoteHashtagRSS(keyword)],
    ['api',     () => fromNoteApi(keyword)],
    ['search',  () => fromNoteSearch(keyword)],
    ['rss',     () => fromNoteRSS(keyword)],
    ['bing',    () => fromBing(keyword)],
  ] as [string, () => Promise<FeedItem[]>][]) {
    try {
      const items = await fn()
      if (items.length > 0) return items.slice(0, 20)
    } catch (e) {
      console.log(`[note:${label}] failed:`, e)
    }
  }
  return []
}
