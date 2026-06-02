import { useQuery } from '@tanstack/react-query'
import { fetchFeed } from '../api'
import { FeedCard } from './FeedCard'

interface Props {
  termId: number | null
  platform?: string
  mediaType?: string
}

export function Feed({ termId, platform, mediaType }: Props) {
  const { data = [], isLoading, error } = useQuery({
    queryKey: ['feed', termId, platform, mediaType],
    queryFn: () =>
      fetchFeed({
        term_id: termId ?? undefined,
        platform: platform || undefined,
        media_type: mediaType || undefined,
        limit: 50,
      }),
    refetchInterval: 60_000,
  })

  if (isLoading) return <p className="text-sm text-gray-400 p-4">Loading…</p>
  if (error)
    return (
      <p className="text-sm text-red-400 p-4">
        Error loading feed. Make sure the backend is running on port 8000.
      </p>
    )
  if (!data.length)
    return (
      <div className="p-10 text-center text-gray-400">
        <p className="text-5xl mb-3">🦦</p>
        <p className="text-sm">No items match the current feed view.</p>
        <p className="text-xs mt-1 text-gray-300">Polls every 15 minutes by default.</p>
      </div>
    )

  return (
    <div className="space-y-3">
      {data.map((fi) => (
        <FeedCard key={fi.match_id} feedItem={fi} />
      ))}
    </div>
  )
}
