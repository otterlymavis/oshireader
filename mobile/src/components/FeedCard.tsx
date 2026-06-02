import { useMemo } from 'react'
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import type { FeedItem } from '../localDb'
import { useTheme } from '../ThemeContext'
import { useLang } from '../LangContext'
import { timeAgoI18n } from '../i18n'
import type { Theme } from '../theme'
import {
  PLATFORM_META, PLATFORM_DISPLAY, DEFAULT_META,
  platformMeta, displayPlatform,
} from './platformMeta'
import { STANDARD_STYLE } from '../styleConstants'

export { PLATFORM_META, PLATFORM_DISPLAY, DEFAULT_META }


interface Props {
  feedItem: FeedItem
  isSaved?: boolean
  onToggleSave?: () => void
  onDelete?: () => void
  onPress?: () => void
}

function makeStyles(t: Theme) {
  return StyleSheet.create({
    card: {
      backgroundColor: t.card, borderRadius: 16, overflow: 'hidden',
      flexDirection: 'row',
      borderWidth: t.mode === 'dark' ? 0 : 1,
      borderColor: t.border,
      shadowColor: t.mode === 'dark' ? '#000' : '#6B5B8A',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: t.mode === 'dark' ? 0.35 : 0.07,
      shadowRadius: 6, elevation: 2,
    },
    pressed: { opacity: 0.8, transform: [{ scale: 0.99 }] },
    accent: { width: 4 },
    inner: { flex: 1, paddingHorizontal: 12, paddingVertical: 10 },
    body: { flex: 1 },
    metaRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4, flexWrap: 'wrap' },
    badge: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 7, paddingVertical: 2, borderRadius: 999 },
    badgeIcon: { fontSize: 12 },
    badgeText: { fontSize: 11, fontWeight: '700' },
    time: { fontSize: 11, color: t.textMuted },
    term: { fontSize: 11, fontWeight: '600', flexShrink: 1, paddingHorizontal: 7, paddingVertical: 2, borderRadius: 999 },
    actions: { marginLeft: 'auto', flexDirection: 'row', alignItems: 'center', gap: 8 },
    actionBtn: { width: 28, height: 28, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
    bookmarkIcon: { fontSize: 16, opacity: 0.35 },
    bookmarkSaved: { opacity: 1 },
    title: { fontSize: 15, fontWeight: '700', lineHeight: 22 },
    excerpt: { fontSize: 13, color: t.textSub, lineHeight: 20, marginTop: 3 },
    author: { fontSize: 12, marginTop: 4 },
  })
}

export function FeedCard({ feedItem: item, isSaved = false, onToggleSave, onDelete, onPress }: Props) {
  const { theme, style } = useTheme()
  const { t } = useLang()
  const s = useMemo(() => makeStyles(theme), [theme])
  const colors = platformMeta(item.platform)
  const navigation = useNavigation<any>()
  const std = STANDARD_STYLE[theme.mode]
  const isStandard = style === 'standard'
  const badgeBg    = isStandard ? std.badgeBg : colors.bg
  const badgeFg    = isStandard ? std.badgeFg : colors.fg
  const accentColor = isStandard ? std.accent  : colors.accent
  const titleColor  = isStandard ? theme.text  : colors.fg
  const headline = (
    item.title?.trim() ||
    item.content_text?.replace(/\s+/g, ' ').trim() ||
    item.url
  )

  return (
    <Pressable
      style={({ pressed }) => [s.card, pressed && s.pressed]}
      onPress={onPress ?? (() => navigation.navigate('Reader', {
        url: item.url,
        title: item.title ?? '',
        id: item.id,
        platform: item.platform,
      }))}
    >
      <View style={[s.accent, { backgroundColor: accentColor, width: 5 }]} />
      <View style={s.inner}>
        <View style={s.body}>
          <View style={s.metaRow}>
            <View style={[s.badge, { backgroundColor: badgeBg }]}>
              <Text style={s.badgeIcon}>{colors.icon}</Text>
              <Text style={[s.badgeText, { color: badgeFg }]}>
                {displayPlatform(item.platform)}
              </Text>
            </View>
            <Text style={s.time}>{timeAgoI18n(item.published_at, t)}</Text>
            {item.watch_term_keyword ? (
              <Text style={[s.term, { backgroundColor: badgeBg, color: badgeFg }]} numberOfLines={1}>
                #{item.watch_term_keyword}
              </Text>
            ) : null}
            {(onToggleSave || onDelete) && (
              <View style={s.actions}>
                {onToggleSave && (
                  <Pressable
                    onPress={(e) => {
                      e.stopPropagation()
                      onToggleSave()
                    }}
                    style={s.actionBtn}
                    hitSlop={8}
                  >
                    <Text style={[s.bookmarkIcon, isSaved && s.bookmarkSaved]}>
                      {isSaved ? '🔖' : '🏷️'}
                    </Text>
                  </Pressable>
                )}
                {onDelete && (
                  <Pressable
                    onPress={(e) => {
                      e.stopPropagation()
                      onDelete()
                    }}
                    accessibilityRole="button"
                    accessibilityLabel="Delete feed item"
                    style={({ pressed }) => [
                      s.actionBtn,
                      { backgroundColor: theme.mode === 'dark' ? '#3F1D1D' : '#FEE2E2', opacity: pressed ? 0.7 : 1 },
                    ]}
                    hitSlop={8}
                  >
                    <Ionicons name="trash-outline" size={16} color="#DC2626" />
                  </Pressable>
                )}
              </View>
            )}
          </View>
          <Text style={[s.title, { color: titleColor }]} numberOfLines={2}>{headline}</Text>
          {!item.title && item.content_text ? null : item.content_text ? (
            <Text style={s.excerpt} numberOfLines={3}>{item.content_text}</Text>
          ) : null}
          {item.author ? (
            <Text style={[s.author, { color: accentColor }]} numberOfLines={1}>{item.author}</Text>
          ) : null}
        </View>
      </View>
    </Pressable>
  )
}
