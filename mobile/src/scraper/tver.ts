import type { FeedItem } from '../localDb'
import {
  fetchDDGLite,
  fetchBingDesktop,
  fetchBingNewsRSS,
  fetchGoogleSearch,
  extractBingWebLinks,
  DESKTOP_UA,
  normalizeText,
  textMatchesKeyword,
  now,
} from './utils'

function episodeId(url: string): string {
  return url.match(/\/(?:episodes|series)\/([^/?#]+)/)?.[1] ?? encodeURIComponent(url).slice(-20)
}

function compactText(parts: Array<unknown>): string {
  return parts
    .flatMap(part => Array.isArray(part) ? part : [part])
    .filter((part): part is string => typeof part === 'string' && part.trim().length > 0)
    .join(' ')
}

function peopleNames(content: any): string[] {
  return [
    ...(Array.isArray(content?.talents) ? content.talents : []),
    ...(Array.isArray(content?.casts) ? content.casts : []),
    ...(Array.isArray(content?.performers) ? content.performers : []),
  ]
    .map((person: any) => person?.name ?? person?.talentName ?? person?.castName)
    .filter((name: unknown): name is string => typeof name === 'string' && name.trim().length > 0)
}

function tverPrimaryText(content: any): string {
  return compactText([
    content?.title,
    content?.seriesTitle,
    content?.programTitle,
    content?.name,
    content?.episodeTitle,
  ])
}

function tverDescriptionText(content: any): string {
  return compactText([
    content?.description,
    content?.episodeDescription,
    content?.summary,
    content?.synopsis,
  ])
}

function tverSearchText(content: any): string {
  return compactText([
    tverPrimaryText(content),
    tverDescriptionText(content),
    peopleNames(content),
  ])
}

function isRelevantTVerContent(content: any, keyword: string): boolean {
  const strongText = compactText([tverPrimaryText(content), peopleNames(content)])
  if (textMatchesKeyword(strongText, keyword)) return true

  return textMatchesKeyword(tverDescriptionText(content), keyword)
}

function formatTVerTitle(content: any): string | null {
  const series = compactText([content?.seriesTitle, content?.programTitle])
  const episode = compactText([content?.title, content?.episodeTitle, content?.name])
  if (series && episode && !normalizeText(episode).includes(normalizeText(series))) {
    return `${series}: ${episode}`
  }
  return episode || series || null
}

function tverThumbnail(content: any): string | null {
  const raw = (
    content?.thumbnailUrl ??
    content?.thumbnailURL ??
    content?.thumbnail_url ??
    content?.thumbnailPath ??
    content?.imageUrl ??
    content?.imageURL ??
    content?.images?.[0]?.url ??
    null
  )
  if (!raw || typeof raw !== 'string') return null
  if (/^https?:\/\//i.test(raw)) return raw
  if (raw.startsWith('/')) return `https://statics.tver.jp${raw}`
  return raw
}

function cleanTVerSearchTitle(title: string): string {
  return title
    .replace(/\s*[-|]\s*TVer(?:\s*[-|].*)?$/i, '')
    .replace(/\bTVer\b/gi, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function tverContentUrl(content: any, type?: string | null): string | null {
  const contentType = String(type ?? content?.type ?? '').toLowerCase()
  const id = String(content?.id ?? '').trim()
  const seriesId = String(content?.seriesID ?? content?.seriesId ?? '').trim()
  if (contentType === 'series' && (seriesId || id)) return `https://tver.jp/series/${seriesId || id}`
  if (contentType === 'special' && id) return `https://tver.jp/specials/${id}`
  if (id) return `https://tver.jp/episodes/${id}`
  if (seriesId) return `https://tver.jp/series/${seriesId}`
  return null
}

async function fromTVerAPI(keyword: string): Promise<FeedItem[]> {
  const token = await createTVerPlatformToken()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15_000)
  let data: unknown
  try {
    const params = new URLSearchParams({
      platform_uid: token.platform_uid,
      platform_token: token.platform_token,
      keyword,
      detail: 'true',
      platform: 'web',
      require_talent_data: 'true',
      page: '1',
    })
    const res = await fetch(
      `https://platform-api.tver.jp/service/api/v1/callKeywordSearch?${params.toString()}`,
      {
        signal: controller.signal,
        headers: {
          'User-Agent': DESKTOP_UA,
          'Accept': 'application/json, */*',
          'x-tver-platform-type': 'web',
          'x-clientplatform': 'web',
          'Origin': 'https://tver.jp',
          'Referer': 'https://tver.jp/',
          'Accept-Language': 'ja,en;q=0.9',
        },
      },
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    data = await res.json()
  } finally {
    clearTimeout(timer)
  }

  // Log result keys to help diagnose future shape changes
  const resultKeys = Object.keys((data as any)?.result ?? {})
  console.log('[tver:api] result keys:', JSON.stringify(resultKeys))

  // Handle multiple known TVer API response shapes
  const episodes: any[] =
    (data as any)?.result?.episodes?.contents ??
    (data as any)?.result?.seriesAndEpisode?.episodes?.contents ??
    (data as any)?.result?.contents ??
    (data as any)?.result?.rows ??
    (data as any)?.contents ??
    (data as any)?.rows ??
    []

  const matched: FeedItem[] = []
  const fallback: FeedItem[] = []
  for (const ep of episodes) {
    const contentType = ep?.type ?? ep?.content?.type ?? ep?.episode?.type ?? ep?.contentType ?? null
    const content = ep?.content ?? ep?.episode ?? ep
    const url = tverContentUrl(content, contentType)
    const itemId = content?.id ?? content?.seriesID ?? content?.seriesId ?? ep?.id ?? ''
    if (!itemId || !url) continue
    const searchableText = tverSearchText(content)
    const item: FeedItem = {
      id: `tver:${itemId}`,
      platform: 'tver',
      url,
      title: formatTVerTitle(content),
      content_text: searchableText || null,
      author: content?.broadcasterName ?? content?.productionProviderName ?? null,
      thumbnail_url: tverThumbnail(content),
      media_type: 'video',
      published_at: now(),
      watch_term_keyword: keyword,
      fetched_at: now(),
    }
    if (isRelevantTVerContent(content, keyword)) matched.push(item)
    else fallback.push(item)
  }
  console.log(`[tver:api] raw=${episodes.length} matched=${matched.length} fallback=${fallback.length}`)
  return matched.slice(0, 20)
}

async function createTVerPlatformToken(): Promise<{ platform_uid: string; platform_token: string }> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15_000)
  try {
    const res = await fetch('https://platform-api.tver.jp/v2/api/platform_users/browser/create', {
      method: 'POST',
      signal: controller.signal,
      headers: {
        'User-Agent': DESKTOP_UA,
        'Accept': 'application/json, */*',
        'Accept-Language': 'ja,en;q=0.9',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://tver.jp',
        'Referer': 'https://tver.jp/',
      },
      body: 'device_type=pc',
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const platform_uid = data?.result?.platform_uid
    const platform_token = data?.result?.platform_token
    if (!platform_uid || !platform_token) throw new Error('missing TVer platform token')
    return { platform_uid, platform_token }
  } finally {
    clearTimeout(timer)
  }
}

async function fromTVerSearch(keyword: string): Promise<FeedItem[]> {
  // DDG → Bing desktop — both use desktop UA which Bing requires for proper DOM
  for (const [label, fetchFn] of [
    ['ddg',  () => fetchDDGLite(`site:tver.jp ${keyword}`)],
    ['bing', () => fetchBingDesktop(`site:tver.jp ${keyword}`)],
    ['google', () => fetchGoogleSearch(`site:tver.jp ${keyword}`)],
  ] as [string, () => Promise<string>][]) {
    try {
      const html = await fetchFn()
      const links = extractBingWebLinks(html, url => /tver\.jp\/(?:episodes|series)\//i.test(url))
      const relevant = links
        .map(({ url, title }) => ({ url, title: cleanTVerSearchTitle(title) }))
        .filter(({ title }) => title && textMatchesKeyword(title, keyword))
      console.log(`[tver:${label}] links=${links.length} relevant=${relevant.length}`)
      if (relevant.length > 0) {
        return relevant.slice(0, 10).map(({ url, title }) => ({
          id: `tver:${episodeId(url)}`,
          platform: 'tver',
          url: url.split('?')[0],
          title: title || null,
          content_text: null, author: null, thumbnail_url: null,
          media_type: 'video', published_at: now(),
          watch_term_keyword: keyword, fetched_at: now(),
        }))
      }
    } catch (e) {
      console.log(`[tver:${label}] failed:`, e)
    }
  }
  return []
}

function tverSearchItem(keyword: string): FeedItem {
  return {
    id: `tver:search:${encodeURIComponent(keyword)}`,
    platform: 'tver',
    url: `https://tver.jp/search/${encodeURIComponent(keyword)}`,
    title: `TVer search: ${keyword}`,
    content_text: keyword,
    author: null,
    thumbnail_url: null,
    media_type: 'video',
    published_at: now(),
    watch_term_keyword: keyword,
    fetched_at: now(),
  }
}

export async function scrapeTVer(keyword: string, _mode: string): Promise<FeedItem[]> {
  for (const [label, fn] of [
    ['api',    () => fromTVerAPI(keyword)],
    ['search', () => fromTVerSearch(keyword)],
  ] as [string, () => Promise<FeedItem[]>][]) {
    try {
      const items = await fn()
      if (items.length > 0) return items
    } catch (e) {
      console.log(`[tver:${label}] failed:`, e)
    }
  }

  // Last resort: Bing News RSS (rarely has tver.jp episodes, but worth trying)
  try {
    const rssItems = await fetchBingNewsRSS(`${keyword} site:tver.jp`)
    const matched = rssItems
      .map(i => ({ ...i, title: cleanTVerSearchTitle(i.title) }))
      .filter(i => /tver\.jp\/(?:episodes|series)\//i.test(i.url) && i.title && textMatchesKeyword(i.title, keyword))
    console.log(`[tver:rss] matched=${matched.length}`)
    return matched.slice(0, 10).map(({ url, title }) => ({
      id: `tver:${episodeId(url)}`, platform: 'tver',
      url: url.split('?')[0], title: title || null,
      content_text: null, author: null, thumbnail_url: null,
      media_type: 'video', published_at: now(),
      watch_term_keyword: keyword, fetched_at: now(),
    }))
  } catch (e) {
    console.log('[tver:rss] failed:', e)
  }
  return []
}
