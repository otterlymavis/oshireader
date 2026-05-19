import type { SourceItem } from './base'

const SEARCH_URL = 'https://api.twitter.com/2/tweets/search/recent'

function toReaderUrl(username: string, tweetId: string): string {
  return `https://fixupx.com/${username}/status/${tweetId}`
}

export async function fetchTwitter(
  keyword: string,
  mode: string,
  bearerToken: string,
): Promise<SourceItem[]> {
  if (!bearerToken) return []

  const query = mode === 'media_only' ? `${keyword} has:media` : keyword
  const params = new URLSearchParams({
    query,
    max_results: '25',
    'tweet.fields': 'created_at,author_id,text',
    expansions: 'author_id,attachments.media_keys',
    'user.fields': 'username',
    'media.fields': 'preview_image_url,url',
  })

  const res = await fetch(`${SEARCH_URL}?${params}`, {
    headers: { Authorization: `Bearer ${bearerToken}` },
  })
  if (!res.ok) throw new Error(`Twitter API ${res.status}: ${await res.text()}`)
  const data: { data?: any[]; includes?: any } = await res.json()

  const users: Record<string, any> = {}
  for (const u of data.includes?.users ?? []) users[u.id] = u

  const mediaMap: Record<string, any> = {}
  for (const m of data.includes?.media ?? []) mediaMap[m.media_key] = m

  return (data.data ?? []).map((tweet) => {
    const user = users[tweet.author_id] ?? {}
    const username: string = user.username ?? ''
    const published = new Date(tweet.created_at)

    let thumb: string | null = null
    for (const key of tweet.attachments?.media_keys ?? []) {
      const m = mediaMap[key]
      thumb = m?.preview_image_url ?? m?.url ?? null
      if (thumb) break
    }

    return {
      platform: 'twitter',
      item_id: tweet.id as string,
      url: username ? toReaderUrl(username, tweet.id) : `https://fixupx.com/i/status/${tweet.id}`,
      published_at: published,
      media_type: thumb ? 'video' : 'text',
      author: username ? `@${username}` : null,
      title: null,
      content_text: tweet.text ?? null,
      thumbnail_url: thumb,
    } satisfies SourceItem
  })
}
