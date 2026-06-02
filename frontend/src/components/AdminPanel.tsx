import { useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchAdminStats, triggerPoll } from '../api'

interface Props {
  platform: string
  mediaType: string
  onPlatformChange: (value: string) => void
  onMediaTypeChange: (value: string) => void
}

const MEDIA_TYPES = ['video', 'image', 'text', 'article']

export function AdminPanel({
  platform,
  mediaType,
  onPlatformChange,
  onMediaTypeChange,
}: Props) {
  const qc = useQueryClient()
  const { data: stats, isLoading } = useQuery({
    queryKey: ['admin-stats'],
    queryFn: fetchAdminStats,
    refetchInterval: 60_000,
  })

  const poll = useMutation({
    mutationFn: triggerPoll,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-stats'] })
      qc.invalidateQueries({ queryKey: ['feed'] })
    },
  })

  const platforms = useMemo(() => {
    return Object.keys(stats?.items_by_platform ?? {}).sort((a, b) => a.localeCompare(b))
  }, [stats?.items_by_platform])

  const activeTerms = stats?.watch_terms.filter((term) => term.is_active).length ?? 0

  return (
    <section className="bg-white rounded-xl shadow-sm p-4 mb-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-2">
          <Stat label="Items" value={stats?.items_total} loading={isLoading} />
          <Stat label="Matches" value={stats?.matches_total} loading={isLoading} />
          <Stat label="Active" value={activeTerms} loading={isLoading} />
        </div>

        <div className="flex flex-wrap gap-2 ml-auto">
          <select
            className="border rounded-lg px-2 py-1.5 text-sm bg-white"
            value={platform}
            onChange={(event) => onPlatformChange(event.target.value)}
            aria-label="Filter by platform"
          >
            <option value="">All platforms</option>
            {platforms.map((name) => (
              <option key={name} value={name}>
                {name} ({stats?.items_by_platform[name] ?? 0})
              </option>
            ))}
          </select>

          <select
            className="border rounded-lg px-2 py-1.5 text-sm bg-white"
            value={mediaType}
            onChange={(event) => onMediaTypeChange(event.target.value)}
            aria-label="Filter by media type"
          >
            <option value="">All media</option>
            {MEDIA_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>

          <button
            type="button"
            onClick={() => poll.mutate()}
            disabled={poll.isPending}
            className="bg-gray-900 hover:bg-gray-800 text-white text-sm font-medium px-3 py-1.5 rounded-lg disabled:opacity-50 transition-colors"
          >
            {poll.isPending ? 'Polling...' : 'Poll now'}
          </button>
        </div>
      </div>

      {poll.isError && (
        <p className="text-xs text-red-500 mt-3">Could not start a poll. Check the backend logs.</p>
      )}
      {poll.isSuccess && (
        <p className="text-xs text-green-600 mt-3">{poll.data.status}</p>
      )}
    </section>
  )
}

function Stat({
  label,
  value,
  loading,
}: {
  label: string
  value: number | undefined
  loading: boolean
}) {
  return (
    <div className="min-w-20 rounded-lg border border-gray-100 px-3 py-2">
      <p className="text-xs text-gray-400">{label}</p>
      <p className="text-lg font-semibold text-gray-900">{loading ? '-' : value ?? 0}</p>
    </div>
  )
}
