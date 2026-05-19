import { API_BASE } from './config'

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

export interface Credential {
  platform: string
  has_bearer_token: boolean
  has_api_key: boolean
  updated_at: string | null
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

export const fetchWatchTerms = (): Promise<WatchTerm[]> =>
  fetch(`${API_BASE}/api/watch-terms/`).then(json<WatchTerm[]>)

export const createWatchTerm = (data: {
  keyword: string
  collection_mode: 'all_info' | 'media_only'
}): Promise<WatchTerm> =>
  fetch(`${API_BASE}/api/watch-terms/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }).then(json<WatchTerm>)

export const deleteWatchTerm = (id: number): Promise<void> =>
  fetch(`${API_BASE}/api/watch-terms/${id}`, { method: 'DELETE' }).then((r) => {
    if (!r.ok) throw new Error(`${r.status}`)
  })

export const updateWatchTerm = (
  id: number,
  data: Partial<Pick<WatchTerm, 'is_active' | 'collection_mode'>>,
): Promise<WatchTerm> =>
  fetch(`${API_BASE}/api/watch-terms/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }).then(json<WatchTerm>)

export const fetchFeed = (params: {
  term_id?: number
  platform?: string
  limit?: number
}): Promise<FeedItem[]> => {
  const q = new URLSearchParams()
  if (params.term_id != null) q.set('term_id', String(params.term_id))
  if (params.platform) q.set('platform', params.platform)
  q.set('limit', String(params.limit ?? 50))
  return fetch(`${API_BASE}/api/feed/?${q}`).then(json<FeedItem[]>)
}

export const fetchCredentials = (): Promise<Credential[]> =>
  fetch(`${API_BASE}/api/credentials/`).then(json<Credential[]>)

export const checkHealth = (): Promise<{ status: string }> =>
  fetch(`${API_BASE}/api/health`).then(json<{ status: string }>)
