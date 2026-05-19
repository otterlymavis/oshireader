export const PLATFORM_META: Record<string, { bg: string; fg: string; accent: string; icon: string }> = {
  youtube:      { bg: '#FFE0E0', fg: '#B30000', accent: '#E40303', icon: '▶️' },
  niconico:     { bg: '#FFF0D6', fg: '#B85C00', accent: '#FF8C00', icon: '🎬' },
  tver:         { bg: '#FFFBE0', fg: '#8B7000', accent: '#F5C400', icon: '📺' },
  note:         { bg: '#D6F5E3', fg: '#005718', accent: '#008026', icon: '📝' },
  girlschannel: { bg: '#D6E8FF', fg: '#0035B8', accent: '#750787', icon: '💗' },
  '5ch':        { bg: '#FFE0E0', fg: '#B30000', accent: '#E40303', icon: '💬' },
  togetter:     { bg: '#FFF0D6', fg: '#B85C00', accent: '#FF8C00', icon: '🧵' },
  news:         { bg: '#FFFBE0', fg: '#8B7000', accent: '#F5C400', icon: '📰' },
  custom:       { bg: '#D6F5E3', fg: '#005718', accent: '#008026', icon: '🔗' },
  yahoonews:    { bg: '#FFE0E0', fg: '#B30000', accent: '#E40303', icon: '🗞️' },
  mdpr:         { bg: '#D6E8FF', fg: '#0035B8', accent: '#004DFF', icon: '💄' },
  saved:        { bg: '#F0D6FA', fg: '#520060', accent: '#750787', icon: '🔖' },
}

export const PLATFORM_DISPLAY: Record<string, string> = {
  youtube: 'YouTube', niconico: 'ニコニコ', note: 'Note',
  '5ch': '5ch', girlschannel: 'GirlsChannel', togetter: 'Togetter',
  tver: 'TVer', news: 'ニュース', custom: 'Custom', saved: '保存済み',
}

export const DEFAULT_META = { bg: '#F3F4F6', fg: '#6B7280', accent: '#9CA3AF', icon: '🔗' }

export function platformMeta(platform: string) {
  if (platform.startsWith('news:') || platform === 'news') return PLATFORM_META['news'] ?? DEFAULT_META
  return PLATFORM_META[platform] ?? DEFAULT_META
}

export function displayPlatform(platform: string): string {
  if (platform.startsWith('news:')) return platform.replace('news:', '')
  return platform
}
