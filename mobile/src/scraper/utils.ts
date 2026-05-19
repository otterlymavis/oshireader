export const MOBILE_UA =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'

export const DESKTOP_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

export const BOT_UA =
  'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'

async function doFetch(url: string, headers: Record<string, string>, timeoutMs = 15_000): Promise<string> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(url, { signal: controller.signal, headers })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.text()
  } finally {
    clearTimeout(timer)
  }
}

export async function fetchHtml(url: string, params?: Record<string, string>): Promise<string> {
  const qs = params ? '?' + new URLSearchParams(params).toString() : ''
  return doFetch(url + qs, {
    'User-Agent': MOBILE_UA,
    'Accept-Language': 'ja,en;q=0.9',
    'Accept': 'text/html,*/*',
  })
}

export async function fetchXml(url: string): Promise<string> {
  return doFetch(url, {
    'User-Agent': DESKTOP_UA,
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    'Accept-Language': 'ja,en;q=0.9',
  })
}

// Bing News RSS â€” structured feed, much more reliable than HTML scraping
export async function fetchBingNewsRSS(
  query: string,
): Promise<Array<{ url: string; title: string; pubDate: string; description: string }>> {
  const xml = await fetchXml(
    `https://www.bing.com/news/search?q=${encodeURIComponent(query)}&format=rss&cc=JP&setlang=JA`,
  )
  const items = parseRssItems(xml)
  return items.flatMap(item => {
    if (!item.link) return []
    // Bing RSS XML uses HTML entities (&amp; instead of &) â€” decode before parsing
    // the click-tracker redirect: apiclick.aspx?...&amp;url=ACTUAL_URL_ENCODED&amp;...
    const rawLink = item.link.replace(/&amp;/g, '&')
    let url = rawLink
    const urlParam = rawLink.match(/[?&]url=([^&]+)/)?.[1]
    if (urlParam) {
      try { url = decodeURIComponent(urlParam) } catch {}
    }
    let pubDate = new Date().toISOString()
    if (item.pubDate) {
      const d = new Date(item.pubDate)
      if (!isNaN(d.getTime())) pubDate = d.toISOString()
    }
    return [{ url, title: decodeHtml(item.title), pubDate, description: decodeHtml(item.description) }]
  })
}

export async function fetchHtmlRef(url: string, referer: string): Promise<string> {
  return doFetch(url, {
    'User-Agent': MOBILE_UA,
    'Accept-Language': 'ja,en;q=0.9',
    'Accept': 'text/html,*/*',
    'Referer': referer,
  })
}

export async function fetchBingDesktop(query: string): Promise<string> {
  return doFetch(
    `https://www.bing.com/search?q=${encodeURIComponent(query)}&cc=JP&setlang=JA`,
    {
      'User-Agent': DESKTOP_UA,
      'Accept-Language': 'ja,en;q=0.9',
      'Accept': 'text/html,*/*',
      'Referer': 'https://www.bing.com/',
    },
  )
}

// DuckDuckGo Lite â€” returns direct URLs (no redirect wrapper), less bot detection than Bing
export async function fetchDDGLite(query: string): Promise<string> {
  return doFetch(
    `https://lite.duckduckgo.com/lite/?q=${encodeURIComponent(query)}&kl=jp-jp`,
    {
      'User-Agent': DESKTOP_UA,
      'Accept-Language': 'ja,en;q=0.9',
      'Accept': 'text/html,*/*',
      'Referer': 'https://duckduckgo.com/',
    },
  )
}

export async function fetchGoogleSearch(query: string): Promise<string> {
  return doFetch(
    `https://www.google.com/search?q=${encodeURIComponent(query)}&hl=ja&gl=JP&num=20`,
    {
      'User-Agent': DESKTOP_UA,
      'Accept-Language': 'ja,en;q=0.9',
      'Accept': 'text/html,*/*',
    },
  )
}

export async function fetchHtmlDesktop(url: string, timeoutMs = 8_000): Promise<string> {
  return doFetch(url, {
    'User-Agent': DESKTOP_UA,
    'Accept-Language': 'ja,en;q=0.9',
    'Accept': 'text/html,*/*',
  }, timeoutMs)
}

export async function fetchJinaReader(url: string): Promise<string> {
  return doFetch(`https://r.jina.ai/${url}`, {
    'User-Agent': DESKTOP_UA,
    'Accept': 'text/plain,*/*',
    'Accept-Language': 'ja,en;q=0.9',
  })
}

export async function fetchHtmlBot(url: string, params?: Record<string, string>): Promise<string> {
  const qs = params ? '?' + new URLSearchParams(params).toString() : ''
  return doFetch(url + qs, {
    'User-Agent': BOT_UA,
    'Accept-Language': 'ja,en;q=0.9',
    'Accept': 'text/html,*/*',
  })
}

export function extractLinks(
  html: string,
  pattern: RegExp,
  limit = 25,
): Array<{ href: string; text: string }> {
  const out: Array<{ href: string; text: string }> = []
  const re = /<a\b([^>]*)>([\s\S]*?)<\/a>/gi
  let m: RegExpExecArray | null
  while (out.length < limit && (m = re.exec(html)) !== null) {
    const attrs = m[1]
    const inner = m[2]
    const hm = attrs.match(/\bhref="([^"]*)"/)
    if (!hm || !pattern.test(hm[1])) continue
    const aria = attrs.match(/\baria-label="([^"]*)"/)
    const text = (aria?.[1] || inner.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ')).trim()
    if (text) out.push({ href: hm[1], text })
  }
  return out
}

export function extractNextData(html: string): Record<string, unknown> | null {
  const m = html.match(/<script\b[^>]*\bid="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/i)
  if (!m) return null
  try { return JSON.parse(m[1]) as Record<string, unknown> } catch { return null }
}

export function parseRssItems(xml: string) {
  const items: Array<{ title: string; link: string; guid: string; pubDate: string; description: string }> = []
  const itemRe = /<(?:item|entry)\b[\s\S]*?<\/(?:item|entry)>/gi
  let itemM: RegExpExecArray | null

  const get = (s: string, tag: string): string => {
    const r = new RegExp(`<${tag}[^>]*>(?:<!\\[CDATA\\[([\\s\\S]*?)\\]\\]>|([\\s\\S]*?))<\\/${tag}>`, 'i')
    const m = s.match(r)
    return (m ? (m[1] ?? m[2] ?? '') : '').trim()
  }

  while ((itemM = itemRe.exec(xml)) !== null) {
    const s = itemM[0]
    const title = get(s, 'title')
    let link = get(s, 'link')
    if (!link) link = s.match(/<link[^>]+href="([^"]+)"/)?.[1] ?? ''
    if (!title || !link) continue
    items.push({
      title,
      link,
      guid: get(s, 'guid') || get(s, 'id') || link,
      pubDate: get(s, 'pubDate') || get(s, 'published') || get(s, 'updated') || '',
      description: get(s, 'description') || get(s, 'summary') || '',
    })
  }
  return items
}

export const now = () => new Date().toISOString()

export function decodeHtml(text: string): string {
  return text
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&#(\d+);/g, (_, n) => {
      const code = Number(n)
      return Number.isFinite(code) ? String.fromCharCode(code) : ''
    })
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => {
      const code = parseInt(n, 16)
      return Number.isFinite(code) ? String.fromCharCode(code) : ''
    })
}

export function normalizeText(text: string): string {
  return decodeHtml(text).toLowerCase().replace(/\s+/g, '')
}

export function textMatchesKeyword(text: string | null | undefined, keyword: string): boolean {
  const haystack = normalizeText(text ?? '')
  const needle = normalizeText(keyword)
  if (!needle) return true
  if (haystack.includes(needle)) return true
  const parts = keyword.split(/[\u3000\s]+/).map(normalizeText).filter(Boolean)
  return parts.length > 1 && parts.every(part => haystack.includes(part))
}

// Bing web search wraps result URLs in /ck/a?...&u=a1BASE64URL&... redirects.
// This decodes the destination from those redirect links.
function decodeBingRedirect(hrefRaw: string): string | null {
  const href = hrefRaw.replace(/&amp;/g, '&')
  const m = href.match(/[?&]u=a1([A-Za-z0-9+/=_-]+)/)
  if (!m) return null
  try {
    let b64 = m[1].replace(/-/g, '+').replace(/_/g, '/')
    const pad = b64.length % 4
    if (pad === 2) b64 += '=='
    else if (pad === 3) b64 += '='
    const decoded = atob(b64)
    return decoded.startsWith('http') ? decoded : null
  } catch {
    return null
  }
}

// Extract links from Bing/DDG web search HTML, decoding redirect wrappers where needed.
export function extractBingWebLinks(
  html: string,
  urlTest: (url: string) => boolean,
  limit = 25,
): Array<{ url: string; title: string }> {
  const out: Array<{ url: string; title: string }> = []
  const seen = new Set<string>()
  const re = /<a\b([^>]*)>([\s\S]*?)<\/a>/gi
  let m: RegExpExecArray | null
  while (out.length < limit && (m = re.exec(html)) !== null) {
    const attrs = m[1]
    let url: string | null = null

    // Direct URL (DDG Lite old format, Bing organic results)
    const directM = attrs.match(/\bhref="(https?:\/\/[^"]+)"/)
    if (directM && urlTest(directM[1])) {
      url = directM[1]
    }

    // Bing redirect: /ck/a?...&u=a1BASE64URL&...
    if (!url) {
      const bingM = attrs.match(/\bhref="([^"]*\/ck\/a\?[^"]+)"/)
      if (bingM) {
        const decoded = decodeBingRedirect(bingM[1])
        if (decoded && urlTest(decoded)) url = decoded
      }
    }

    // DDG Lite redirect: /l/?uddg=URL_ENCODED (current DDG format)
    if (!url) {
      const ddgM = attrs.match(/\bhref="\/l\/\?[^"]*uddg=([^"&]+)/)
      if (ddgM) {
        try {
          const decoded = decodeURIComponent(ddgM[1])
          if (decoded.startsWith('http') && urlTest(decoded)) url = decoded
        } catch {}
      }
    }

    // Google result redirect: /url?q=URL_ENCODED
    if (!url) {
      const googleM = attrs.match(/\bhref="\/url\?q=([^"&]+)/)
      if (googleM) {
        try {
          const decoded = decodeURIComponent(googleM[1])
          if (decoded.startsWith('http') && urlTest(decoded)) url = decoded
        } catch {}
      }
    }

    if (!url || seen.has(url)) continue
    seen.add(url)
    const title = decodeHtml(m[2].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim())
    out.push({ url, title: title || url })
  }
  return out
}
