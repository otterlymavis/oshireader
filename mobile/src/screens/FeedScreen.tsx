import { useCallback, useEffect, useMemo, useRef, useState, useLayoutEffect } from 'react'
import {
  ActivityIndicator,
  Alert,
  FlatList,
  ImageBackground,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useNavigation } from '@react-navigation/native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getTerms, queryFeed, mergeItems, getSaved, toggleSaved, getSubscribedPlatforms, getWallpaper, getSourcesOrder, setSourcesOrder, deleteFeedItem, type FeedItem } from '../localDb'
import { scrapeAll } from '../scraper'
import { scrapeCustomUrls } from '../scraper/custom'
import { fetchFeed, type FeedItem as BackendFeedItem } from '../api'
import { useSettings } from '../hooks/useSettings'
import { FeedCard, PLATFORM_META, PLATFORM_DISPLAY, DEFAULT_META } from '../components/FeedCard'
import { AddUrlModal } from '../components/AddUrlModal'
import { SourceReorderModal } from '../components/SourceReorderModal'
import { useTheme } from '../ThemeContext'
import { useLang } from '../LangContext'
import type { Theme } from '../theme'
import { STANDARD_STYLE, type ColorStyle } from '../styleConstants'

function makeStyles(t: Theme) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: t.bg },
    filterBar: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      backgroundColor: t.card, paddingHorizontal: 14, paddingVertical: 10,
      borderBottomWidth: StyleSheet.hairlineWidth, borderColor: t.border,
    },
    filterBarLeft: { flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 1 },
    filterBarTitle: { fontSize: 13, fontWeight: '600', color: t.textSub },
    activeBadge: { backgroundColor: t.primary, borderRadius: 999, paddingHorizontal: 6, paddingVertical: 1 },
    activeBadgeText: { fontSize: 11, color: '#fff', fontWeight: '700' },
    activePill: { backgroundColor: t.divider, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2 },
    activePillText: { fontSize: 12, color: t.textSub, fontWeight: '500' },
    filterPanel: {
      backgroundColor: t.card, paddingHorizontal: 16, paddingTop: 12, paddingBottom: 14,
      borderBottomWidth: StyleSheet.hairlineWidth, borderColor: t.border,
    },
    sectionLabel: {
      fontSize: 9, fontWeight: '700', color: t.textMuted,
      textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 7,
    },
    grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 4 },
    chip: {
      paddingHorizontal: 10, paddingVertical: 7,
      borderRadius: 999, backgroundColor: t.divider, borderWidth: 1, borderColor: t.border,
    },
    chipActive: { backgroundColor: t.primary, borderColor: t.primary },
    chipText: { fontSize: 11, color: t.textSub, fontWeight: '500' },
    chipTextActive: { color: '#fff' },
    list: { paddingHorizontal: 16, paddingTop: 10, paddingBottom: 20 },
    empty: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
    emptyEmoji: { fontSize: 48, marginBottom: 12 },
    emptyTitle: { fontSize: 16, fontWeight: '600', color: t.text, marginBottom: 6 },
    emptyBody: { fontSize: 13, color: t.textMuted, textAlign: 'center', lineHeight: 20 },
  })
}

const MEDIA_PLATFORMS = new Set(['youtube', 'niconico', 'tver'])
const SOURCE_TILE_W = 58

interface SourceChipProps {
  id: string
  selectedPlatform: string | null
  theme: Theme
  style: ColorStyle
  onPress: (id: string) => void
}

function SourceChip({
  id,
  selectedPlatform,
  theme,
  style,
  onPress,
}: SourceChipProps) {
  const meta = PLATFORM_META[id] ?? DEFAULT_META
  const label = PLATFORM_DISPLAY[id] ?? id
  const isActive = selectedPlatform === id

  return (
    <View
      style={{
        width: SOURCE_TILE_W,
        height: 58,
      }}
    >
      <Pressable
        onPress={() => onPress(id)}
        android_ripple={Platform.OS === 'android' ? { color: meta.accent + '33', borderless: false } : undefined}
        style={({ pressed }) => ({
          width: SOURCE_TILE_W,
          height: 58,
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 10,
          backgroundColor: isActive
            ? (style === 'standard' ? STANDARD_STYLE[theme.mode].accent : meta.accent)
            : (style === 'standard' ? STANDARD_STYLE[theme.mode].badgeBg : meta.bg),
          opacity: pressed && Platform.OS === 'ios' ? 0.75 : 1,
        })}
      >
        <Text style={{ fontSize: 18, lineHeight: 22 }}>{meta.icon}</Text>
        <Text
          style={{
            fontSize: 9,
            fontWeight: isActive ? '700' : '500',
            color: isActive ? '#fff' : (style === 'standard' ? STANDARD_STYLE[theme.mode].badgeFg : meta.fg),
            textAlign: 'center',
            marginTop: 2,
          }}
          numberOfLines={1}
        >
          {label}
        </Text>
      </Pressable>
    </View>
  )
}

/** Convert a backend feed item to the local FeedItem shape. */
function toLocalItem(fi: BackendFeedItem): FeedItem {
  return {
    id: fi.item.id,
    platform: fi.item.platform,
    url: fi.item.url,
    title: fi.item.title,
    content_text: fi.item.content_text,
    author: fi.item.author,
    thumbnail_url: fi.item.thumbnail_url,
    media_type: fi.item.media_type ?? 'article',
    published_at: fi.item.published_at,
    watch_term_keyword: fi.watch_term_keyword,
    fetched_at: fi.matched_at,
  }
}

export default function FeedScreen() {
  const { theme, style } = useTheme()
  const s = useMemo(() => makeStyles(theme), [theme])
  const qc = useQueryClient()
  const navigation = useNavigation()
  const { t } = useLang()
  const { settings } = useSettings()
  const [selectedKeyword, setSelectedKeyword] = useState<string | null>(null)
  const [selectedPlatform, setSelectedPlatform] = useState<string | null>(null)
  const [mediaFilter, setMediaFilter] = useState<'all' | 'media_only'>('all')
  const [days, setDays] = useState(30)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [addUrlOpen, setAddUrlOpen] = useState(false)
  const [reorderOpen, setReorderOpen] = useState(false)
  const sourceOrderRef = useRef<string[]>([])

  const { data: terms = [] } = useQuery({ queryKey: ['watch-terms'], queryFn: getTerms })
  const { data: savedPages = [] } = useQuery({ queryKey: ['saved'], queryFn: getSaved })
  const { data: subscribedPlatforms = [] } = useQuery({
    queryKey: ['subscribed-platforms'],
    queryFn: getSubscribedPlatforms,
  })
  const { data: wallpaper = null } = useQuery({
    queryKey: ['wallpaper'],
    queryFn: getWallpaper,
  })
  const { data: sourcesOrder = null } = useQuery({
    queryKey: ['sources-order'],
    queryFn: getSourcesOrder,
  })
  const savedIds = new Set(savedPages.map(p => p.id))

  const TIME_RANGES = [
    { label: t('allTime'), days: 0 },
    { label: t('days3'),   days: 3 },
    { label: t('month1'),  days: 30 },
    { label: t('months3'), days: 90 },
    { label: t('months6'), days: 180 },
  ]

  const { data: feed = [], isLoading } = useQuery({
    queryKey: ['feed', selectedKeyword, days],
    queryFn: () => queryFeed({ keyword: selectedKeyword, days }),
  })

  const handleRefresh = async () => {
    setIsRefreshing(true)
    try {
      const activeTerms = terms.filter(tr => tr.is_active)

      const tasks: Promise<any>[] = [
        // Local on-device scrapers (always run)
        ...activeTerms.map(tr =>
          scrapeAll(tr.keyword, tr.collection_mode).then(items => mergeItems(items)),
        ),
        scrapeCustomUrls().then(items => mergeItems(items)),
      ]

      // Backend feed (only when enabled in settings)
      if (settings.useBackendFeed) {
        tasks.push(
          fetchFeed({ limit: 200 })
            .then(backendItems => mergeItems(backendItems.map(toLocalItem)))
            .catch(() => {}), // backend offline → silent fallback
        )
      }

      await Promise.allSettled(tasks)
      qc.invalidateQueries({ queryKey: ['feed'] })
    } finally {
      setIsRefreshing(false)
    }
  }

  useLayoutEffect(() => {
    navigation.setOptions({
      headerRight: () => (
        <Pressable onPress={handleRefresh} disabled={isRefreshing} style={{ padding: 6, marginRight: 8 }}>
          <Text style={{ fontSize: 20, color: theme.primary }}>{isRefreshing ? '…' : '↻'}</Text>
        </Pressable>
      ),
    })
  }, [isRefreshing, handleRefresh, theme.primary])

  const displayFeed = useMemo(() => {
    let result = feed
    if (selectedPlatform) {
      result = result.filter(i =>
        selectedPlatform === 'news'
          ? (i.platform === 'news' || i.platform.startsWith('news:'))
          : i.platform === selectedPlatform,
      )
    }
    if (mediaFilter === 'media_only') {
      result = result.filter(i => i.media_type === 'video' || MEDIA_PLATFORMS.has(i.platform))
    }
    return result
  }, [feed, selectedPlatform, mediaFilter])

  const chips = [{ id: null as string | null, keyword: t('all') }, ...terms]
  const activeCount = (selectedKeyword ? 1 : 0) + (selectedPlatform ? 1 : 0) + (mediaFilter === 'media_only' ? 1 : 0)

  // Apply stored display order to subscribed platforms
  const orderedPlatforms = useMemo(() => {
    if (!subscribedPlatforms.length) return subscribedPlatforms
    if (!sourcesOrder) return subscribedPlatforms
    const orderSet = new Set(sourcesOrder)
    const ordered = sourcesOrder.filter(id => subscribedPlatforms.includes(id))
    const unordered = subscribedPlatforms.filter(id => !orderSet.has(id))
    return [...ordered, ...unordered]
  }, [subscribedPlatforms, sourcesOrder])

  useEffect(() => {
    sourceOrderRef.current = orderedPlatforms
  }, [orderedPlatforms])

  useEffect(() => {
    if (selectedPlatform && !subscribedPlatforms.includes(selectedPlatform)) {
      setSelectedPlatform(null)
    }
  }, [selectedPlatform, subscribedPlatforms])

  const handleSaveOrder = (newOrder: string[]) => {
    sourceOrderRef.current = newOrder
    setSourcesOrder(newOrder).then(() => {
      qc.invalidateQueries({ queryKey: ['sources-order'] })
    })
  }

  const handleSourcePress = useCallback((id: string) => {
    setSelectedPlatform(current => current === id ? null : id)
  }, [])

  const handleDeleteItem = useCallback((item: FeedItem) => {
    Alert.alert('Delete item?', item.title || item.url, [
      { text: t('cancel'), style: 'cancel' },
      {
        text: t('delete'),
        style: 'destructive',
        onPress: async () => {
          await deleteFeedItem(item.id, item.watch_term_keyword)
          qc.invalidateQueries({ queryKey: ['feed'] })
        },
      },
    ])
  }, [qc, t])

  const inner = (
    <View style={[s.container, wallpaper && { backgroundColor: 'transparent' }]}>
      <Pressable style={s.filterBar} onPress={() => setFiltersOpen(v => !v)}>
        <View style={s.filterBarLeft}>
          <Ionicons name="options-outline" size={16} color={activeCount > 0 ? theme.primary : theme.textMuted} />
          <Text style={[s.filterBarTitle, activeCount > 0 && { color: theme.primary }]}>{t('filter')}</Text>
          {activeCount > 0 && (
            <View style={s.activeBadge}>
              <Text style={s.activeBadgeText}>{activeCount}</Text>
            </View>
          )}
          {selectedKeyword && (
            <View style={s.activePill}>
              <Text style={s.activePillText}>{selectedKeyword}</Text>
            </View>
          )}
          {selectedPlatform && (() => {
            const meta = PLATFORM_META[selectedPlatform] ?? DEFAULT_META
            return (
              <View style={[s.activePill, { backgroundColor: meta.bg }]}>
                <Text style={[s.activePillText, { color: meta.fg }]}>
                  {meta.icon} {PLATFORM_DISPLAY[selectedPlatform] ?? selectedPlatform}
                </Text>
              </View>
            )
          })()}
        </View>
        <Ionicons
          name={filtersOpen ? 'chevron-up' : 'chevron-down'}
          size={16}
          color={theme.textMuted}
        />
      </Pressable>

      {filtersOpen && (
        <View style={s.filterPanel}>
          <Text style={s.sectionLabel}>{t('allInfo')} / {t('mediaOnly')}</Text>
          <View style={[s.grid, { marginBottom: 12 }]}>
            <Pressable
              style={[s.chip, mediaFilter === 'all' && s.chipActive]}
              onPress={() => setMediaFilter('all')}
            >
              <Text style={[s.chipText, mediaFilter === 'all' && s.chipTextActive]}>
                📄 {t('allInfo')}
              </Text>
            </Pressable>
            <Pressable
              style={[s.chip, mediaFilter === 'media_only' && s.chipActive]}
              onPress={() => setMediaFilter('media_only')}
            >
              <Text style={[s.chipText, mediaFilter === 'media_only' && s.chipTextActive]}>
                📹 {t('mediaOnly')}
              </Text>
            </Pressable>
          </View>

          <Text style={s.sectionLabel}>{t('period')}</Text>
          <View style={[s.grid, { marginBottom: 2 }]}>
            {TIME_RANGES.map(r => (
              <Pressable
                key={r.days}
                style={[s.chip, days === r.days && s.chipActive]}
                onPress={() => setDays(r.days)}
              >
                <Text style={[s.chipText, days === r.days && s.chipTextActive]}>{r.label}</Text>
              </Pressable>
            ))}
          </View>

          <Text style={[s.sectionLabel, { marginTop: 14, marginBottom: 9 }]}>{t('keyword')}</Text>
          <View style={s.grid}>
            {chips.map((c) => {
              const isAll = c.id === null
              const isActive = isAll ? selectedKeyword === null : selectedKeyword === c.keyword
              return (
                <Pressable
                  key={String(c.id)}
                  style={[s.chip, isActive && s.chipActive]}
                  onPress={() => setSelectedKeyword(isAll ? null : c.keyword)}
                >
                  <Text style={[s.chipText, isActive && s.chipTextActive]}>
                    {c.keyword}
                  </Text>
                </Pressable>
              )
            })}
          </View>
        </View>
      )}

      {/* Sources strip — always visible, horizontal swipeable */}
      {orderedPlatforms.length > 0 && (
        <View style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: theme.card, borderBottomWidth: 1, borderColor: theme.divider, height: 70, flexShrink: 0 }}>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            style={{ flex: 1 }}
            contentContainerStyle={{ flexDirection: 'row', alignItems: 'center', paddingLeft: 14, paddingRight: 8, paddingVertical: 6, gap: 4 }}
          >
          {/* All button */}
          <Pressable
            onPress={() => setSelectedPlatform(null)}
            android_ripple={Platform.OS === 'android' ? { color: theme.primary + '33', borderless: false } : undefined}
            style={({ pressed }) => ({
              width: 58, height: 58,
              alignItems: 'center', justifyContent: 'center',
              borderRadius: 10,
              backgroundColor: selectedPlatform === null ? theme.primary : theme.divider,
              opacity: pressed && Platform.OS === 'ios' ? 0.75 : 1,
            })}
          >
            <Text style={{ fontSize: 18, lineHeight: 22 }}>🌐</Text>
            <Text style={{ fontSize: 9, fontWeight: selectedPlatform === null ? '700' : '500', color: selectedPlatform === null ? '#fff' : theme.textMuted, textAlign: 'center', marginTop: 2 }} numberOfLines={1}>{t('all')}</Text>
          </Pressable>

          {orderedPlatforms.map((id) => (
            <SourceChip
              key={id}
              id={id}
              selectedPlatform={selectedPlatform}
              theme={theme}
              style={style}
              onPress={handleSourcePress}
            />
          ))}
          </ScrollView>

          {/* Reorder button */}
          <Pressable
            onPress={() => setReorderOpen(true)}
            accessibilityRole="button"
            accessibilityLabel="Reorder sources"
            hitSlop={6}
            style={({ pressed }) => ({
              width: 48, height: 58,
              alignItems: 'center', justifyContent: 'center',
              borderRadius: 10,
              backgroundColor: theme.divider,
              marginRight: 10,
              opacity: pressed ? 0.6 : 1,
            })}
          >
            <Text style={{ fontSize: 20, color: theme.textMuted }}>≡</Text>
          </Pressable>
        </View>
      )}

      {isLoading ? (
        <ActivityIndicator style={{ marginTop: 48 }} color={theme.primary} />
      ) : displayFeed.length === 0 ? (
        <View style={s.empty}>
          <Text style={[s.emptyEmoji, { fontSize: 40, letterSpacing: 0 }]}>≽՞•ﻌ•՞≼</Text>
          <Text style={[s.emptyTitle, { color: theme.primary }]}>{t('feedEmpty')}</Text>
          <Text style={s.emptyBody}>{t('feedEmptyBody')}</Text>
        </View>
      ) : (
        <FlatList
          data={displayFeed}
          keyExtractor={(fi) => `${fi.id}::${fi.watch_term_keyword || ''}`}
          contentContainerStyle={s.list}
          renderItem={({ item }) => (
            <FeedCard
              feedItem={item}
              isSaved={savedIds.has(item.id)}
              onToggleSave={() => {
                toggleSaved(item).then(() => qc.invalidateQueries({ queryKey: ['saved'] }))
              }}
              onDelete={() => handleDeleteItem(item)}
            />
          )}
          onRefresh={handleRefresh}
          refreshing={isRefreshing}
          ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
        />
      )}
      {/* Add URL floating button */}
      <Pressable
        onPress={() => setAddUrlOpen(true)}
        style={({ pressed }) => ({
          position: 'absolute',
          bottom: 24,
          right: 20,
          width: 52,
          height: 52,
          borderRadius: 26,
          backgroundColor: theme.primary,
          alignItems: 'center',
          justifyContent: 'center',
          elevation: 6,
          shadowColor: '#000',
          shadowOpacity: 0.25,
          shadowRadius: 8,
          shadowOffset: { width: 0, height: 3 },
          zIndex: 10,
          opacity: pressed ? 0.8 : 1,
        })}
      >
        <Text style={{ fontSize: 26, color: '#fff', lineHeight: 30, marginTop: -2 }}>+</Text>
      </Pressable>

      <AddUrlModal visible={addUrlOpen} onClose={() => setAddUrlOpen(false)} />
      <SourceReorderModal
        visible={reorderOpen}
        platforms={orderedPlatforms}
        onSave={handleSaveOrder}
        onClose={() => setReorderOpen(false)}
      />
    </View>
  )

  if (wallpaper) {
    return (
      <ImageBackground
        source={{ uri: wallpaper }}
        style={s.container}
        imageStyle={{ opacity: 0.18, resizeMode: 'cover' }}
      >
        {inner}
      </ImageBackground>
    )
  }

  return inner
}
