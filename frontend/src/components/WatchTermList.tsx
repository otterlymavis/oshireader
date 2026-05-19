import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchWatchTerms, deleteWatchTerm, updateWatchTerm } from '../api'
import type { WatchTerm } from '../api'

interface Props {
  selected: number | null
  onSelect: (id: number | null) => void
}

export function WatchTermList({ selected, onSelect }: Props) {
  const qc = useQueryClient()
  const { data: terms = [] } = useQuery({ queryKey: ['watch-terms'], queryFn: fetchWatchTerms })

  const del = useMutation({
    mutationFn: deleteWatchTerm,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watch-terms'] }),
  })

  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) =>
      updateWatchTerm(id, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watch-terms'] }),
  })

  return (
    <ul className="space-y-1">
      <li>
        <button
          onClick={() => onSelect(null)}
          className={`w-full text-left px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
            selected === null ? 'bg-violet-100 text-violet-800' : 'hover:bg-gray-100 text-gray-700'
          }`}
        >
          All feeds
        </button>
      </li>
      {terms.map((t: WatchTerm) => (
        <li
          key={t.id}
          className={`flex items-center gap-1.5 px-3 py-2 rounded-lg transition-colors ${
            selected === t.id ? 'bg-violet-100' : 'hover:bg-gray-50'
          }`}
        >
          <button className="flex-1 text-left text-sm font-medium truncate" onClick={() => onSelect(t.id)}>
            {t.keyword}
            <span
              className={`ml-1.5 text-xs px-1.5 py-0.5 rounded ${
                t.collection_mode === 'media_only'
                  ? 'bg-blue-100 text-blue-700'
                  : 'bg-gray-100 text-gray-500'
              }`}
            >
              {t.collection_mode === 'media_only' ? 'media' : 'all'}
            </span>
          </button>
          <button
            title={t.is_active ? 'Pause' : 'Resume'}
            onClick={() => toggle.mutate({ id: t.id, is_active: !t.is_active })}
            className={`w-7 h-4 rounded-full flex-shrink-0 transition-colors ${
              t.is_active ? 'bg-violet-500' : 'bg-gray-300'
            }`}
          />
          <button
            title="Delete"
            onClick={() => {
              if (window.confirm(`Delete "${t.keyword}"?`)) del.mutate(t.id)
            }}
            className="text-gray-300 hover:text-red-400 text-xs flex-shrink-0 transition-colors"
          >
            ✕
          </button>
        </li>
      ))}
    </ul>
  )
}
