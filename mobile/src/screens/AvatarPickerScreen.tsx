import { useMemo, useState } from 'react'
import {
  ActivityIndicator, FlatList, Image, Pressable,
  StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useNavigation, useRoute } from '@react-navigation/native'
import { useQueryClient } from '@tanstack/react-query'
import { useTheme } from '../ThemeContext'
import { setOshiAvatar, setWallpaper } from '../localDb'
import { searchIrasutoya, type IrasutoyaImage } from '../scraper/irasutoya'
import type { Theme } from '../theme'

export interface AvatarPickerParams {
  keyword: string
}

function makeStyles(t: Theme) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: t.bg },
    header: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: t.card, paddingHorizontal: 8, paddingVertical: 10,
      borderBottomWidth: 1, borderColor: t.divider,
    },
    backBtn: { paddingHorizontal: 8, paddingVertical: 4 },
    backText: { fontSize: 28, color: t.primary, lineHeight: 32 },
    searchBar: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8, marginHorizontal: 8 },
    input: {
      flex: 1, borderWidth: 1, borderColor: t.border, borderRadius: 10,
      paddingHorizontal: 12, paddingVertical: 8, fontSize: 15, color: t.text,
      backgroundColor: t.inputBg,
    },
    searchBtn: { backgroundColor: t.primary, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8 },
    searchBtnText: { color: '#fff', fontWeight: '700', fontSize: 14 },
    hint: {
      backgroundColor: t.primaryBg, marginHorizontal: 12, marginVertical: 8,
      borderRadius: 10, paddingHorizontal: 14, paddingVertical: 8,
    },
    hintText: { fontSize: 12, color: t.primary, textAlign: 'center', lineHeight: 18 },
    grid: { padding: 6 },
    cell: {
      flex: 1, margin: 4, borderRadius: 12, overflow: 'hidden',
      backgroundColor: t.card,
      shadowColor: '#000', shadowOffset: { width: 0, height: 1 },
      shadowOpacity: 0.07, shadowRadius: 3, elevation: 2,
    },
    img: { width: '100%', aspectRatio: 1 },
    selectedOverlay: {
      ...StyleSheet.absoluteFillObject,
      backgroundColor: 'rgba(124,58,237,0.3)',
      borderWidth: 3, borderColor: '#7C3AED', borderRadius: 12,
      justifyContent: 'center', alignItems: 'center',
    },
    checkmark: { fontSize: 36 },
    cellTitle: { fontSize: 10, color: t.textMuted, padding: 4, textAlign: 'center' },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, padding: 32 },
    centerKaomoji: { fontSize: 36 },
    centerText: { fontSize: 14, color: t.textMuted, textAlign: 'center' },
    centerSub: { fontSize: 12, color: t.textMuted, textAlign: 'center' },
    actionRow: {
      position: 'absolute', bottom: 24, flexDirection: 'row',
      alignSelf: 'center', gap: 12,
    },
    saveBtn: {
      backgroundColor: t.primary, borderRadius: 999,
      paddingHorizontal: 22, paddingVertical: 14,
      shadowColor: t.primary, shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.4, shadowRadius: 8, elevation: 6,
    },
    wallpaperBtn: {
      backgroundColor: '#EC4899', borderRadius: 999,
      paddingHorizontal: 22, paddingVertical: 14,
      shadowColor: '#EC4899', shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.4, shadowRadius: 8, elevation: 6,
    },
    saveBtnText: { color: '#fff', fontWeight: '800', fontSize: 15, letterSpacing: 0.3 },
  })
}

export default function AvatarPickerScreen() {
  const navigation = useNavigation()
  const route = useRoute<any>()
  const { keyword } = route.params as AvatarPickerParams
  const { theme } = useTheme()
  const s = useMemo(() => makeStyles(theme), [theme])
  const qc = useQueryClient()

  const [query, setQuery] = useState(keyword)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<IrasutoyaImage[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [searched, setSearched] = useState(false)

  const doSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    setSearched(false)
    setResults([])
    setSelected(null)
    try {
      setResults(await searchIrasutoya(query.trim()))
    } catch (e) {
      console.log('[irasutoya] search failed:', e)
    } finally {
      setLoading(false)
      setSearched(true)
    }
  }

  const saveAvatar = async () => {
    if (!selected) return
    await setOshiAvatar(keyword, selected)
    qc.invalidateQueries({ queryKey: ['oshi-avatars'] })
    navigation.goBack()
  }

  const saveWallpaper = async () => {
    if (!selected) return
    await setWallpaper(selected)
    qc.invalidateQueries({ queryKey: ['wallpaper'] })
    navigation.goBack()
  }

  return (
    <View style={s.container}>
      <View style={s.header}>
        <Pressable onPress={() => navigation.goBack()} style={s.backBtn} hitSlop={10}>
          <Text style={s.backText}>‹</Text>
        </Pressable>
        <View style={s.searchBar}>
          <TextInput
            style={s.input}
            value={query}
            onChangeText={setQuery}
            returnKeyType="search"
            onSubmitEditing={doSearch}
            placeholder="キーワードで検索…"
            placeholderTextColor={theme.textMuted}
            autoFocus
          />
          <Pressable style={s.searchBtn} onPress={doSearch} disabled={loading}>
            <Text style={s.searchBtnText}>{loading ? '…' : '🔍'}</Text>
          </Pressable>
        </View>
      </View>

      <View style={s.hint}>
        <Text style={s.hintText}>
          🖼️ いらすとやのイラストからアバターを選んでね (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧{'\n'}
          画像をタップして選択 → 下の保存ボタンで確定！
        </Text>
      </View>

      {loading ? (
        <ActivityIndicator style={{ marginTop: 48 }} color={theme.primary} size="large" />
      ) : results.length > 0 ? (
        <FlatList
          data={results}
          keyExtractor={item => item.url}
          numColumns={3}
          contentContainerStyle={[s.grid, { paddingBottom: 100 }]}
          renderItem={({ item }) => {
            const isSelected = selected === item.thumb
            return (
              <Pressable
                style={s.cell}
                onPress={() => setSelected(isSelected ? null : item.thumb)}
              >
                <Image source={{ uri: item.thumb }} style={s.img} resizeMode="cover" />
                {isSelected && (
                  <View style={s.selectedOverlay}>
                    <Text style={s.checkmark}>✓</Text>
                  </View>
                )}
                <Text style={s.cellTitle} numberOfLines={2}>{item.title}</Text>
              </Pressable>
            )
          }}
        />
      ) : searched ? (
        <View style={s.center}>
          <Text style={s.centerKaomoji}>( •᷄⌓•᷅ )</Text>
          <Text style={s.centerText}>見つかりませんでした</Text>
          <Text style={s.centerSub}>別のキーワードで試してみてね</Text>
        </View>
      ) : (
        <View style={s.center}>
          <Text style={s.centerKaomoji}>(*ﾟ▽ﾟ)ﾉ</Text>
          <Text style={s.centerText}>キーワードを入れて検索してみよう！</Text>
          <Text style={s.centerSub}>推しの特徴や職業で検索するのがオススメ ♪</Text>
        </View>
      )}

      {selected && (
        <View style={s.actionRow}>
          <Pressable style={s.saveBtn} onPress={saveAvatar}>
            <Text style={s.saveBtnText}>✨ アバターに設定</Text>
          </Pressable>
          <Pressable style={s.wallpaperBtn} onPress={saveWallpaper}>
            <Text style={s.saveBtnText}>🌸 壁紙にする</Text>
          </Pressable>
        </View>
      )}
    </View>
  )
}
