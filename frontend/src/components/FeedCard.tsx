import type { FeedItem } from '../api'

const PLATFORM_BADGE: Record<string, string> = {
  youtube: 'bg-red-100 text-red-700',
}

function timeAgo(iso: string): string {
  const mins = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60_000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export function FeedCard({ feedItem }: { feedItem: FeedItem }) {
  const { item } = feedItem
  return (
    <a
      href={item.url}
      target="_blank"
      rel="noopener noreferrer"
      className="flex gap-3 p-3 bg-white rounded-xl shadow-sm hover:shadow transition-shadow"
    >
      {item.thumbnail_url && (
        <img
          src={item.thumbnail_url}
          alt=""
          className="w-32 h-20 object-cover rounded-lg flex-shrink-0 bg-gray-100"
        />
      )}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span
            className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              PLATFORM_BADGE[item.platform] ?? 'bg-gray-100 text-gray-600'
            }`}
          >
            {item.platform}
          </span>
          {item.media_type && (
            <span className="text-xs text-gray-400">{item.media_type}</span>
          )}
          <span className="text-xs text-gray-400">{timeAgo(item.published_at)}</span>
          <span className="text-xs text-violet-400 ml-auto">{feedItem.watch_term_keyword}</span>
        </div>
        {item.title && (
          <p className="text-sm font-semibold text-gray-900 line-clamp-2 leading-snug">{item.title}</p>
        )}
        {item.author && <p className="text-xs text-gray-500 mt-1">{item.author}</p>}
      </div>
    </a>
  )
}
