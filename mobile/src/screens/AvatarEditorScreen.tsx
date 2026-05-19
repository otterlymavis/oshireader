import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  ActivityIndicator,
  Animated,
  Dimensions,
  FlatList,
  Image,
  PanResponder,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native'
import { useNavigation, useRoute } from '@react-navigation/native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getOshiCompositions, setOshiComposition, setWallpaper,
  type AvatarLayer,
} from '../localDb'
import { searchIrasutoya, getPopularIrasutoya, type IrasutoyaImage } from '../scraper/irasutoya'
import { useTheme } from '../ThemeContext'
import { useLang } from '../LangContext'
import type { Theme } from '../theme'

// Display label per locale → Japanese query term sent to irasutoya API (null = popular feed)
type CatDef = { label: Record<string, string>; query: string | null }
const CATEGORY_DEFS: CatDef[] = [
  { label: { ja: 'おすすめ ✨', en: 'Popular ✨', 'zh-TW': '熱門 ✨', 'zh-CN': '热门 ✨' }, query: null },
  { label: { ja: '人物',   en: 'People',      'zh-TW': '人物',  'zh-CN': '人物'  }, query: '人物' },
  { label: { ja: '表情',   en: 'Expressions', 'zh-TW': '表情',  'zh-CN': '表情'  }, query: '表情' },
  { label: { ja: '動物',   en: 'Animals',     'zh-TW': '動物',  'zh-CN': '动物'  }, query: '動物' },
  { label: { ja: '女性',   en: 'Women',       'zh-TW': '女性',  'zh-CN': '女性'  }, query: '女性' },
  { label: { ja: '男性',   en: 'Men',         'zh-TW': '男性',  'zh-CN': '男性'  }, query: '男性' },
  { label: { ja: '子供',   en: 'Children',    'zh-TW': '兒童',  'zh-CN': '儿童'  }, query: '子供' },
  { label: { ja: '食べ物', en: 'Food',        'zh-TW': '食物',  'zh-CN': '食物'  }, query: '食べ物' },
]

const SEARCH_PLACEHOLDER: Record<string, string> = {
  ja: 'いらすとやで検索…',
  en: 'Search irasutoya…',
  'zh-TW': '搜尋 irasutoya…',
  'zh-CN': '搜索 irasutoya…',
}

const { width: SW } = Dimensions.get('window')
const CANVAS_H = 300
const BASE_SIZE = 90

// ── Styles ───────────────────────────────────────────────────────────────────

function makeStyles(t: Theme) {
  return StyleSheet.create({
    root: { flex: 1, backgroundColor: t.bg },
    canvas: {
      width: SW, height: CANVAS_H,
      backgroundColor: t.mode === 'dark' ? '#1a1a1a' : '#f0f0f0',
      overflow: 'hidden', position: 'relative',
    },
    canvasHint: {
      position: 'absolute', bottom: 8, left: 0, right: 0,
      alignItems: 'center',
    },
    canvasHintText: { fontSize: 11, color: t.textMuted, opacity: 0.6 },
    controls: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      flexWrap: 'wrap',
      paddingHorizontal: 12, paddingVertical: 8,
      backgroundColor: t.card, borderBottomWidth: 1, borderColor: t.divider,
    },
    ctrlLabel: { fontSize: 11, color: t.textMuted, marginRight: 4 },
    ctrlBtn: {
      paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999,
      backgroundColor: t.divider,
    },
    ctrlBtnActive: { backgroundColor: t.primaryBg },
    ctrlBtnDanger: { backgroundColor: '#FEE2E2' },
    ctrlBtnText: { fontSize: 13, fontWeight: '700', color: t.text },
    ctrlBtnTextActive: { color: t.primary },
    ctrlBtnDangerText: { color: '#B91C1C' },
    ctrlBtnDisabled: { opacity: 0.35 },
    saveRow: {
      flexDirection: 'row', gap: 8, paddingHorizontal: 12, paddingVertical: 8,
      backgroundColor: t.card, borderBottomWidth: 1, borderColor: t.divider,
    },
    saveBtn: {
      flex: 1, paddingVertical: 9, borderRadius: 10, alignItems: 'center',
      backgroundColor: t.primary,
    },
    saveBtnAlt: { backgroundColor: t.primaryBg },
    saveBtnText: { fontSize: 13, fontWeight: '700', color: '#fff' },
    saveBtnAltText: { color: t.primary },
    searchRow: {
      flexDirection: 'row', gap: 8, paddingHorizontal: 12, paddingTop: 10, paddingBottom: 6,
    },
    input: {
      flex: 1, height: 38, paddingHorizontal: 12, borderRadius: 10,
      backgroundColor: t.card, color: t.text, fontSize: 14,
      borderWidth: 1, borderColor: t.border,
    },
    searchBtn: {
      paddingHorizontal: 14, paddingVertical: 8, borderRadius: 10,
      backgroundColor: t.primary,
    },
    searchBtnText: { fontSize: 14, fontWeight: '700', color: '#fff' },
    grid: { paddingHorizontal: 10, paddingBottom: 20 },
    thumb: {
      width: (SW - 44) / 3, height: (SW - 44) / 3,
      margin: 4, borderRadius: 8, overflow: 'hidden',
      backgroundColor: t.divider,
    },
    thumbImg: { width: '100%', height: '100%' },
    thumbOverlay: {
      ...StyleSheet.absoluteFillObject,
      backgroundColor: 'rgba(124,58,237,0.15)',
      justifyContent: 'center', alignItems: 'center',
    },
    thumbAdd: { fontSize: 22 },
    categoryRow: {
      flexDirection: 'row', flexWrap: 'wrap', gap: 6,
      paddingHorizontal: 12, paddingBottom: 8,
    },
    catChip: {
      paddingHorizontal: 11, paddingVertical: 5, borderRadius: 999,
      backgroundColor: t.divider, borderWidth: 1, borderColor: t.border,
    },
    catChipActive: { backgroundColor: t.primary, borderColor: t.primary },
    catChipText: { fontSize: 12, color: t.textSub, fontWeight: '500' },
    catChipTextActive: { color: '#fff', fontWeight: '700' },
    resultMeta: { paddingHorizontal: 14, paddingBottom: 6 },
    resultMetaText: { fontSize: 12, color: t.textMuted },
    emptyBox: { padding: 24, alignItems: 'center', gap: 8 },
    emptyKaomoji: { fontSize: 32 },
    emptyText: { fontSize: 13, color: t.textMuted, textAlign: 'center' },
    layerImg: {
      position: 'absolute',
      borderWidth: 2, borderColor: 'transparent',
      borderRadius: 4,
      overflow: 'hidden',
    },
    layerImgSelected: { borderColor: '#7C3AED' },
    layerImgCropping: { borderColor: '#22C55E' },
  })
}

// ── Layer animated state ──────────────────────────────────────────────────────

type LayerAnim = {
  pan: Animated.ValueXY
  panResponder: ReturnType<typeof PanResponder.create>
}

const ROTATION_STEP = 15
const CROP_ZOOM_STEP = 0.15
const MIN_CROP_SCALE = 1
const MAX_CROP_SCALE = 3

function clampCrop(layer: AvatarLayer, cropX: number, cropY: number, cropScale = layer.cropScale ?? 1) {
  const size = BASE_SIZE * layer.scale
  const maxOffset = Math.max(0, (size * cropScale - size) / 2)
  return {
    cropX: Math.max(-maxOffset, Math.min(maxOffset, cropX)),
    cropY: Math.max(-maxOffset, Math.min(maxOffset, cropY)),
    cropScale,
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function AvatarEditorScreen() {
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const keyword: string = route.params?.keyword ?? ''
  const { theme } = useTheme()
  const { lang } = useLang()
  const s = useMemo(() => makeStyles(theme), [theme])
  const qc = useQueryClient()

  const catLabel = (def: CatDef) => def.label[lang] ?? def.label.en ?? def.label.ja

  const [layers, setLayers] = useState<AvatarLayer[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [activeCatKey, setActiveCatKey] = useState<string | null>(null) // ja key or null = popular
  const [results, setResults] = useState<IrasutoyaImage[]>([])
  const [searching, setSearching] = useState(false)
  const [saving, setSaving] = useState(false)
  const [cropMode, setCropMode] = useState(false)

  // Stable map: layerId → { pan, panResponder }
  const animsRef = useRef<Map<string, LayerAnim>>(new Map())
  const layersRef = useRef<AvatarLayer[]>([])
  const cropModeRef = useRef(false)
  const cropStartRef = useRef({ x: 0, y: 0 })

  const { data: compositions = {} } = useQuery({
    queryKey: ['oshi-compositions'],
    queryFn: getOshiCompositions,
  })

  useEffect(() => {
    layersRef.current = layers
  }, [layers])

  useEffect(() => {
    cropModeRef.current = cropMode
  }, [cropMode])

  useEffect(() => {
    if (!selectedId) setCropMode(false)
  }, [selectedId])

  // Initialise animated values for a layer (idempotent)
  const initAnim = useCallback((layer: AvatarLayer) => {
    if (animsRef.current.has(layer.id)) return
    const pan = new Animated.ValueXY({ x: layer.x, y: layer.y })
    const id = layer.id

    const pr = PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: () => {
        setSelectedId(id)
        if (cropModeRef.current) {
          const layerNow = layersRef.current.find(l => l.id === id)
          cropStartRef.current = {
            x: layerNow?.cropX ?? 0,
            y: layerNow?.cropY ?? 0,
          }
          return
        }
        // Offset so dx/dy are relative to current position
        pan.setOffset({ x: (pan.x as any)._value, y: (pan.y as any)._value })
        pan.setValue({ x: 0, y: 0 })
      },
      onPanResponderMove: (_event, gesture) => {
        if (cropModeRef.current) {
          const layerNow = layersRef.current.find(l => l.id === id)
          if (!layerNow) return
          const crop = clampCrop(
            layerNow,
            cropStartRef.current.x + gesture.dx,
            cropStartRef.current.y + gesture.dy,
            Math.max(1.2, layerNow.cropScale ?? 1),
          )
          setLayers(prev => prev.map(l => l.id === id ? { ...l, ...crop } : l))
          return
        }
        pan.setValue({ x: gesture.dx, y: gesture.dy })
      },
      onPanResponderRelease: () => {
        if (cropModeRef.current) return
        pan.flattenOffset()
        const layerNow = layersRef.current.find(l => l.id === id)
        const size = BASE_SIZE * (layerNow?.scale ?? 1)
        const x = Math.max(0, Math.min(SW - size, (pan.x as any)._value))
        const y = Math.max(0, Math.min(CANVAS_H - size, (pan.y as any)._value))
        // Clamp the animated value too
        pan.setValue({ x, y })
        setLayers(prev => prev.map(l => l.id === id ? { ...l, x, y } : l))
      },
    })
    animsRef.current.set(id, { pan, panResponder: pr })
  }, [])

  // Load existing composition on mount
  useEffect(() => {
    const comp = compositions[keyword] ?? []
    if (comp.length > 0 && layers.length === 0) {
      const normalized = comp.map(layer => ({
        ...layer,
        cropX: layer.cropX ?? 0,
        cropY: layer.cropY ?? 0,
        cropScale: layer.cropScale ?? 1,
        rotation: layer.rotation ?? 0,
      }))
      for (const layer of normalized) initAnim(layer)
      setLayers(normalized)
    }
  }, [compositions, keyword])

  useLayoutEffect(() => {
    navigation.setOptions({ headerShown: false })
  }, [])

  // ── Initial load: popular images ────────────────────────────────────────

  useEffect(() => {
    setSearching(true)
    getPopularIrasutoya()
      .then(imgs => setResults(imgs))
      .catch(() => setResults([]))
      .finally(() => setSearching(false))
  }, [])

  const handleSearch = async (q: string) => {
    const trimmed = q.trim()
    if (!trimmed) return
    setActiveCatKey('__search__')
    setSearching(true)
    try {
      setResults(await searchIrasutoya(trimmed))
    } catch {
      setResults([])
    } finally {
      setSearching(false)
    }
  }

  const handleCategory = async (def: CatDef) => {
    setActiveCatKey(def.query)
    setSearching(true)
    try {
      setResults(
        def.query === null
          ? await getPopularIrasutoya()
          : await searchIrasutoya(def.query),  // always Japanese query term
      )
    } catch {
      setResults([])
    } finally {
      setSearching(false)
    }
  }

  // ── Layer operations ──────────────────────────────────────────────────────

  const addLayer = useCallback((imageUrl: string) => {
    const id = `layer-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    const maxZ = layers.reduce((m, l) => Math.max(m, l.zIndex), 0)
    const x = Math.max(0, SW / 2 - BASE_SIZE / 2)
    const y = Math.max(0, CANVAS_H / 2 - BASE_SIZE / 2)
    const newLayer: AvatarLayer = {
      id, imageUrl, x, y,
      scale: 1, cropX: 0, cropY: 0, cropScale: 1,
      rotation: 0, zIndex: maxZ + 1,
    }
    initAnim(newLayer)
    setLayers(prev => [...prev, newLayer])
    setSelectedId(id)
    setCropMode(false)
  }, [layers, initAnim])

  const deleteSelected = useCallback(() => {
    if (!selectedId) return
    animsRef.current.delete(selectedId)
    setLayers(prev => prev.filter(l => l.id !== selectedId))
    setSelectedId(null)
    setCropMode(false)
  }, [selectedId])

  const scaleSelected = useCallback((delta: number) => {
    setLayers(prev => prev.map(l => {
      if (l.id !== selectedId) return l
      const scale = Math.max(0.3, Math.min(3, l.scale + delta))
      const resized = { ...l, scale }
      const size = BASE_SIZE * scale
      const x = Math.max(0, Math.min(SW - size, resized.x))
      const y = Math.max(0, Math.min(CANVAS_H - size, resized.y))
      const crop = clampCrop(resized, resized.cropX ?? 0, resized.cropY ?? 0, resized.cropScale ?? 1)
      animsRef.current.get(l.id)?.pan.setValue({ x, y })
      return { ...resized, x, y, ...crop }
    }))
  }, [selectedId])

  const rotateSelected = useCallback((delta: number) => {
    setLayers(prev => prev.map(l =>
      l.id === selectedId
        ? { ...l, rotation: ((l.rotation ?? 0) + delta + 360) % 360 }
        : l,
    ))
  }, [selectedId])

  const toggleCropMode = useCallback(() => {
    if (!selectedId) return
    const next = !cropMode
    setCropMode(next)
    if (next) {
      setLayers(prev => prev.map(l => {
        if (l.id !== selectedId) return l
        const cropScale = Math.max(1.2, l.cropScale ?? 1)
        return { ...l, ...clampCrop(l, l.cropX ?? 0, l.cropY ?? 0, cropScale) }
      }))
    }
  }, [cropMode, selectedId])

  const cropZoomSelected = useCallback((delta: number) => {
    setLayers(prev => prev.map(l => {
      if (l.id !== selectedId) return l
      const cropScale = Math.max(MIN_CROP_SCALE, Math.min(MAX_CROP_SCALE, (l.cropScale ?? 1) + delta))
      return { ...l, ...clampCrop(l, l.cropX ?? 0, l.cropY ?? 0, cropScale) }
    }))
  }, [selectedId])

  const resetCropSelected = useCallback(() => {
    setLayers(prev => prev.map(l =>
      l.id === selectedId ? { ...l, cropX: 0, cropY: 0, cropScale: 1 } : l,
    ))
  }, [selectedId])

  const bringForward = useCallback(() => {
    setLayers(prev => {
      const maxZ = prev.reduce((m, l) => Math.max(m, l.zIndex), 0)
      return prev.map(l => l.id === selectedId ? { ...l, zIndex: maxZ + 1 } : l)
    })
  }, [selectedId])

  const sendBack = useCallback(() => {
    setLayers(prev => {
      const minZ = prev.reduce((m, l) => Math.min(m, l.zIndex), 0)
      return prev.map(l => l.id === selectedId ? { ...l, zIndex: minZ - 1 } : l)
    })
  }, [selectedId])

  // ── Save ──────────────────────────────────────────────────────────────────

  const handleSave = async () => {
    if (saving) return
    setSaving(true)
    try {
      // Sync final animated positions into layers state before saving
      const finalLayers = layers.map(l => {
        const anim = animsRef.current.get(l.id)
        if (anim) {
          return {
            ...l,
            x: (anim.pan.x as any)._value,
            y: (anim.pan.y as any)._value,
          }
        }
        return l
      })
      await setOshiComposition(keyword, finalLayers)
      qc.invalidateQueries({ queryKey: ['oshi-compositions'] })
      qc.invalidateQueries({ queryKey: ['oshi-avatars'] })
      navigation.goBack()
    } finally {
      setSaving(false)
    }
  }

  const handleSetWallpaper = async () => {
    const first = [...layers].sort((a, b) => b.zIndex - a.zIndex)[0]
    if (!first) return
    await setWallpaper(first.imageUrl)
    qc.invalidateQueries({ queryKey: ['wallpaper'] })
  }

  // ── Render helpers ────────────────────────────────────────────────────────

  const sortedLayers = useMemo(
    () => [...layers].sort((a, b) => a.zIndex - b.zIndex),
    [layers],
  )

  const selectedLayer = layers.find(l => l.id === selectedId) ?? null

  const renderThumb = ({ item }: { item: IrasutoyaImage }) => (
    <Pressable
      style={({ pressed }) => [s.thumb, { opacity: pressed ? 0.75 : 1 }]}
      onPress={() => addLayer(item.thumb)}
    >
      <Image source={{ uri: item.thumb }} style={s.thumbImg} resizeMode="cover" />
      <View style={s.thumbOverlay}>
        <Text style={s.thumbAdd}>＋</Text>
      </View>
    </Pressable>
  )

  // ── Layout ────────────────────────────────────────────────────────────────

  return (
    <View style={s.root}>
      {/* Header */}
      <View style={{
        flexDirection: 'row', alignItems: 'center', gap: 8,
        paddingHorizontal: 12, paddingTop: 48, paddingBottom: 10,
        backgroundColor: theme.card, borderBottomWidth: 1, borderColor: theme.divider,
      }}>
        <Pressable onPress={() => navigation.goBack()} hitSlop={10}
          style={{ paddingRight: 4 }}>
          <Text style={{ fontSize: 28, color: theme.primary, lineHeight: 32 }}>‹</Text>
        </Pressable>
        <Text style={{ flex: 1, fontSize: 16, fontWeight: '700', color: theme.text }} numberOfLines={1}>
          ✨ {keyword}
        </Text>
      </View>

      {/* Canvas */}
      <View style={s.canvas}>
        {sortedLayers.map(layer => {
          const anim = animsRef.current.get(layer.id)
          if (!anim) return null
          const size = BASE_SIZE * layer.scale
          const cropScale = layer.cropScale ?? 1
          const cropX = layer.cropX ?? 0
          const cropY = layer.cropY ?? 0
          const isSelected = layer.id === selectedId
          return (
            <Animated.View
              key={layer.id}
              style={[
                s.layerImg,
                isSelected && s.layerImgSelected,
                isSelected && cropMode && s.layerImgCropping,
                {
                  width: size, height: size,
                  transform: [
                    ...anim.pan.getTranslateTransform(),
                    { rotate: `${layer.rotation ?? 0}deg` },
                  ],
                },
              ]}
              {...anim.panResponder.panHandlers}
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
            </Animated.View>
          )
        })}
        {layers.length === 0 && (
          <View style={s.canvasHint}>
            <Text style={s.canvasHintText}>下の画像をタップしてキャンバスに追加 (˶ᵔ ᵕ ᵔ˶)</Text>
          </View>
        )}
      </View>

      {/* Layer controls */}
      <View style={s.controls}>
        <Pressable
          style={[s.ctrlBtn, cropMode && s.ctrlBtnActive, !selectedLayer && s.ctrlBtnDisabled]}
          disabled={!selectedLayer}
          onPress={toggleCropMode}
        >
          <Text style={[s.ctrlBtnText, cropMode && s.ctrlBtnTextActive]}>{cropMode ? 'Move' : 'Crop'}</Text>
        </Pressable>
        {cropMode && (
          <>
            <Pressable
              style={[s.ctrlBtn, !selectedLayer && s.ctrlBtnDisabled]}
              disabled={!selectedLayer}
              onPress={() => cropZoomSelected(CROP_ZOOM_STEP)}
            >
              <Text style={s.ctrlBtnText}>Zoom +</Text>
            </Pressable>
            <Pressable
              style={[s.ctrlBtn, !selectedLayer && s.ctrlBtnDisabled]}
              disabled={!selectedLayer}
              onPress={() => cropZoomSelected(-CROP_ZOOM_STEP)}
            >
              <Text style={s.ctrlBtnText}>Zoom -</Text>
            </Pressable>
            <Pressable
              style={[s.ctrlBtn, !selectedLayer && s.ctrlBtnDisabled]}
              disabled={!selectedLayer}
              onPress={resetCropSelected}
            >
              <Text style={s.ctrlBtnText}>Fit</Text>
            </Pressable>
          </>
        )}
        <Text style={s.ctrlLabel}>選択中：</Text>
        <Pressable
          style={[s.ctrlBtn, !selectedLayer && s.ctrlBtnDisabled]}
          disabled={!selectedLayer}
          onPress={() => scaleSelected(0.15)}
        >
          <Text style={s.ctrlBtnText}>＋</Text>
        </Pressable>
        <Pressable
          style={[s.ctrlBtn, !selectedLayer && s.ctrlBtnDisabled]}
          disabled={!selectedLayer}
          onPress={() => scaleSelected(-0.15)}
        >
          <Text style={s.ctrlBtnText}>－</Text>
        </Pressable>
        <Pressable
          style={[s.ctrlBtn, !selectedLayer && s.ctrlBtnDisabled]}
          disabled={!selectedLayer}
          onPress={() => rotateSelected(-ROTATION_STEP)}
        >
          <Text style={s.ctrlBtnText}>⟲</Text>
        </Pressable>
        <Pressable
          style={[s.ctrlBtn, !selectedLayer && s.ctrlBtnDisabled]}
          disabled={!selectedLayer}
          onPress={() => rotateSelected(ROTATION_STEP)}
        >
          <Text style={s.ctrlBtnText}>⟳</Text>
        </Pressable>
        <Pressable
          style={[s.ctrlBtn, !selectedLayer && s.ctrlBtnDisabled]}
          disabled={!selectedLayer}
          onPress={bringForward}
        >
          <Text style={s.ctrlBtnText}>↑前</Text>
        </Pressable>
        <Pressable
          style={[s.ctrlBtn, !selectedLayer && s.ctrlBtnDisabled]}
          disabled={!selectedLayer}
          onPress={sendBack}
        >
          <Text style={s.ctrlBtnText}>↓後</Text>
        </Pressable>
        <Pressable
          style={[s.ctrlBtn, s.ctrlBtnDanger, !selectedLayer && s.ctrlBtnDisabled]}
          disabled={!selectedLayer}
          onPress={deleteSelected}
        >
          <Text style={[s.ctrlBtnText, s.ctrlBtnDangerText]}>削除</Text>
        </Pressable>
      </View>

      {/* Save row */}
      <View style={s.saveRow}>
        <Pressable style={s.saveBtn} onPress={handleSave} disabled={saving}>
          <Text style={s.saveBtnText}>{saving ? '…' : '💾 保存'}</Text>
        </Pressable>
        <Pressable style={[s.saveBtn, s.saveBtnAlt]} onPress={handleSetWallpaper} disabled={layers.length === 0}>
          <Text style={[s.saveBtnText, s.saveBtnAltText]}>🌸 壁紙にする</Text>
        </Pressable>
      </View>

      {/* Search + results */}
      <View style={s.searchRow}>
        <TextInput
          style={s.input}
          placeholder={SEARCH_PLACEHOLDER[lang] ?? SEARCH_PLACEHOLDER.en}
          placeholderTextColor={theme.textMuted}
          value={query}
          onChangeText={setQuery}
          onSubmitEditing={() => handleSearch(query)}
          returnKeyType="search"
        />
        <Pressable style={s.searchBtn} onPress={() => handleSearch(query)}>
          <Text style={s.searchBtnText}>🔍</Text>
        </Pressable>
      </View>

      <View style={s.categoryRow}>
        {CATEGORY_DEFS.map(def => {
          const active = activeCatKey === def.query && activeCatKey !== '__search__'
          return (
            <Pressable
              key={def.label.ja}
              style={[s.catChip, active && s.catChipActive]}
              onPress={() => handleCategory(def)}
            >
              <Text style={[s.catChipText, active && s.catChipTextActive]}>{catLabel(def)}</Text>
            </Pressable>
          )
        })}
      </View>

      {!searching && results.length > 0 && (
        <View style={s.resultMeta}>
          <Text style={s.resultMetaText}>{results.length} images</Text>
        </View>
      )}

      {searching ? (
        <ActivityIndicator color={theme.primary} style={{ marginTop: 24 }} />
      ) : results.length === 0 ? (
        <View style={s.emptyBox}>
          <Text style={s.emptyKaomoji}>(´• ω •`)</Text>
          <Text style={s.emptyText}>
            {query
              ? ({ ja: '検索結果がありません', en: 'No results found', 'zh-TW': '找不到結果', 'zh-CN': '未找到结果' }[lang] ?? 'No results found')
              : ({ ja: 'キーワードを入力して検索', en: 'Search for images above', 'zh-TW': '請輸入關鍵字搜尋', 'zh-CN': '请输入关键词搜索' }[lang] ?? 'Search for images above')
            }
          </Text>
        </View>
      ) : (
        <FlatList
          data={results}
          keyExtractor={i => i.url}
          numColumns={3}
          renderItem={renderThumb}
          contentContainerStyle={s.grid}
          showsVerticalScrollIndicator={false}
        />
      )}
    </View>
  )
}
