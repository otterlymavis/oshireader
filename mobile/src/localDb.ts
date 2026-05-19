import AsyncStorage from '@react-native-async-storage/async-storage'

export interface WatchTerm {
  id: string
  keyword: string
  collection_mode: 'all_info' | 'media_only'
  is_active: boolean
  notify_on_new: boolean
  created_at: string
}

export interface FeedItem {
  id: string
  platform: string
  url: string
  title: string | null
  content_text: string | null
  author: string | null
  thumbnail_url: string | null
  media_type: string
  published_at: string
  watch_term_keyword: string
  fetched_at: string
}

const TERMS_KEY = '@otterpia:terms'
const ITEMS_KEY = '@otterpia:items'
const MAX_ITEMS = 600
const STRICT_KEYWORD_PLATFORMS = new Set(['mdpr', 'news', 'tver'])

const uid = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

// â”€â”€ Watch Terms â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export async function getTerms(): Promise<WatchTerm[]> {
  const raw = await AsyncStorage.getItem(TERMS_KEY)
  return raw ? (JSON.parse(raw) as WatchTerm[]) : []
}

export async function saveTerm(data: {
  keyword: string
  collection_mode: 'all_info' | 'media_only'
}): Promise<WatchTerm> {
  const terms = await getTerms()
  const term: WatchTerm = {
    id: uid(),
    keyword: data.keyword.trim(),
    collection_mode: data.collection_mode,
    is_active: true,
    notify_on_new: false,
    created_at: new Date().toISOString(),
  }
  await AsyncStorage.setItem(TERMS_KEY, JSON.stringify([term, ...terms]))
  return term
}

export async function updateTerm(
  id: string,
  patch: Partial<Pick<WatchTerm, 'is_active' | 'collection_mode' | 'notify_on_new'>>,
): Promise<void> {
  const terms = await getTerms()
  await AsyncStorage.setItem(
    TERMS_KEY,
    JSON.stringify(terms.map(t => (t.id === id ? { ...t, ...patch } : t))),
  )
}

export async function deleteTerm(id: string): Promise<void> {
  const terms = await getTerms()
  const term = terms.find(t => t.id === id)
  await AsyncStorage.setItem(TERMS_KEY, JSON.stringify(terms.filter(t => t.id !== id)))
  if (term) {
    const items = await getItems()
    await AsyncStorage.setItem(
      ITEMS_KEY,
      JSON.stringify(items.filter(i => i.watch_term_keyword !== term.keyword)),
    )
  }
}

// â”€â”€ Feed Items â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export async function getItems(): Promise<FeedItem[]> {
  const raw = await AsyncStorage.getItem(ITEMS_KEY)
  return raw ? (JSON.parse(raw) as FeedItem[]) : []
}

export async function mergeItems(newItems: FeedItem[]): Promise<number> {
  const existing = await getItems()
  const itemKey = (i: FeedItem) => `${i.id}::${i.watch_term_keyword || ''}`
  const cleanedNewItems = newItems.map(normalizeFeedItem).filter(i => !isSearchPageFallback(i))
  const incomingByKey = new Map(cleanedNewItems.map(i => [itemKey(i), i]))
  const seen = new Set(existing.map(itemKey))
  const fresh = cleanedNewItems.filter(i => i.id && !seen.has(itemKey(i)))
  let updated = 0
  const refreshed = existing.map(item => {
    const incoming = incomingByKey.get(itemKey(item))
    if (!incoming) return normalizeFeedItem(item)
    const next = mergeKnownItem(item, incoming)
    if (next !== item) updated += 1
    return next
  })
  const byPlatform = fresh.reduce<Record<string, number>>((acc, i) => {
    acc[i.platform] = (acc[i.platform] ?? 0) + 1
    return acc
  }, {})
  console.log(`[mergeItems] incoming=${newItems.length} fresh=${fresh.length} updated=${updated} byPlatform=${JSON.stringify(byPlatform)}`)
  const merged = [...fresh, ...refreshed].slice(0, MAX_ITEMS)
  await AsyncStorage.setItem(ITEMS_KEY, JSON.stringify(merged))
  return fresh.length
}

export async function deleteFeedItem(id: string, watchTermKeyword?: string): Promise<void> {
  const items = await getItems()
  await AsyncStorage.setItem(
    ITEMS_KEY,
    JSON.stringify(items.filter(i =>
      i.id !== id || (watchTermKeyword !== undefined && i.watch_term_keyword !== watchTermKeyword),
    )),
  )
}

function mergeKnownItem(existing: FeedItem, incoming: FeedItem): FeedItem {
  const currentTitle = cleanDisplayTitle(existing.title, existing.platform)
  const nextTitle = cleanDisplayTitle(incoming.title, incoming.platform)
  const shouldReplaceTitle =
    Boolean(nextTitle) &&
    (!currentTitle || isBadDisplayTitle(currentTitle) || currentTitle.includes('...') || nextTitle.length > currentTitle.length + 8)
  const next = {
    ...existing,
    title: shouldReplaceTitle ? nextTitle : currentTitle || existing.title,
    content_text: incoming.content_text || existing.content_text,
    author: incoming.author || existing.author,
    thumbnail_url: incoming.thumbnail_url || existing.thumbnail_url,
    // Keep the earlier date: real publish dates are always older than scrape-time placeholders.
    // This prevents repeated refreshes from bumping items to "just now".
    published_at: (existing.published_at && incoming.published_at)
      ? (incoming.published_at < existing.published_at ? incoming.published_at : existing.published_at)
      : (existing.published_at || incoming.published_at),
    fetched_at: incoming.fetched_at || existing.fetched_at,
  }
  return JSON.stringify(next) === JSON.stringify(existing) ? existing : next
}

function isBadDisplayTitle(title: string): boolean {
  return /[\uFFFD]/.test(title)
}

function normalizeFeedItem(item: FeedItem): FeedItem {
  const cleanTitle = cleanDisplayTitle(item.title, item.platform)
  return {
    ...item,
    title: cleanTitle || (isBadDisplayTitle(item.title ?? '') || isUrlLikeText(item.title ?? '') ? null : item.title),
  }
}

function isSearchPageFallback(item: FeedItem): boolean {
  const title = cleanDisplayTitle(item.title, item.platform).toLowerCase()
  return (
    /^search:/.test(item.id) ||
    /:search:/.test(item.id) ||
    /(?:^|\s)(?:note|twitter|x|tver|yahoo|instagram|threads|tiktok)\s+search\s*:/.test(title)
  )
}

function isBareAddressItem(item: FeedItem): boolean {
  if (item.platform !== 'yahoonews') return false
  const title = cleanDisplayTitle(item.title, item.platform)
  const content = cleanDisplayTitle(item.content_text, item.platform)
  return (!title || isUrlLikeText(title)) && (!content || isUrlLikeText(content))
}

function isUrlLikeText(text: string): boolean {
  return /^https?:\/\//i.test(text.trim()) || /(?:news\.yahoo\.co\.jp|news\.google\.com)\/\S+/i.test(text)
}

function cleanDisplayTitle(title: string | null | undefined, platform: string): string {
  let text = (title ?? '')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)))
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  if (isBadDisplayTitle(text)) return ''
  if (!text) return ''
  if (platform === 'yahoonews') {
    text = text
      .replace(/\s*-\s*Yahoo!ãƒ‹ãƒ¥ãƒ¼ã‚¹\s*$/i, '')
      .replace(/\s*\([^)]*ãƒ‹ãƒ¥ãƒ¼ã‚¹\)\s*-\s*Yahoo!ãƒ‹ãƒ¥ãƒ¼ã‚¹\s*$/i, '')
  }
  if (platform === 'yahoonews') {
    text = text
      .replace(/\s*-\s*Yahoo!\u30cb\u30e5\u30fc\u30b9\s*$/i, '')
      .replace(/\s*\([^)]*\u30cb\u30e5\u30fc\u30b9\)\s*-\s*Yahoo!\u30cb\u30e5\u30fc\u30b9\s*$/i, '')
  }
  if (platform === 'mdpr') {
    text = text
      .replace(/\s*-\s*ãƒ¢ãƒ‡ãƒ«ãƒ—ãƒ¬ã‚¹\s*$/, '')
      .replace(/\s*\|\s*ãƒ¢ãƒ‡ãƒ«ãƒ—ãƒ¬ã‚¹\s*$/, '')
      .replace(/\s*ãƒ¢ãƒ‡ãƒ«ãƒ—ãƒ¬ã‚¹\s*$/, '')
  }
  return text.trim()
}

// â”€â”€ Subscribed Platforms â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const SUBS_KEY = '@otterpia:subscribed_platforms'
const SUBS_ALL_DEFAULT_MIGRATION_KEY = '@otterpia:subscribed_platforms_all_default_v1'
const SUBS_SITE_NEWS_MIGRATION_KEY = '@otterpia:subscribed_platforms_site_news_v1'

export const ALL_PLATFORM_IDS = [
  'youtube', 'niconico', 'tver', 'note',
  'girlschannel', '5ch', 'togetter', 'news', 'custom',
  'yahoonews', 'mdpr',
]

const PREVIOUS_DEFAULT_PLATFORM_IDS = [
  '5ch', 'girlschannel', 'youtube', 'yahoonews', 'tver', 'niconico',
]
const PLATFORM_IDS_BEFORE_SITE_NEWS = [
  'youtube', 'niconico', 'tver', 'note',
  'girlschannel', '5ch', 'togetter', 'news', 'custom',
  'yahoonews', 'mdpr',
]
const SITE_NEWS_DEFAULT_PLATFORM_IDS: string[] = []

export async function getSubscribedPlatforms(): Promise<string[]> {
  const [raw, migrated, siteNewsMigrated] = await Promise.all([
    AsyncStorage.getItem(SUBS_KEY),
    AsyncStorage.getItem(SUBS_ALL_DEFAULT_MIGRATION_KEY),
    AsyncStorage.getItem(SUBS_SITE_NEWS_MIGRATION_KEY),
  ])
  if (!raw) {
    await AsyncStorage.multiSet([
      [SUBS_ALL_DEFAULT_MIGRATION_KEY, '1'],
      [SUBS_SITE_NEWS_MIGRATION_KEY, '1'],
    ])
    return [...ALL_PLATFORM_IDS]
  }
  const ids = JSON.parse(raw) as string[]
  const cleaned = ids.filter((id, index) => ALL_PLATFORM_IDS.includes(id) && ids.indexOf(id) === index)
  const wasPreviousDefault =
    cleaned.length === PREVIOUS_DEFAULT_PLATFORM_IDS.length &&
    PREVIOUS_DEFAULT_PLATFORM_IDS.every(id => cleaned.includes(id))
  if (!migrated || wasPreviousDefault) {
    await AsyncStorage.multiSet([
      [SUBS_KEY, JSON.stringify(ALL_PLATFORM_IDS)],
      [SUBS_ALL_DEFAULT_MIGRATION_KEY, '1'],
      [SUBS_SITE_NEWS_MIGRATION_KEY, '1'],
    ])
    return [...ALL_PLATFORM_IDS]
  }
  if (!siteNewsMigrated) {
    const missingSiteNews = SITE_NEWS_DEFAULT_PLATFORM_IDS.filter(id => !cleaned.includes(id))
    if (missingSiteNews.length > 0) {
      const next = [...cleaned, ...missingSiteNews]
      await AsyncStorage.multiSet([
        [SUBS_KEY, JSON.stringify(next)],
        [SUBS_SITE_NEWS_MIGRATION_KEY, '1'],
      ])
      return next
    }
    await AsyncStorage.setItem(SUBS_SITE_NEWS_MIGRATION_KEY, '1')
  }
  return cleaned
}

export async function setSubscribedPlatforms(ids: string[]): Promise<void> {
  await AsyncStorage.multiSet([
    [SUBS_KEY, JSON.stringify(ids)],
    [SUBS_ALL_DEFAULT_MIGRATION_KEY, '1'],
    [SUBS_SITE_NEWS_MIGRATION_KEY, '1'],
  ])
}

// â”€â”€ Feed Query â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export async function queryFeed(opts: {
  keyword?: string | null
  days?: number
}): Promise<FeedItem[]> {
  const [all, subs] = await Promise.all([getItems(), getSubscribedPlatforms()])
  const days = opts.days ?? 30
  const cutoff = days > 0
    ? new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString()
    : null
  return all
    .map(normalizeFeedItem)
    .filter(i => !isSearchPageFallback(i))
    .filter(i => !isBareAddressItem(i))
    .filter(i => !cutoff || i.published_at >= cutoff || i.platform === '5ch' || i.platform === 'girlschannel' || i.platform === 'togetter')
    .filter(i => !(i.platform === 'yahoonews' && /:search:/.test(i.id)))
    .filter(i => i.platform === 'custom' || !opts.keyword || i.watch_term_keyword === opts.keyword)
    .filter(i => !STRICT_KEYWORD_PLATFORMS.has(i.platform) || !i.watch_term_keyword || itemMatchesKeyword(i, i.watch_term_keyword))
    .filter(i => {
      if (i.platform === 'news' || i.platform.startsWith('news:')) return subs.includes('news')
      return subs.includes(i.platform)
    })
    .sort((a, b) => b.published_at.localeCompare(a.published_at))
    .slice(0, 500)
}

function normalizeItemText(text: string): string {
  return text
    .toLowerCase()
    .replace(/&amp;/g, '&')
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)))
    .replace(/\s+/g, '')
}

function itemMatchesKeyword(item: FeedItem, keyword: string): boolean {
  const haystack = normalizeItemText(`${item.title ?? ''} ${item.content_text ?? ''}`)
  const needle = normalizeItemText(keyword)
  if (!needle) return true
  if (haystack.includes(needle)) return true
  const parts = keyword.split(/[\u3000\s]+/).map(normalizeItemText).filter(Boolean)
  return parts.length > 1 && parts.every(part => haystack.includes(part))
}

// â”€â”€ Saved Pages â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const SAVED_KEY = '@otterpia:saved'

export interface SavedPage {
  id: string
  url: string
  title: string | null
  platform: string
  saved_at: string
}

export async function getSaved(): Promise<SavedPage[]> {
  const raw = await AsyncStorage.getItem(SAVED_KEY)
  return raw ? (JSON.parse(raw) as SavedPage[]) : []
}

export async function toggleSaved(item: FeedItem): Promise<boolean> {
  const saved = await getSaved()
  const exists = saved.some(s => s.id === item.id)
  if (exists) {
    await AsyncStorage.setItem(SAVED_KEY, JSON.stringify(saved.filter(s => s.id !== item.id)))
    return false
  }
  const page: SavedPage = {
    id: item.id,
    url: item.url,
    title: item.title,
    platform: item.platform,
    saved_at: new Date().toISOString(),
  }
  await AsyncStorage.setItem(SAVED_KEY, JSON.stringify([page, ...saved]))
  return true
}

// â”€â”€ Custom Tracked URLs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const CUSTOM_KEY = '@otterpia:custom_urls'

export interface CustomUrl {
  id: string
  url: string
  title: string | null
  added_at: string
}

export async function getCustomUrls(): Promise<CustomUrl[]> {
  const raw = await AsyncStorage.getItem(CUSTOM_KEY)
  return raw ? (JSON.parse(raw) as CustomUrl[]) : []
}

export async function addCustomUrl(url: string, title: string): Promise<void> {
  const list = await getCustomUrls()
  const id = `custom:${encodeURIComponent(url).slice(0, 60)}`
  if (list.some(c => c.id === id)) return
  const entry: CustomUrl = { id, url, title: title.trim() || null, added_at: new Date().toISOString() }
  await AsyncStorage.setItem(CUSTOM_KEY, JSON.stringify([entry, ...list]))
}

export async function removeCustomUrl(id: string): Promise<void> {
  const list = await getCustomUrls()
  await AsyncStorage.setItem(CUSTOM_KEY, JSON.stringify(list.filter(c => c.id !== id)))
}

export async function removeSaved(id: string): Promise<void> {
  const saved = await getSaved()
  await AsyncStorage.setItem(SAVED_KEY, JSON.stringify(saved.filter(s => s.id !== id)))
}

// â”€â”€ Content Cache (offline reading) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const CACHE_PREFIX = '@otterpia:cache:'

export async function saveContentCache(id: string, html: string): Promise<void> {
  await AsyncStorage.setItem(CACHE_PREFIX + id, html)
}

export async function getContentCache(id: string): Promise<string | null> {
  return AsyncStorage.getItem(CACHE_PREFIX + id)
}

export async function removeContentCache(id: string): Promise<void> {
  await AsyncStorage.removeItem(CACHE_PREFIX + id)
}

// â”€â”€ Oshi Avatars â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const AVATARS_KEY = '@otterpia:oshi_avatars'

export async function getOshiAvatars(): Promise<Record<string, string>> {
  const raw = await AsyncStorage.getItem(AVATARS_KEY)
  return raw ? (JSON.parse(raw) as Record<string, string>) : {}
}

export async function setOshiAvatar(keyword: string, imageUrl: string): Promise<void> {
  const avatars = await getOshiAvatars()
  await AsyncStorage.setItem(AVATARS_KEY, JSON.stringify({ ...avatars, [keyword]: imageUrl }))
}

export async function removeOshiAvatar(keyword: string): Promise<void> {
  const avatars = await getOshiAvatars()
  delete avatars[keyword]
  await AsyncStorage.setItem(AVATARS_KEY, JSON.stringify(avatars))
}

// â”€â”€ Wallpaper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const WALLPAPER_KEY = '@otterpia:wallpaper'

export async function getWallpaper(): Promise<string | null> {
  return AsyncStorage.getItem(WALLPAPER_KEY)
}

export async function setWallpaper(url: string | null): Promise<void> {
  if (url) await AsyncStorage.setItem(WALLPAPER_KEY, url)
  else await AsyncStorage.removeItem(WALLPAPER_KEY)
}

// â”€â”€ Avatar Compositions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface AvatarLayer {
  id: string
  imageUrl: string
  x: number
  y: number
  scale: number
  cropX?: number
  cropY?: number
  cropScale?: number
  rotation?: number
  zIndex: number
}

const COMPOSITIONS_KEY = '@otterpia:compositions'

export async function getOshiCompositions(): Promise<Record<string, AvatarLayer[]>> {
  const raw = await AsyncStorage.getItem(COMPOSITIONS_KEY)
  return raw ? (JSON.parse(raw) as Record<string, AvatarLayer[]>) : {}
}

export async function setOshiComposition(keyword: string, layers: AvatarLayer[]): Promise<void> {
  const all = await getOshiCompositions()
  await AsyncStorage.setItem(COMPOSITIONS_KEY, JSON.stringify({ ...all, [keyword]: layers }))
}

// â”€â”€ Sources Display Order â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const SOURCES_ORDER_KEY = '@otterpia:sources_order'

export async function getSourcesOrder(): Promise<string[] | null> {
  const raw = await AsyncStorage.getItem(SOURCES_ORDER_KEY)
  return raw ? (JSON.parse(raw) as string[]) : null
}

export async function setSourcesOrder(ids: string[]): Promise<void> {
  await AsyncStorage.setItem(SOURCES_ORDER_KEY, JSON.stringify(ids))
}

// â”€â”€ Stats â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export async function getStats(): Promise<{ total: number; by_platform: Record<string, number> }> {
  const items = await getItems()
  const by_platform: Record<string, number> = {}
  for (const i of items) {
    const key = i.platform.startsWith('news:') ? 'news' : i.platform
    by_platform[key] = (by_platform[key] ?? 0) + 1
  }
  return { total: items.length, by_platform }
}
