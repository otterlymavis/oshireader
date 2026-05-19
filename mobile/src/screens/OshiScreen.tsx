import { useMemo, useRef, useState } from 'react'
import {
  Image, NativeScrollEvent, NativeSyntheticEvent,
  Pressable, ScrollView, StyleSheet, Text, View,
  useWindowDimensions,
} from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useQuery } from '@tanstack/react-query'
import { getTerms, getOshiCompositions, getItems } from '../localDb'
import type { AvatarLayer } from '../localDb'
import { useTheme } from '../ThemeContext'
import type { Theme } from '../theme'

const CANVAS_SRC = 300 // matches AvatarEditorScreen canvas width/height

function makeStyles(t: Theme, sw: number) {
  const avatarH = sw  // square canvas scaled to full width
  return StyleSheet.create({
    root: { flex: 1, backgroundColor: t.bg },
    header: {
      paddingHorizontal: 18, paddingTop: 14, paddingBottom: 10,
      flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between',
      borderBottomWidth: 1, borderColor: t.divider,
    },
    headerTitle: { fontSize: 20, fontWeight: '800', color: t.text },
    headerSub: { fontSize: 12, color: t.textMuted, marginBottom: 1 },
    // Pager
    pager: { flex: 1 },
    page: { width: sw, flex: 1 },
    // Avatar canvas
    canvas: {
      width: sw, height: avatarH,
      backgroundColor: t.mode === 'dark' ? '#1a1a1a' : '#f0f0f0',
    },
    placeholder: {
      flex: 1, justifyContent: 'center', alignItems: 'center', gap: 10,
    },
    placeholderEmoji: { fontSize: 56 },
    placeholderHint: { fontSize: 13, color: t.primary, fontWeight: '600' },
    // Info panel
    info: {
      paddingHorizontal: 18, paddingVertical: 14,
      flexDirection: 'row', alignItems: 'center', gap: 10,
    },
    infoLeft: { flex: 1, gap: 6 },
    keyword: { fontSize: 18, fontWeight: '800', color: t.text },
    metaRow: { flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
    countBadge: {
      paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999,
      backgroundColor: t.primaryBg,
    },
    countText: { fontSize: 12, fontWeight: '700', color: t.primary },
    modeBadge: {
      paddingHorizontal: 7, paddingVertical: 3, borderRadius: 999,
      backgroundColor: t.divider,
    },
    modeText: { fontSize: 12, color: t.textMuted, fontWeight: '500' },
    activeIndicator: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#22C55E' },
    inactiveIndicator: { width: 8, height: 8, borderRadius: 4, backgroundColor: t.border },
    editBtn: {
      paddingHorizontal: 16, paddingVertical: 9, borderRadius: 12,
      backgroundColor: t.primaryBg,
    },
    editBtnText: { fontSize: 13, fontWeight: '700', color: t.primary },
    // Page dots
    dots: {
      flexDirection: 'row', justifyContent: 'center', alignItems: 'center',
      gap: 7, paddingVertical: 12,
    },
    dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: t.border },
    dotActive: { width: 20, backgroundColor: t.primary },
    // Empty state
    empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12, padding: 32 },
    emptyKaomoji: { fontSize: 40, lineHeight: 48 },
    emptyTitle: { fontSize: 18, fontWeight: '700', color: t.primary, textAlign: 'center' },
    emptyBody: { fontSize: 13, color: t.textMuted, textAlign: 'center', lineHeight: 20 },
  })
}

export default function OshiScreen() {
  const navigation = useNavigation<any>()
  const { theme } = useTheme()
  const { width } = useWindowDimensions()
  const s = useMemo(() => makeStyles(theme, width), [theme, width])

  const [activePage, setActivePage] = useState(0)
  const scrollRef = useRef<ScrollView>(null)

  const { data: rawTerms = [] } = useQuery({ queryKey: ['watch-terms'], queryFn: getTerms })
  // Show oldest-added oshi first
  const terms = useMemo(() => [...rawTerms].sort((a, b) => a.created_at.localeCompare(b.created_at)), [rawTerms])
  const { data: compositions = {} } = useQuery({ queryKey: ['oshi-compositions'], queryFn: getOshiCompositions })
  const { data: allItems = [] } = useQuery({ queryKey: ['all-items'], queryFn: getItems })

  const countByKeyword = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const item of allItems) {
      const k = item.watch_term_keyword
      counts[k] = (counts[k] ?? 0) + 1
    }
    return counts
  }, [allItems])

  const onMomentumScrollEnd = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const page = Math.round(e.nativeEvent.contentOffset.x / width)
    setActivePage(page)
  }

  if (terms.length === 0) {
    return (
      <View style={s.root}>
        <View style={s.empty}>
          <Text style={s.emptyKaomoji}>(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧</Text>
          <Text style={s.emptyTitle}>推しを追加しよう！</Text>
          <Text style={s.emptyBody}>
            「設定」タブからキーワードを登録すると{'\n'}ここに推しのプロフィールが表示されます
          </Text>
        </View>
      </View>
    )
  }

  const SCALE = width / CANVAS_SRC

  return (
    <View style={s.root}>
      {/* Static header */}
      <View style={s.header}>
        <Text style={s.headerTitle}>推しリスト ✨</Text>
        <Text style={s.headerSub}>{terms.length}人の推しを追跡中</Text>
      </View>

      {/* Horizontal pager */}
      <ScrollView
        ref={scrollRef}
        horizontal
        pagingEnabled
        showsHorizontalScrollIndicator={false}
        onMomentumScrollEnd={onMomentumScrollEnd}
        style={s.pager}
        decelerationRate="fast"
      >
        {terms.map(term => {
          const comp: AvatarLayer[] = compositions[term.keyword] ?? []
          const count = countByKeyword[term.keyword] ?? 0

          return (
            <View key={term.id} style={s.page}>
              {/* Avatar canvas */}
              <Pressable
                style={s.canvas}
                onPress={() => navigation.navigate('AvatarEditor', { keyword: term.keyword })}
              >
                {comp.length === 0 ? (
                  <View style={s.placeholder}>
                    <Text style={s.placeholderEmoji}>🎨</Text>
                    <Text style={s.placeholderHint}>タップしてアバターを作成</Text>
                  </View>
                ) : (
                  [...comp].sort((a, b) => a.zIndex - b.zIndex).map(layer => {
                    const size = 90 * layer.scale * SCALE
                    const cropX = (layer.cropX ?? 0) * SCALE
                    const cropY = (layer.cropY ?? 0) * SCALE
                    const cropScale = layer.cropScale ?? 1
                    return (
                      <View
                        key={layer.id}
                        style={{
                          position: 'absolute',
                          left: layer.x * SCALE,
                          top: layer.y * SCALE,
                          width: size, height: size,
                          overflow: 'hidden',
                          transform: [{ rotate: `${layer.rotation ?? 0}deg` }],
                        }}
                      >
                        <Image
                          source={{ uri: layer.imageUrl }}
                          style={{
                            width: size,
                            height: size,
                            transform: [
                              { translateX: cropX },
                              { translateY: cropY },
                              { scale: cropScale },
                            ],
                          }}
                          resizeMode="contain"
                        />
                      </View>
                    )
                  })
                )}
              </Pressable>

              {/* Info panel */}
              <View style={s.info}>
                <View style={s.infoLeft}>
                  <Text style={s.keyword} numberOfLines={1}>{term.keyword}</Text>
                  <View style={s.metaRow}>
                    <View style={s.countBadge}>
                      <Text style={s.countText}>📰 {count}</Text>
                    </View>
                    <View style={s.modeBadge}>
                      <Text style={s.modeText}>
                        {term.collection_mode === 'media_only' ? '📹 メディア' : '📄 全情報'}
                      </Text>
                    </View>
                    <View style={term.is_active ? s.activeIndicator : s.inactiveIndicator} />
                  </View>
                </View>
                <Pressable
                  style={s.editBtn}
                  onPress={() => navigation.navigate('AvatarEditor', { keyword: term.keyword })}
                >
                  <Text style={s.editBtnText}>✏️ Edit</Text>
                </Pressable>
              </View>
            </View>
          )
        })}
      </ScrollView>

      {/* Page dots — only shown when there's more than one oshi */}
      {terms.length > 1 && (
        <View style={s.dots}>
          {terms.map((_, i) => (
            <Pressable
              key={i}
              style={[s.dot, i === activePage && s.dotActive]}
              onPress={() => {
                scrollRef.current?.scrollTo({ x: i * width, animated: true })
                setActivePage(i)
              }}
            />
          ))}
        </View>
      )}
    </View>
  )
}
