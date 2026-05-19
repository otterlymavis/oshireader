import type { SourceItem } from './base'

const SEARCH_URL = 'https://www.googleapis.com/youtube/v3/search'

export async function fetchYouTube(
  keyword: string,
  _mode: string,
  apiKey: string,
): Promise<SourceItem[]> {
  if (!apiKey) return []

  const params = new URLSearchParams({
    part: 'snippet',
    q: keyword,
    type: 'video',
    order: 'date',
    maxResults: '25',
    key: apiKey,
  })

  const res = await fetch(`${SEARCH_URL}?${params}`)
  if (!res.ok) throw new Error(`YouTube API ${res.status}: ${await res.text()}`)
  const data: { items?: any[] } = await res.json()

  return (data.items ?? []).flatMap((raw) => {
    const vidId: string | undefined = raw.id?.videoId
    if (!vidId) return []
    const snippet = raw.snippet ?? {}
    const published = new Date(snippet.publishedAt)
    const thumb: string | undefined = snippet.thumbnails?.medium?.url
    return [{
      platform: 'youtube',
      item_id: vidId,
      url: `https://www.youtube.com/watch?v=${vidId}`,
      published_at: published,
      media_type: 'video',
      author: snippet.channelTitle ?? null,
      title: snippet.title ?? null,
      content_text: snippet.description ?? null,
      thumbnail_url: thumb ?? null,
    } satisfies SourceItem]
  })
}
