import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createWatchTerm } from '../api'

export function WatchTermForm() {
  const qc = useQueryClient()
  const [keyword, setKeyword] = useState('')
  const [mode, setMode] = useState<'all_info' | 'media_only'>('all_info')

  const mut = useMutation({
    mutationFn: () => createWatchTerm({ keyword: keyword.trim(), collection_mode: mode }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['watch-terms'] })
      setKeyword('')
    },
  })

  return (
    <form
      className="flex flex-col gap-2 p-4 bg-white rounded-xl shadow"
      onSubmit={(e) => {
        e.preventDefault()
        if (keyword.trim()) mut.mutate()
      }}
    >
      <input
        className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
        placeholder="Watch term (e.g. 山田裕貴)"
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
      />
      <div className="flex gap-2">
        <select
          className="flex-1 border rounded-lg px-2 py-1.5 text-sm"
          value={mode}
          onChange={(e) => setMode(e.target.value as 'all_info' | 'media_only')}
        >
          <option value="all_info">All Info</option>
          <option value="media_only">Only Media</option>
        </select>
        <button
          type="submit"
          disabled={mut.isPending || !keyword.trim()}
          className="bg-violet-500 hover:bg-violet-600 text-white text-sm font-medium px-4 py-1.5 rounded-lg disabled:opacity-50 transition-colors"
        >
          {mut.isPending ? '…' : 'Watch'}
        </button>
      </div>
    </form>
  )
}
