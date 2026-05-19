import type { FeedItem } from '../localDb'
import {
  fetchDDGLite,
  fetchBingDesktop,
  fetchGoogleSearch,
  fetchHtmlDesktop,
  fetchJinaReader,
  extractBingWebLinks,
  decodeHtml,
  textMatchesKeyword,
  now,
} from './utils'

function threadId(url: string): string {
  const m = url.match(/\/(\d{9,})/)
  return m ? m[1] : encodeURIComponent(url).slice(-20)
}

// 5ch thread IDs are Unix timestamps in seconds (10 digits ≈ year 2001–2286)
function threadDate(url: string): string {
  const m = url.match(/\/(\d{10})(?:\/|$)/)
  if (!m) return now()
  const ts = parseInt(m[1], 10) * 1000
  const d = new Date(ts)
  if (isNaN(d.getTime()) || d.getFullYear() < 2000 || d.getFullYear() > 2100) return now()
  return d.toISOString()
}

function toReadable5chUrl(rawUrl: string): string {
  try {
    const parsed = new URL(rawUrl)
    const isItest = /^itest\.5ch\.(net|io)$/i.test(parsed.hostname)
    const isFiveCh = /(^|\.)5ch\.(net|io)$/i.test(parsed.hostname)
    if (!isFiveCh && !isItest) return rawUrl
    const itestMatch = parsed.pathname.match(/^\/([^/]+)\/test\/read\.cgi\/([^/]+)\/(\d{9,})/)
    if (isItest && itestMatch) {
      return `https://itest.5ch.io/${itestMatch[1]}/test/read.cgi/${itestMatch[2]}/${itestMatch[3]}/`
    }
    const match = parsed.pathname.match(/\/test\/read\.cgi\/([^/]+)\/(\d{9,})/)
    if (!match) return rawUrl
    const server = parsed.hostname.split('.')[0]
    if (!server || /^(www|itest|5ch|find|dig)$/i.test(server)) return rawUrl
    return `https://itest.5ch.io/${server}/test/read.cgi/${match[1]}/${match[2]}/`
  } catch {
    return rawUrl
  }
}

// Match only actual thread URLs (must contain /test/read.cgi/)
const is5ch = (url: string) =>
  /(?:5ch|2ch)\.(?:net|sc|io)\//.test(url) && /\/test\/read\.cgi\//.test(url)

async function fromJinaFiveCh(keyword: string): Promise<FeedItem[]> {
  const results: FeedItem[] = []
  const seen = new Set<string>()
  for (const target of [
    `https://find.5ch.io/search?q=${encodeURIComponent(keyword)}`,
    `https://find.5ch.net/search?q=${encodeURIComponent(keyword)}`,
  ]) {
    try {
      const text = await fetchJinaReader(target)
      const re = /\[([^\]]{2,200})\]\((https?:\/\/[^)\s]+\/test\/read\.cgi\/[^)\s]+)\)/g
      let m: RegExpExecArray | null
      while (results.length < 20 && (m = re.exec(text)) !== null) {
        const title = decodeHtml(m[1]).replace(/\s+/g, ' ').trim()
        const url = toReadable5chUrl(m[2].split('?')[0])
        if (!is5ch(url) || !textMatchesKeyword(title, keyword)) continue
        const id = threadId(url)
        if (seen.has(id)) continue
        seen.add(id)
        results.push({
          id: `5ch:${id}`,
          platform: '5ch',
          url,
          title: title || null,
          content_text: null,
          author: null,
          thumbnail_url: null,
          media_type: 'text',
          published_at: now(),
          watch_term_keyword: keyword,
          fetched_at: now(),
        })
      }
      console.log(`[5ch:jina] url=${target} results=${results.length}`)
      if (results.length > 0) return results
    } catch (e) {
      console.log('[5ch:jina] failed:', e)
    }
  }
  return results
}

export async function scrapeFiveCh(keyword: string, mode: string): Promise<FeedItem[]> {
  if (mode === 'media_only') return []

  const toItem = (url: string, title: string): FeedItem => ({
    id: `5ch:${threadId(url)}`,
    platform: '5ch', url: toReadable5chUrl(url), title: title || null,
    content_text: null, author: null, thumbnail_url: null,
    media_type: 'text', published_at: now(),
    watch_term_keyword: keyword, fetched_at: now(),
  })

  const jinaItems = await fromJinaFiveCh(keyword)
  if (jinaItems.length > 0) return jinaItems

  // Primary: 5ch/2ch search engines — use desktop UA, short timeout
  for (const searchUrl of [
    `https://find.5ch.net/search?q=${encodeURIComponent(keyword)}`,
    `https://dig.5ch.net/?keywords=${encodeURIComponent(keyword)}&all=2`,
    `https://2ch.sc/search/?q=${encodeURIComponent(keyword)}`,
  ]) {
    try {
      const html = await fetchHtmlDesktop(searchUrl)
      const base = new URL(searchUrl).origin
      // 2ch.sc and some 5ch search pages use relative /test/read.cgi/ paths
      const re = /href="((?:https?:\/\/[a-z0-9.-]+)?\/test\/read\.cgi\/[a-z0-9_-]+\/\d+(?:\/[^"?#]*)?)"/gi
      const results: FeedItem[] = []
      const seen = new Set<string>()
      let m: RegExpExecArray | null
      while (results.length < 20 && (m = re.exec(html)) !== null) {
        const raw = m[1]
        if (!raw.startsWith('http') && /\/\/(?:find|dig)\.5ch\./i.test(base)) continue
        const url = raw.startsWith('http') ? raw : `${base}${raw}`
        const id = threadId(url)
        if (seen.has(id)) continue
        seen.add(id)
        const nearby = html.slice(Math.max(0, m.index - 100), m.index + 400)
        const titleM = nearby.match(/class="[^"]*title[^"]*"[^>]*>([^<]{4,})</)
          ?? nearby.match(/<dt[^>]*>([^<]{4,})<\/dt>/)
          ?? nearby.match(/alt="([^"]{4,})"/)
          ?? nearby.match(/title="([^"]{4,})"/)
        results.push(toItem(url, titleM?.[1]?.trim() ?? ''))
      }
      console.log(`[5ch:find] url=${searchUrl} results=${results.length}`)
      if (results.length > 0) return results
    } catch (e) {
      console.log(`[5ch:find] failed:`, e)
    }
  }

  // Fallback: DDG → Bing → Google. Use plain site: without path — path-based site: is
  // not supported by most engines and returns nothing.
  for (const [label, fetchFn] of [
    ['ddg',  () => fetchDDGLite(`site:5ch.net ${keyword}`)],
    ['bing', () => fetchBingDesktop(`site:5ch.net ${keyword}`)],
    ['bing2ch', () => fetchBingDesktop(`site:2ch.sc ${keyword}`)],
    ['google', () => fetchGoogleSearch(`site:5ch.net ${keyword}`)],
  ] as [string, () => Promise<string>][]) {
    try {
      const html = await fetchFn()
      const links = extractBingWebLinks(html, is5ch)
      console.log(`[5ch:${label}] links=${links.length}`)
      if (links.length > 0) return links.map(({ url, title }) => toItem(url, title))
    } catch (e) {
      console.log(`[5ch:${label}] failed:`, e)
    }
  }

  return []
}
