import { useState } from 'react'
import { WatchTermForm } from './components/WatchTermForm'
import { WatchTermList } from './components/WatchTermList'
import { Feed } from './components/Feed'
import { AdminPanel } from './components/AdminPanel'

export default function App() {
  const [selectedTerm, setSelectedTerm] = useState<number | null>(null)
  const [platform, setPlatform] = useState('')
  const [mediaType, setMediaType] = useState('')

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b px-6 py-3 flex items-center gap-3 sticky top-0 z-10">
        <span className="text-2xl">🦦</span>
        <h1 className="text-lg font-bold text-violet-700 tracking-tight">Otterpia</h1>
        <span className="text-xs text-gray-400 mt-0.5">your oshi, everywhere</span>
      </header>

      <div className="max-w-4xl mx-auto px-4 py-6 flex gap-6 items-start">
        <aside className="w-56 flex-shrink-0 space-y-4 sticky top-16">
          <WatchTermForm />
          <div className="bg-white rounded-xl shadow p-3">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2 px-1">
              Watching
            </p>
            <WatchTermList selected={selectedTerm} onSelect={setSelectedTerm} />
          </div>
        </aside>

        <main className="flex-1 min-w-0">
          <AdminPanel
            platform={platform}
            mediaType={mediaType}
            onPlatformChange={setPlatform}
            onMediaTypeChange={setMediaType}
          />
          <Feed termId={selectedTerm} platform={platform} mediaType={mediaType} />
        </main>
      </div>
    </div>
  )
}
