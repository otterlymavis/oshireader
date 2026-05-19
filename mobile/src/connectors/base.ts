export interface SourceItem {
  platform: string
  item_id: string
  url: string
  published_at: Date
  media_type: string
  author?: string | null
  title?: string | null
  content_text?: string | null
  thumbnail_url?: string | null
}
