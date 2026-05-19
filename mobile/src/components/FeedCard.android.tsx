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
}

function makeStyles(t: Theme) {
  return StyleSheet.create({
    // M3 Filled card — surface container, elevation via shadow + tonal bg
    card: {
      backgroundColor: t.card,
      borderRadius: 12,
      overflow: 'hidden',
      elevation: 1,
      marginHorizontal: 0,
    },
    inner: { flex: 1, paddingHorizontal: 12, paddingVertical: 10 },
    // Thin left accent line (M3 uses state layers; we keep the colored accent for scannability)
    accentBar: { width: 4, borderRadius: 0 },
    row: { flexDirection: 'row', flex: 1 },
    body: { flex: 1 },
    // Meta row: platform chip + time + keyword
    metaRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 6, flexWrap: 'wrap' },
    chip: {
      flexDirection: 'row', alignItems: 'center', gap: 3,
      paddingHorizontal: 8, paddingVertical: 3,
      borderRadius: 8,
    },
    chipIcon: { fontSize: 12 },
    chipText: { fontSize: 11, fontWeight: '700' },
    time: { fontSize: 11, color: t.textMuted },
    term: {
      fontSize: 11, fontWeight: '600', flexShrink: 1,
      paddingHorizontal: 6, paddingVertical: 2, borderRadius: 6,
    },
    // Title — M3 Body Large
    title: { fontSize: 15, fontWeight: '700', lineHeight: 22 },
    excerpt: { fontSize: 13, color: t.textSub, lineHeight: 20, marginTop: 3 },
    author: { fontSize: 12, marginTop: 4 },
    // Actions
    actions: { marginLeft: 'auto', flexDirection: 'row', alignItems: 'center', gap: 4 },
    actionBtn: {
      width: 36, height: 36, borderRadius: 18,
      alignItems: 'center', justifyContent: 'center',
    },
    bookmarkIcon: { fontSize: 16, opacity: 0.35 },
    bookmarkSaved: { opacity: 1 },
  })
}

export function FeedCard({ feedItem: item, isSaved = false, onToggleSave, onDelete }: Props) {
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
      android_ripple={{ color: accentColor + '22', foreground: true, borderless: false }}
      onPress={() => navigation.navigate('Reader', {
        url: item.url,
        title: item.title ?? '',
        id: item.id,
        platform: item.platform,
      })}
      style={s.card}
    >
      <View style={s.row}>
        <View style={[s.accentBar, { backgroundColor: accentColor }]} />
        <View style={s.inner}>
          <View style={s.body}>
            <View style={s.metaRow}>
              <View style={[s.chip, { backgroundColor: badgeBg }]}>
                <Text style={s.chipIcon}>{colors.icon}</Text>
                <Text style={[s.chipText, { color: badgeFg }]}>
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
                      onPress={(e) => { e.stopPropagation(); onToggleSave() }}
                      android_ripple={{ color: theme.primary + '33', borderless: true, radius: 20 }}
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
                      onPress={(e) => { e.stopPropagation(); onDelete() }}
                      android_ripple={{ color: '#DC262633', borderless: true, radius: 20 }}
                      accessibilityRole="button"
                      accessibilityLabel="Delete feed item"
                      style={[s.actionBtn, { backgroundColor: theme.mode === 'dark' ? '#3F1D1D' : '#FEE2E2' }]}
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
      </View>
    </Pressable>
  )
}
