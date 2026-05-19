import { useMemo } from 'react'
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getSaved, removeSaved, removeContentCache } from '../localDb'
import { useTheme } from '../ThemeContext'
import { useLang } from '../LangContext'
import { timeAgoI18n } from '../i18n'
import type { Theme } from '../theme'

function makeStyles(t: Theme) {
  return StyleSheet.create({
    root: { flex: 1, backgroundColor: t.bg },
    list: { padding: 14, paddingBottom: 40 },
    card: {
      backgroundColor: t.card, borderRadius: 14, overflow: 'hidden',
      shadowColor: '#000', shadowOffset: { width: 0, height: 1 },
      shadowOpacity: t.mode === 'dark' ? 0.3 : 0.06, shadowRadius: 4, elevation: 2,
    },
    cardInner: { flexDirection: 'row', alignItems: 'center', padding: 14, gap: 10 },
    cardBody: { flex: 1 },
    platform: { fontSize: 11, fontWeight: '700', color: '#92400E', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 3 },
    title: { fontSize: 13, fontWeight: '600', color: t.text, lineHeight: 18, marginBottom: 3 },
    url: { fontSize: 11, color: t.textMuted, marginBottom: 2 },
    time: { fontSize: 11, color: t.primary },
    deleteBtn: { padding: 6 },
    deleteIcon: { fontSize: 14, color: t.textMuted },
    empty: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
    emptyEmoji: { fontSize: 48, marginBottom: 12 },
    emptyTitle: { fontSize: 16, fontWeight: '600', color: t.text, marginBottom: 6 },
    emptyBody: { fontSize: 13, color: t.textMuted, textAlign: 'center', lineHeight: 20 },
  })
}

export default function SavedScreen() {
  const { theme } = useTheme()
  const { t } = useLang()
  const s = useMemo(() => makeStyles(theme), [theme])
  const qc = useQueryClient()
  const navigation = useNavigation<any>()
  const { data: saved = [] } = useQuery({ queryKey: ['saved'], queryFn: getSaved })

  function platformLabel(platform: string): string {
    if (platform === 'saved') return t('manual')
    if (platform.startsWith('news:')) return platform.replace('news:', '')
    const map: Record<string, string> = {
      girlschannel: 'GirlsChannel', togetter: 'Togetter',
      '5ch': '5ch', tver: 'TVer', youtube: 'YouTube',
      niconico: 'NicoNico', note: 'Note',
      custom: 'Custom', news: 'News',
    }
    return map[platform] ?? platform
  }

  return (
    <View style={s.root}>
      {saved.length === 0 ? (
        <View style={s.empty}>
          <Text style={s.emptyEmoji}>🔖</Text>
          <Text style={s.emptyTitle}>{t('savedEmpty')}</Text>
          <Text style={s.emptyBody}>{t('savedEmptyBody')}</Text>
        </View>
      ) : (
        <FlatList
          contentContainerStyle={s.list}
          data={saved}
          keyExtractor={p => p.id}
          ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
          renderItem={({ item }) => (
            <Pressable
              style={({ pressed }) => [s.card, pressed && { opacity: 0.8 }]}
              onPress={() => navigation.navigate('Reader', {
                url: item.url,
                title: item.title ?? '',
                id: item.id,
                platform: item.platform,
              })}
            >
              <View style={s.cardInner}>
                <View style={s.cardBody}>
                  <Text style={s.platform}>{platformLabel(item.platform)}</Text>
                  <Text style={s.title} numberOfLines={2}>{item.title ?? item.url}</Text>
                  <Text style={s.url} numberOfLines={1}>{item.url}</Text>
                  <Text style={s.time}>{timeAgoI18n(item.saved_at, t)}</Text>
                </View>
                <Pressable
                  style={s.deleteBtn}
                  hitSlop={8}
                  onPress={() => {
                    removeSaved(item.id)
                    removeContentCache(item.id)
                    qc.invalidateQueries({ queryKey: ['saved'] })
                  }}
                >
                  <Text style={s.deleteIcon}>✕</Text>
                </Pressable>
              </View>
            </Pressable>
          )}
        />
      )}
    </View>
  )
}
