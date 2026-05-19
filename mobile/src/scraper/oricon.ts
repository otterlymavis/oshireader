import type { FeedItem } from '../localDb'
import {
  fetchDDGLite,
  fetchBingDesktop,
  fetchBingNewsRSS,
  fetchGoogleSearch,
  fetchJinaReader,
  extractBingWebLinks,
  parseRssItems,
  DESKTOP_UA,
  decodeHtml,
  textMatchesKeyword,
  now,
} from './utils'

const isOriconUrl = (url: string) =>
  /oricon\.co\.jp\/(?:news|article)\/\d/.test(url)

function oriconArticleUrl(url: string): string {
  const secure = url.replace(/^http:/i, 'https:').split('#')[0]
  const id = secure.match(/\/(?:news|article)\/(\d+)/)?.[1]
  return id ? `https://www.oricon.co.jp/news/${id}/full/` : secure.split('?')[0]
}

async function fetchOriconHtml(url: string): Promise<string> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15_000)
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: {
        'User-Agent': DESKTOP_UA,
        'Accept': 'text/html,*/*',
        'Accept-Language': 'ja,en;q=0.9',
        'Referer': 'https://www.oricon.co.jp/',
      },
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const buffer = await res.arrayBuffer()

    // 1. Check Content-Type header charset
    const contentType = res.headers.get('content-type') ?? ''
    const ctCharset = contentType.match(/charset=([^\s;]+)/i)?.[1] ?? ''
    if (ctCharset) {
      if (/shift.?jis|sjis|windows.?31j/i.test(ctCharset)) {
        for (const enc of ['shift-jis', 'shift_jis', 'windows-31j']) {
          try { return new TextDecoder(enc).decode(buffer) } catch {}
        }
      }
      try { return new TextDecoder(ctCharset).decode(buffer) } catch {}
    }

    // 2. Sniff <meta charset> from the first 2 KB (charset tags are ASCII-safe)
    const sniff = new TextDecoder('windows-1252', { fatal: false }).decode(buffer.slice(0, 2048))
    const metaCharset =
      sniff.match(/<meta[^>]+charset=["']?([^\s"'>;]+)/i)?.[1] ??
      sniff.match(/<meta[^>]+content=["'][^"']*charset=([^\s"';]+)/i)?.[1] ??
      ''
    if (/shift.?jis|sjis|windows.?31j/i.test(metaCharset)) {
      for (const enc of ['shift-jis', 'shift_jis', 'windows-31j']) {
        try { return new TextDecoder(enc).decode(buffer) } catch {}
      }
    }

    // 3. Default to UTF-8 (modern Oricon pages are UTF-8)
    return new TextDecoder('utf-8').decode(buffer)
  } finally {
    clearTimeout(timer)
  }
}

// Oricon blocks generic RSS readers â€” Referer header is required to avoid 403
async function fetchOriconFeed(feedUrl: string): Promise<string> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15_000)
  try {
    const res = await fetch(feedUrl, {
      signal: controller.signal,
      headers: {
        'User-Agent': DESKTOP_UA,
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        'Accept-Language': 'ja,en;q=0.9',
        'Referer': 'https://www.oricon.co.jp/',
      },
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.text()
  } finally {
    clearTimeout(timer)
  }
}

function oriconId(url: string): string {
  return url.match(/\/(?:news|article)\/(\d+)/)?.[1] ?? encodeURIComponent(url).slice(-20)
}

function cleanOriconTitle(title: string | null | undefined): string {
  return decodeHtml(title ?? '')
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .replace(/\s*[-|]\s*(?:ORICON NEWS|\u30aa\u30ea\u30b3\u30f3\u30cb\u30e5\u30fc\u30b9|\u30aa\u30ea\u30b3\u30f3)\s*$/i, '')
    .trim()
}

function oriconTextMatches(title: string | null | undefined, content: string | null | undefined, keyword: string): boolean {
  return textMatchesKeyword(`${cleanOriconTitle(title)} ${content ?? ''}`, keyword)
}

function usableOriconTitle(title: string | null | undefined): string | null {
  const clean = cleanOriconTitle(title)
  if (!clean || /[\uFFFD]/.test(clean)) return null
  if (/[｡-ﾟ]{2,}/.test(clean)) return null
  if (/^ORICON NEWS search:/i.test(clean)) return null
  return clean
}

function extractOriconReadableHtml(html: string, sourceUrl: string): string {
  const base = `<base href="${oriconArticleUrl(sourceUrl)}">`
  const cleaned = html
    .replace(/<script\b[\s\S]*?<\/script>/gi, '')
    .replace(/<style\b[\s\S]*?<\/style>/gi, '')
    .replace(/<noscript\b[\s\S]*?<\/noscript>/gi, '')
    .replace(/<!--[\s\S]*?-->/g, '')
  const title =
    cleaned.match(/<h1\b[^>]*>([\s\S]*?)<\/h1>/i)?.[0] ??
    cleaned.match(/<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i)?.[1] ??
    ''
  const article =
    cleaned.match(/<article\b[^>]*>([\s\S]*?)<\/article>/i)?.[1] ??
    cleaned.match(/<main\b[^>]*>([\s\S]*?)<\/main>/i)?.[1] ??
    cleaned.match(/<div[^>]+(?:class|id)=["'][^"']*(?:article|news|content|detail|body|text)[^"']*["'][^>]*>([\s\S]*?)<\/div>/i)?.[1] ??
    cleaned.match(/<body\b[^>]*>([\s\S]*?)<\/body>/i)?.[1] ??
    cleaned
  const heading = title && !/^</.test(title)
    ? `<h1>${decodeHtml(title).replace(/<[^>]+>/g, '').trim()}</h1>`
    : title
  return `${base}${heading}${article}`
}

export async function fetchOriconReadableContent(url: string): Promise<string> {
  const articleUrl = oriconArticleUrl(url)
  try {
    const html = await fetchOriconHtml(articleUrl)
    return extractOriconReadableHtml(html, articleUrl)
  } catch {
    const text = await fetchJinaReader(articleUrl)
    const safe = decodeHtml(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
    return safe
      .split(/\n{2,}/)
      .map(block => block.trim())
      .filter(Boolean)
      .map(block => {
        if (block.startsWith('# ')) return `<h1>${block.slice(2)}</h1>`
        if (block.startsWith('## ')) return `<h2>${block.slice(3)}</h2>`
        return `<p>${block.replace(/\n/g, '<br>')}</p>`
      })
      .join('\n')
  }
}

async function fetchOriconArticleTitle(url: string): Promise<string | null> {
  try {
    const html = await fetchOriconHtml(oriconArticleUrl(url))
    const raw =
      html.match(/<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i)?.[1] ??
      html.match(/<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:title["']/i)?.[1] ??
      html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] ??
      ''
    return usableOriconTitle(raw)
  } catch {}

  try {
    const text = await fetchJinaReader(oriconArticleUrl(url))
    const raw = text.match(/^Title:\s*(.+)$/m)?.[1] ?? text.match(/^#\s+(.+)$/m)?.[1] ?? ''
    return usableOriconTitle(raw)
  } catch {}

  return null
}

async function fromBing(keyword: string): Promise<FeedItem[]> {
  for (const [label, fetchFn] of [
    ['ddg',  () => fetchDDGLite(`site:oricon.co.jp ${keyword}`)],
    ['bing', () => fetchBingDesktop(`site:oricon.co.jp ${keyword}`)],
    ['google', () => fetchGoogleSearch(`site:oricon.co.jp ${keyword}`)],
  ] as [string, () => Promise<string>][]) {
    try {
      const html = await fetchFn()
      const links = extractBingWebLinks(html, isOriconUrl)
      console.log(`[oricon:${label}] links=${links.length}`)
      const relevant = links.filter(({ title }) => title.length >= 4 && textMatchesKeyword(title, keyword))
      if (relevant.length > 0) {
        const items: FeedItem[] = []
        for (const { url, title } of relevant.slice(0, 12)) {
          const articleUrl = oriconArticleUrl(url)
          const fetched = await fetchOriconArticleTitle(articleUrl)
          const finalTitle = cleanOriconTitle(fetched || title)
          if (!finalTitle || !oriconTextMatches(finalTitle, null, keyword)) continue
          items.push({
            id: `oricon:${oriconId(articleUrl)}`,
            platform: 'oricon', url: articleUrl,
            title: finalTitle || null,
            content_text: null, author: null, thumbnail_url: null,
            media_type: 'article', published_at: now(),
            watch_term_keyword: keyword, fetched_at: now(),
          })
        }
        if (items.length > 0) return items
      }
    } catch (e) {
      console.log(`[oricon:${label}] failed:`, e)
    }
  }
  return []
}

async function fromOriconSearch(keyword: string): Promise<FeedItem[]> {
  // Try both URL formats â€” Oricon sometimes uses path-based, sometimes query-based
  for (const url of [
    `https://www.oricon.co.jp/search/result.php?search_string=${encodeURIComponent(keyword)}&types=article`,
  ]) {
    try {
      const html = await fetchOriconHtml(url)
      const results: FeedItem[] = []
      const seen = new Set<string>()
      const re = /href="((https?:\/\/(?:www\.)?oricon\.co\.jp)?\/(?:news|article)\/\d+\/?)"/gi
      let m: RegExpExecArray | null
      while (results.length < 20 && (m = re.exec(html)) !== null) {
        const raw = m[1]
        const link = oriconArticleUrl(raw.startsWith('http') ? raw : `https://www.oricon.co.jp${raw}`)
        const id = oriconId(link)
        if (seen.has(id)) continue
        seen.add(id)
        const nearby = html.slice(Math.max(0, m.index - 20), m.index + 400)
        const titleM = nearby.match(/alt="([^"]{4,})"/)
          ?? nearby.match(/<h[23][^>]*>([^<]{4,})<\/h[23]>/)
          ?? nearby.match(/title="([^"]{4,})"/)
          ?? nearby.match(/>([^<]{5,140})<\/(?:h[1-6]|strong|span|div|p|a)>/)
        const title =
          await fetchOriconArticleTitle(link) ||
          usableOriconTitle(titleM?.[1]?.replace(/\s+/g, ' ').trim())
        if (!title) continue
        if (!oriconTextMatches(title, null, keyword)) continue
        const dateM = nearby.match(/(\d{4}-\d{2}-\d{2})/) ?? nearby.match(/(\d{4})å¹´(\d{1,2})æœˆ(\d{1,2})æ—¥/)
        let publishedAt = now()
        if (dateM) {
          const dateText = dateM[2]
            ? `${dateM[1]}-${dateM[2].padStart(2, '0')}-${dateM[3].padStart(2, '0')}`
            : dateM[1]
          const date = new Date(dateText)
          if (!isNaN(date.getTime())) publishedAt = date.toISOString()
        }
        results.push({
          id: `oricon:${id}`,
          platform: 'oricon', url: link,
          title: title || null,
          content_text: null, author: null, thumbnail_url: null,
          media_type: 'article', published_at: publishedAt,
          watch_term_keyword: keyword, fetched_at: now(),
        })
      }
      console.log(`[oricon:search] url=${url} results=${results.length}`)
      if (results.length > 0) return results
    } catch (e) {
      console.log(`[oricon:search] failed:`, e)
    }
  }
  return []
}

async function fromOriconDirectFeeds(keyword: string): Promise<FeedItem[]> {
  const results: FeedItem[] = []
  const seen = new Set<string>()

  const addItem = (link: string, title: string, pubDate?: string) => {
    const articleUrl = oriconArticleUrl(link)
    const id = oriconId(articleUrl)
    if (seen.has(id)) return
    seen.add(id)
    let pd = now()
    if (pubDate) { const d = new Date(pubDate); if (!isNaN(d.getTime())) pd = d.toISOString() }
    const cleanTitle = cleanOriconTitle(title)
    results.push({
      id: `oricon:${id}`, platform: 'oricon', url: articleUrl,
      title: cleanTitle || null,
      content_text: null, author: null, thumbnail_url: null,
      media_type: 'article', published_at: pd,
      watch_term_keyword: keyword, fetched_at: now(),
    })
  }

  // 1. RSS feeds with proper Referer (Oricon blocks hotlinked RSS without it)
  const feeds = [
    'https://www.oricon.co.jp/rss/news/music/',
    'https://www.oricon.co.jp/rss/news/',
  ]
  await Promise.allSettled(feeds.map(async feedUrl => {
    try {
      const xml = await fetchOriconFeed(feedUrl)
      const items = parseRssItems(xml)
      const before = results.length
      for (const item of items) {
        if (!item.link) continue
        if (!oriconTextMatches(item.title, null, keyword)) continue
        addItem(item.link, item.title, item.pubDate)
      }
      console.log(`[oricon:feed] ${feedUrl} added=${results.length - before}`)
    } catch (e) {
      console.log(`[oricon:feed] failed ${feedUrl}:`, e)
    }
  }))

  return results
}

async function fromOriconRSS(keyword: string): Promise<FeedItem[]> {
  const rssItems = await fetchBingNewsRSS(`${keyword} site:oricon.co.jp`)
  const candidates = rssItems.filter(i => isOriconUrl(i.url))
  const results: FeedItem[] = []
  for (const item of candidates.slice(0, 12)) {
    const articleUrl = oriconArticleUrl(item.url)
    const articleTitle = await fetchOriconArticleTitle(articleUrl)
    const finalTitle = cleanOriconTitle(articleTitle || item.title)
    if (!oriconTextMatches(finalTitle, null, keyword)) continue
    results.push({
      id: `oricon:${oriconId(articleUrl)}`, platform: 'oricon', url: articleUrl,
      title: finalTitle || null,
      content_text: null, author: null, thumbnail_url: null,
      media_type: 'article', published_at: item.pubDate,
      watch_term_keyword: keyword, fetched_at: now(),
    })
  }
  console.log(`[oricon:rss] candidates=${candidates.length} matched=${results.length}`)
  return results
}

export async function scrapeOricon(keyword: string, mode: string): Promise<FeedItem[]> {
  for (const [label, fn] of [
    ['feeds',  () => fromOriconDirectFeeds(keyword)],
    ['rss',    () => fromOriconRSS(keyword)],
    ['bing',   () => fromBing(keyword)],
    ['search', () => fromOriconSearch(keyword)],
  ] as [string, () => Promise<FeedItem[]>][]) {
    try {
      const items = await fn()
      if (items.length > 0) return items
    } catch (e) {
      console.log(`[oricon:${label}] failed:`, e)
    }
  }
  return []
}
