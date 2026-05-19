import AsyncStorage from '@react-native-async-storage/async-storage'
import { MOBILE_UA, fetchHtml, fetchDDGLite } from './utils'

export interface IrasutoyaImage {
  url: string   // irasutoya post URL
  thumb: string // illustration image URL
  title: string
}

const POPULAR_CACHE_KEY = '@otterpia:irasutoya_popular_v2'
const POPULAR_CACHE_TTL = 24 * 60 * 60 * 1000
const SEARCH_TARGET = 72
const DDG_POST_LIMIT = 36
const BLOGGER_PAGE_SIZE = 50
const BLOGGER_PAGE_STARTS = [1, 51]

// ── helpers ───────────────────────────────────────────────────────────────────

function upscale(src: string): string {
  return src
    .replace(/\/s\d+-c\//, '/s400-c/')
    .replace(/\/s\d+-c$/, '/s400-c')
    .replace(/\/s\d+\//, '/s400/')
    .replace(/=s\d+-c/, '=s400-c')
    .replace(/=s\d+$/, '=s400')
}

// Fetch one post page and return its main illustration + title
async function fetchPostImage(postUrl: string): Promise<{ thumb: string; title: string } | null> {
  try {
    const html = await fetchHtml(postUrl)
    const imgM = html.match(
      /(?:src|data-src)="(https?:\/\/(?:blogger\.googleusercontent\.com|[^"]*\.bp\.blogspot\.com)[^"]+)"/i,
    )
    if (!imgM) return null
    const pageTitle = html.match(/<title[^>]*>([^<]+)<\/title>/i)?.[1]
      ?.replace(/\s*[-|｜・]\s*いらすとや.*$/i, '')
      .replace(/\s*[-|｜・]\s*irasutoya.*$/i, '')
      .trim() ?? ''
    return { thumb: upscale(imgM[1]), title: pageTitle }
  } catch {
    return null
  }
}

// ── Blogger JSON API fetch ─────────────────────────────────────────────────────

async function fetchBloggerFeed(feedUrl: string): Promise<IrasutoyaImage[]> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15_000)
  let data: any
  try {
    const res = await fetch(feedUrl, {
      signal: controller.signal,
      headers: {
        'User-Agent': MOBILE_UA,
        'Accept': 'application/json, */*',
        'Accept-Language': 'ja,en;q=0.9',
      },
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    data = await res.json()
  } finally {
    clearTimeout(timer)
  }

  const entries: any[] = data?.feed?.entry ?? []
  const results: IrasutoyaImage[] = []

  for (const entry of entries) {
    const title: string = entry?.title?.['$t'] ?? ''
    const altLink: string =
      (entry?.link as any[] | undefined)?.find((l: any) => l.rel === 'alternate')?.href ?? ''

    let thumb: string = entry?.['media$thumbnail']?.url ?? ''
    if (!thumb) {
      const content: string = entry?.content?.['$t'] ?? entry?.summary?.['$t'] ?? ''
      const m = content.match(/src="(https?:\/\/(?:blogger\.googleusercontent\.com|[^"]*\.bp\.blogspot\.com)[^"]+)"/)
      thumb = m?.[1] ?? ''
    }

    if (altLink && thumb) {
      results.push({ url: altLink, thumb: upscale(thumb), title })
    }
  }

  console.log(`[irasutoya:blogger] results=${results.length} url=${feedUrl.slice(0, 80)}`)
  return results
}

function dedupeImages(items: IrasutoyaImage[], limit = SEARCH_TARGET): IrasutoyaImage[] {
  const seen = new Set<string>()
  const out: IrasutoyaImage[] = []
  for (const item of items) {
    if (!item.url || !item.thumb || seen.has(item.url)) continue
    seen.add(item.url)
    out.push(item)
    if (out.length >= limit) break
  }
  return out
}

// ── translateToJapanese ───────────────────────────────────────────────────────
// Uses Google Translate's free endpoint to convert non-Japanese text to Japanese.
// Irasutoya's Blogger API only understands Japanese; DDG also works better with it.

function isJapanese(text: string): boolean {
  return /[぀-ゟ゠-ヿ一-鿿豈-﫿]/.test(text)
}

async function translateToJapanese(text: string): Promise<string> {
  if (isJapanese(text)) return text
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 8_000)
    let data: unknown
    try {
      const res = await fetch(
        `https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ja&dt=t&q=${encodeURIComponent(text)}`,
        { signal: controller.signal },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      data = await res.json()
    } finally {
      clearTimeout(timer)
    }
    const translated = (data as any)?.[0]
      ?.map((chunk: any) => chunk?.[0] ?? '')
      .join('') ?? ''
    console.log(`[irasutoya] translated "${text}" → "${translated}"`)
    return translated || text
  } catch (e) {
    console.log('[irasutoya] translate failed:', e)
    return text
  }
}

// ── searchIrasutoya ──────────────────────────────────────────────────────────
// 1. DDG site:irasutoya.com search — understands English/any language (Google's index)
// 2. Fetch each post page to extract the illustration image
// 3. Fall back to Blogger JSON API — translate keyword to Japanese first

export async function searchIrasutoya(keyword: string): Promise<IrasutoyaImage[]> {
  const collected: IrasutoyaImage[] = []

  // Step 1: DDG site search
  try {
    const ddgHtml = await fetchDDGLite(`site:irasutoya.com ${keyword}`)
    const postUrls: string[] = []
    const seen = new Set<string>()
    // Match direct irasutoya post URLs from DDG results
    const re = /href="(https?:\/\/www\.irasutoya\.com\/\d{4}\/\d{2}\/[^"#?]+\.html)"/gi
    let m: RegExpExecArray | null
    while (postUrls.length < DDG_POST_LIMIT && (m = re.exec(ddgHtml)) !== null) {
      const u = m[1]
      if (!seen.has(u)) { seen.add(u); postUrls.push(u) }
    }
    console.log(`[irasutoya:ddg] keyword="${keyword}" posts=${postUrls.length}`)

    if (postUrls.length > 0) {
      const settled = await Promise.allSettled(postUrls.map(fetchPostImage))
      for (let i = 0; i < settled.length; i++) {
        const r = settled[i]
        if (r.status === 'fulfilled' && r.value) {
          collected.push({ url: postUrls[i], thumb: r.value.thumb, title: r.value.title || keyword })
        }
      }
      if (collected.length > 0) {
        console.log(`[irasutoya:ddg] images=${collected.length}`)
      }
    }
  } catch (e) {
    console.log('[irasutoya:ddg] failed:', e)
  }

  // Fallback: Blogger JSON API — translate to Japanese first if needed
  try {
    const jaKeyword = await translateToJapanese(keyword)
    const settled = await Promise.allSettled(
      BLOGGER_PAGE_STARTS.map(start =>
        fetchBloggerFeed(
          `https://www.irasutoya.com/feeds/posts/default?alt=json&q=${encodeURIComponent(jaKeyword)}&max-results=${BLOGGER_PAGE_SIZE}&start-index=${start}`,
        ),
      ),
    )
    for (const result of settled) {
      if (result.status === 'fulfilled') collected.push(...result.value)
    }
    const deduped = dedupeImages(collected)
    console.log(`[irasutoya:search] total=${deduped.length}`)
    return deduped
  } catch (e) {
    console.log('[irasutoya:search] blogger failed:', e)
    return dedupeImages(collected)
  }
}

// ── getPopularIrasutoya ───────────────────────────────────────────────────────

export async function getPopularIrasutoya(): Promise<IrasutoyaImage[]> {
  try {
    const raw = await AsyncStorage.getItem(POPULAR_CACHE_KEY)
    if (raw) {
      const { ts, data } = JSON.parse(raw) as { ts: number; data: IrasutoyaImage[] }
      if (Date.now() - ts < POPULAR_CACHE_TTL && data.length > 0) {
        console.log(`[irasutoya:popular] cache hit (${data.length})`)
        return data
      }
    }
  } catch {}

  const settled = await Promise.allSettled([
    fetchBloggerFeed('https://www.irasutoya.com/feeds/posts/default?alt=json&max-results=20'),
    fetchBloggerFeed(
      `https://www.irasutoya.com/feeds/posts/default/-/${encodeURIComponent('人物')}?alt=json&max-results=15`,
    ),
  ])

  const seen = new Set<string>()
  const combined: IrasutoyaImage[] = []
  for (const r of settled) {
    if (r.status === 'fulfilled') {
      for (const img of r.value) {
        if (!seen.has(img.url) && combined.length < 30) {
          seen.add(img.url)
          combined.push(img)
        }
      }
    }
  }
  console.log(`[irasutoya:popular] fetched ${combined.length}`)

  if (combined.length > 0) {
    AsyncStorage.setItem(
      POPULAR_CACHE_KEY,
      JSON.stringify({ ts: Date.now(), data: combined }),
    ).catch(() => {})
  }
  return combined
}
