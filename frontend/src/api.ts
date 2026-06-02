export interface WatchTerm {
  id: number
  keyword: string
  aliases: string[]
  language_hint: string | null
  collection_mode: 'all_info' | 'media_only'
  is_active: boolean
  created_at: string
}

export interface SourceItem {
  id: string
  platform: string
  url: string
  published_at: string
  author: string | null
  title: string | null
  content_text: string | null
  media_type: string | null
  thumbnail_url: string | null
}

export interface FeedItem {
  match_id: number
  watch_term_id: number
  watch_term_keyword: string
  item: SourceItem
  matched_at: string
}

export interface AdminStats {
  items_total: number
  matches_total: number
  watch_terms: Pick<WatchTerm, 'id' | 'keyword' | 'is_active'>[]
  items_by_platform: Record<string, number>
}

const BASE = '/api'

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<T>
}

export const fetchWatchTerms = (): Promise<WatchTerm[]> =>
  fetch(`${BASE}/watch-terms/`).then(json<WatchTerm[]>)

export const createWatchTerm = (data: {
  keyword: string
  collection_mode: 'all_info' | 'media_only'
}): Promise<WatchTerm> =>
  fetch(`${BASE}/watch-terms/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }).then(json<WatchTerm>)

export const deleteWatchTerm = (id: number): Promise<void> =>
  fetch(`${BASE}/watch-terms/${id}`, { method: 'DELETE' }).then((r) => {
    if (!r.ok) throw new Error(`Delete failed: ${r.status}`)
  })

export const updateWatchTerm = (
  id: number,
  data: Partial<Pick<WatchTerm, 'is_active' | 'collection_mode'>>,
): Promise<WatchTerm> =>
  fetch(`${BASE}/watch-terms/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }).then(json<WatchTerm>)

export const fetchAdminStats = (): Promise<AdminStats> =>
  fetch(`${BASE}/admin/stats`).then(json<AdminStats>)

export const triggerPoll = (): Promise<{ status: string }> =>
  fetch(`${BASE}/admin/poll`, { method: 'POST' }).then(json<{ status: string }>)

export const fetchFeed = (params: {
  term_id?: number
  platform?: string
  media_type?: string
  limit?: number
  offset?: number
}): Promise<FeedItem[]> => {
  const q = new URLSearchParams()
  if (params.term_id != null) q.set('term_id', String(params.term_id))
  if (params.platform) q.set('platform', params.platform)
  if (params.media_type) q.set('media_type', params.media_type)
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return fetch(`${BASE}/feed/?${q}`).then(json<FeedItem[]>)
}
