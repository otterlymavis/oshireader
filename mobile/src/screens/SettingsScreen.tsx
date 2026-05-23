import { useMemo, useState, useEffect } from 'react'
import {
  ActivityIndicator,
  Alert,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import AsyncStorage from '@react-native-async-storage/async-storage'
import {
  getTerms, saveTerm, deleteTerm, updateTerm, mergeItems,
  getSubscribedPlatforms, setSubscribedPlatforms, ALL_PLATFORM_IDS,
  getOshiAvatars, getWallpaper, setWallpaper,
  type WatchTerm,
} from '../localDb'
import { scrapeAll } from '../scraper'
import { requestNotificationPermission } from '../notifications'
import { useSettings } from '../hooks/useSettings'
import { useTheme } from '../ThemeContext'
import { useLang } from '../LangContext'
import { LANG_LABELS, type Lang } from '../i18n'
import type { Theme } from '../theme'
import { fetchTwitterStatus, twitterLogin, twitterDisconnect } from '../api'

function makeStyles(th: Theme) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: th.bg },
    scroll: { padding: 20, paddingBottom: 60 },
    section: {
      fontSize: 10, fontWeight: '700', color: th.textMuted,
      textTransform: 'uppercase', letterSpacing: 0.7, marginBottom: 8,
    },
    card: {
      backgroundColor: th.card, borderRadius: 16,
      borderWidth: th.mode === 'dark' ? 0 : 1, borderColor: th.border,
      shadowColor: th.mode === 'dark' ? '#000' : '#6B5B8A',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: th.mode === 'dark' ? 0.35 : 0.07, shadowRadius: 6, elevation: 2,
    },
    row: {
      flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
      paddingHorizontal: 16, paddingVertical: 10,
      borderBottomWidth: 1, borderColor: th.divider,
    },
    rowLast: { borderBottomWidth: 0 },
    rowLabel: { fontSize: 13, color: th.textSub, fontWeight: '500' },
    rowValue: { fontSize: 13, color: th.textMuted },
    // Inline option chips (for theme / language)
    optionRow: {
      flexDirection: 'row', flexWrap: 'wrap', gap: 8,
      paddingHorizontal: 16, paddingBottom: 12,
      borderBottomWidth: 1, borderColor: th.divider,
    },
    optionBtn: {
      paddingHorizontal: 14, paddingVertical: 7, borderRadius: 999,
      borderWidth: 1.5, borderColor: th.border,
    },
    optionBtnOn: { backgroundColor: th.primaryBg, borderColor: th.primary },
    optionBtnText: { fontSize: 12, color: th.textMuted, fontWeight: '500' },
    optionBtnTextOn: { color: th.primary, fontWeight: '700' },
    // Watch terms form
    formCard: {
      backgroundColor: th.card, borderRadius: 16, padding: 14, gap: 10,
      borderWidth: th.mode === 'dark' ? 0 : 1, borderColor: th.border,
      shadowColor: th.mode === 'dark' ? '#000' : '#6B5B8A',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: th.mode === 'dark' ? 0.35 : 0.07, shadowRadius: 6, elevation: 2,
    },
    input: {
      borderWidth: 1, borderColor: th.border, borderRadius: 10,
      paddingHorizontal: 12, paddingVertical: 9,
      fontSize: 14, color: th.text, backgroundColor: th.inputBg,
    },
    addBtn: { backgroundColor: th.primary, paddingHorizontal: 18, paddingVertical: 9, borderRadius: 8, alignSelf: 'flex-end' },
    addBtnOff: { opacity: 0.4 },
    addBtnText: { color: '#fff', fontWeight: '600', fontSize: 14 },
    // Avatar
    avatarBtn: { width: 38, height: 38, borderRadius: 19, overflow: 'hidden', backgroundColor: th.divider, justifyContent: 'center', alignItems: 'center' },
    avatarImg: { width: 38, height: 38 },
    avatarPlaceholder: { fontSize: 20 },
    // Term rows
    termRow: {
      flexDirection: 'row', alignItems: 'center',
      paddingHorizontal: 16, paddingVertical: 12, gap: 10,
      borderBottomWidth: 1, borderColor: th.divider,
    },
    termRowLast: { borderBottomWidth: 0 },
    termRowInactive: { opacity: 0.45 },
    termLeft: { flex: 1 },
    termKeyword: { fontSize: 13, fontWeight: '600', color: th.text },
    termMode: { fontSize: 11, color: th.textMuted, marginTop: 1 },
    bellBtn: { padding: 5, borderRadius: 8, backgroundColor: th.divider },
    bellBtnOn: { backgroundColor: th.primaryBg },
    bellIcon: { fontSize: 16 },
    delBtn: { backgroundColor: '#FEE2E2', paddingHorizontal: 10, paddingVertical: 5, borderRadius: 8 },
    delText: { color: '#DC2626', fontSize: 12, fontWeight: '600' },
    emptyTerms: { fontSize: 13, color: th.textMuted, textAlign: 'center', padding: 16 },
    // Mode chip inside term row
    modeChip: {
      paddingHorizontal: 7, paddingVertical: 2, borderRadius: 999,
      backgroundColor: th.divider,
    },
    modeChipText: { fontSize: 10, fontWeight: '600', color: th.textMuted },
    // Add-form mode selector
    modeRow: { flexDirection: 'row', gap: 6 },
    modeBtn: {
      flex: 1, alignItems: 'center', paddingVertical: 7, borderRadius: 8,
      borderWidth: 1.5, borderColor: th.border, backgroundColor: th.divider,
    },
    modeBtnOn: { backgroundColor: th.primaryBg, borderColor: th.primary },
    modeBtnText: { fontSize: 12, fontWeight: '600', color: th.textMuted },
    modeBtnTextOn: { color: th.primary },
    // Toggle (auto-translate, etc.)
    toggleRow: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12,
      borderBottomWidth: 1, borderColor: th.divider,
    },
    toggleTrack: {
      width: 44, height: 26, borderRadius: 13,
      backgroundColor: th.divider, justifyContent: 'center', paddingHorizontal: 2,
    },
    toggleTrackOn: { backgroundColor: th.primary },
    toggleThumb: {
      width: 22, height: 22, borderRadius: 11, backgroundColor: '#fff',
      shadowColor: '#000', shadowOffset: { width: 0, height: 1 },
      shadowOpacity: 0.18, shadowRadius: 2, elevation: 2,
    },
    toggleThumbOn: { alignSelf: 'flex-end' },
    // Font size stepper
    stepperRow: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 10,
      borderBottomWidth: 1, borderColor: th.divider,
    },
    stepperBtns: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    stepperBtn: {
      width: 30, height: 30, borderRadius: 15,
      backgroundColor: th.divider, alignItems: 'center', justifyContent: 'center',
    },
    stepperBtnText: { fontSize: 18, fontWeight: '700', color: th.text, lineHeight: 22 },
    stepperValue: { fontSize: 13, fontWeight: '700', color: th.text, minWidth: 28, textAlign: 'center' },
    // API key section
    apiKeyRow: {
      paddingHorizontal: 16, paddingVertical: 10,
      borderBottomWidth: 1, borderColor: th.divider,
    },
    apiKeyLabel: { fontSize: 12, fontWeight: '600', color: th.textSub, marginBottom: 6 },
    apiKeyInput: {
      borderWidth: 1, borderColor: th.border, borderRadius: 8,
      paddingHorizontal: 10, paddingVertical: 7,
      fontSize: 13, color: th.text, backgroundColor: th.inputBg,
      fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    },
    // Twitter login section
    twitterConnectedRow: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12,
      borderBottomWidth: 1, borderColor: th.divider,
    },
    twitterConnectedLabel: { fontSize: 13, color: th.textSub, fontWeight: '500' },
    twitterConnectedValue: { fontSize: 13, color: th.primary, fontWeight: '700' },
    twitterFormRow: {
      paddingHorizontal: 16, paddingVertical: 10,
      gap: 8,
    },
    twitterInput: {
      borderWidth: 1, borderColor: th.border, borderRadius: 8,
      paddingHorizontal: 10, paddingVertical: 8,
      fontSize: 13, color: th.text, backgroundColor: th.inputBg,
    },
    twitterConnectBtn: {
      backgroundColor: th.primary, borderRadius: 8,
      paddingVertical: 10, alignItems: 'center', marginTop: 4,
    },
    twitterConnectBtnOff: { opacity: 0.4 },
    twitterConnectBtnText: { color: '#fff', fontWeight: '700', fontSize: 14 },
    twitterDisconnectBtn: {
      paddingHorizontal: 16, paddingVertical: 10,
    },
    twitterDisconnectText: { color: '#DC2626', fontSize: 13, fontWeight: '600' },
    twitterHint: { fontSize: 11, color: th.textMuted, lineHeight: 16, marginTop: 4 },
    // Source rows — checkmark style
    sourceRow: {
      flexDirection: 'row', alignItems: 'center',
      paddingHorizontal: 16, paddingVertical: 10, gap: 12,
      borderBottomWidth: 1, borderColor: th.divider,
    },
    checkCircle: {
      width: 22, height: 22, borderRadius: 11,
      borderWidth: 2, borderColor: th.border,
      justifyContent: 'center', alignItems: 'center',
    },
    checkCircleOn: { backgroundColor: th.primary, borderColor: th.primary },
    checkMark: { fontSize: 12, color: '#fff', fontWeight: '800' },
    sourceIcon: { fontSize: 20, width: 28, textAlign: 'center' },
    sourceLabel: { flex: 1, fontSize: 13, fontWeight: '400', color: th.text },
    notice: {
      marginTop: 24, padding: 14,
      backgroundColor: th.mode === 'dark' ? '#052e16' : '#F0FDF4',
      borderRadius: 12, borderWidth: 1,
      borderColor: th.mode === 'dark' ? '#14532d' : '#BBF7D0',
    },
    noticeText: { fontSize: 13, color: th.mode === 'dark' ? '#86efac' : '#166534', lineHeight: 19 },
  })
}

const SOURCES = [
  { id: 'youtube',      label: 'YouTube',      icon: '▶️' },
  { id: 'niconico',     label: 'ニコニコ',      icon: '🎬' },
  { id: 'tver',         label: 'TVer',         icon: '📺' },
  { id: 'note',         label: 'Note',         icon: '📝' },
  { id: 'girlschannel', label: 'GirlsChannel', icon: '💗' },
  { id: '5ch',          label: '5ch',          icon: '💬' },
  { id: 'togetter',     label: 'Togetter',     icon: '🧵' },
  { id: 'news',         label: 'ニュース',      icon: '📰' },
  { id: 'yahoonews',    label: 'Yahoo!ニュース', icon: '🗞️' },
  { id: 'mdpr',         label: 'モデルプレス',   icon: '💄' },
  { id: 'custom',       label: 'Custom URL',   icon: '🔗' },
]


const LANGS: Array<{ code: Lang; label: string }> = [
  { code: 'ja',    label: '日本語' },
  { code: 'en',    label: 'English' },
  { code: 'zh-TW', label: '繁體中文' },
  { code: 'zh-CN', label: '简体中文' },
]

export default function SettingsScreen() {
  const { theme, mode, pref, setThemePref, style, setStyle } = useTheme()
  const { t, lang, setLang } = useLang()
  const s = useMemo(() => makeStyles(theme), [theme])
  const qc = useQueryClient()
  const navigation = useNavigation<any>()

  const [keyword, setKeyword] = useState('')
  const [addMode, setAddMode] = useState<'all_info' | 'media_only'>('all_info')
  const [expandedAppearance, setExpandedAppearance] = useState<'theme' | 'style' | 'language' | null>(null)
  const { settings, save: saveSettings } = useSettings()
  const [ytKey, setYtKey] = useState('')
  useEffect(() => { setYtKey(settings.youtubeApiKey) }, [settings.youtubeApiKey])

  // Twitter account
  const [twUsername, setTwUsername] = useState('')
  const [twPassword, setTwPassword] = useState('')
  const [twEmail, setTwEmail] = useState('')
  const { data: twStatus, refetch: refetchTwStatus } = useQuery({
    queryKey: ['twitter-status'],
    queryFn: fetchTwitterStatus,
    retry: false,
  })

  const twLogin = useMutation({
    mutationFn: () => twitterLogin(twUsername.trim(), twPassword, twEmail.trim() || undefined),
    onSuccess: (data) => {
      if (data.logged_in) {
        setTwUsername('')
        setTwPassword('')
        setTwEmail('')
        refetchTwStatus()
        Alert.alert('🐦 Twitter', `Connected as @${data.username}`)
      } else {
        Alert.alert('Login Failed', 'Could not log in. Check your username and password.')
      }
    },
    onError: (e: Error) => Alert.alert('Error', e.message),
  })

  const twDisconnect = useMutation({
    mutationFn: twitterDisconnect,
    onSuccess: () => { refetchTwStatus(); Alert.alert('Twitter', 'Disconnected.') },
    onError: (e: Error) => Alert.alert('Error', e.message),
  })

  const { data: terms = [] } = useQuery({ queryKey: ['watch-terms'], queryFn: getTerms })
  const { data: _persistedSubs } = useQuery({
    queryKey: ['subscribed-platforms'],
    queryFn: getSubscribedPlatforms,
  })
  // Local state for instant toggle feedback; initialised from persisted data once loaded
  const [localSubs, setLocalSubs] = useState<string[] | null>(null)
  const subscribedPlatforms = localSubs ?? _persistedSubs ?? ALL_PLATFORM_IDS
  const { data: oshiAvatars = {} } = useQuery({
    queryKey: ['oshi-avatars'],
    queryFn: getOshiAvatars,
  })
  const { data: wallpaper = null } = useQuery({
    queryKey: ['wallpaper'],
    queryFn: getWallpaper,
  })


  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['watch-terms'] })
    qc.invalidateQueries({ queryKey: ['feed'] })
  }

  const togglePlatform = async (id: string) => {
    const next = subscribedPlatforms.includes(id)
      ? subscribedPlatforms.filter(p => p !== id)
      : [...subscribedPlatforms, id]
    setLocalSubs(next)                               // instant UI update
    await setSubscribedPlatforms(next)               // persist
    qc.setQueryData(['subscribed-platforms'], next)  // sync query cache
    qc.invalidateQueries({ queryKey: ['subscribed-platforms'] })
    qc.invalidateQueries({ queryKey: ['sources-order'] })
    qc.invalidateQueries({ queryKey: ['feed'] })
  }

  const add = useMutation({
    mutationFn: async (kw: string) => {
      const term = await saveTerm({ keyword: kw, collection_mode: addMode })
      scrapeAll(term.keyword, term.collection_mode)
        .then(items => mergeItems(items))
        .then(() => qc.invalidateQueries({ queryKey: ['feed'] }))
        .catch(() => {})
      return term
    },
    onSuccess: () => { invalidate(); setKeyword(''); setAddMode('all_info') },
    onError: (e: Error) => Alert.alert(t('errorAlert'), e.message),
  })

  const toggleActive = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      updateTerm(id, { is_active }),
    onSuccess: invalidate,
  })

  const toggleNotify = useMutation({
    mutationFn: async ({ id, notify_on_new }: { id: string; notify_on_new: boolean }) => {
      if (notify_on_new) {
        const granted = await requestNotificationPermission()
        if (!granted) {
          Alert.alert(t('notifPermTitle'), t('notifPermBody'))
          return
        }
      }
      await updateTerm(id, { notify_on_new })
    },
    onSuccess: invalidate,
  })

  const remove = useMutation({
    mutationFn: deleteTerm,
    onSuccess: invalidate,
    onError: (e: Error) => Alert.alert(t('errorAlert'), e.message),
  })

  const clearAll = () =>
    Alert.alert(t('clearAllTitle'), t('clearAllBody'), [
      { text: t('cancel'), style: 'cancel' },
      {
        text: t('delete'), style: 'destructive', onPress: async () => {
          await AsyncStorage.multiRemove(['@otterpia:terms', '@otterpia:items'])
          qc.invalidateQueries()
        },
      },
    ])

  return (
    <KeyboardAvoidingView style={s.container} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={s.scroll} keyboardShouldPersistTaps="handled">

        {/* Watch Keywords */}
        <Text style={s.section}>👀 {t('watchKeywords')}</Text>
        <View style={s.formCard}>
          <TextInput
            style={s.input}
            placeholder={t('keywordPlaceholder')}
            placeholderTextColor={theme.textMuted}
            value={keyword}
            onChangeText={setKeyword}
            returnKeyType="done"
            onSubmitEditing={() => { if (keyword.trim()) add.mutate(keyword.trim()) }}
          />
          <View style={s.modeRow}>
            <Pressable style={[s.modeBtn, addMode === 'all_info' && s.modeBtnOn]} onPress={() => setAddMode('all_info')}>
              <Text style={[s.modeBtnText, addMode === 'all_info' && s.modeBtnTextOn]}>📄 {t('allInfo')}</Text>
            </Pressable>
            <Pressable style={[s.modeBtn, addMode === 'media_only' && s.modeBtnOn]} onPress={() => setAddMode('media_only')}>
              <Text style={[s.modeBtnText, addMode === 'media_only' && s.modeBtnTextOn]}>📹 {t('mediaOnly')}</Text>
            </Pressable>
          </View>
          <Pressable
            style={[s.addBtn, (!keyword.trim() || add.isPending) && s.addBtnOff]}
            onPress={() => { if (keyword.trim()) add.mutate(keyword.trim()) }}
            disabled={!keyword.trim() || add.isPending}
          >
            <Text style={s.addBtnText}>{add.isPending ? t('searching') : t('add')}</Text>
          </Pressable>
        </View>

        {terms.length > 0 ? (
          <View style={[s.card, { marginTop: 8 }]}>
            {terms.map((term, i) => (
              <View key={term.id} style={[s.termRow, !term.is_active && s.termRowInactive, i === terms.length - 1 && s.termRowLast]}>
                <Pressable
                  style={s.avatarBtn}
                  onPress={() => navigation.navigate('AvatarEditor', { keyword: term.keyword })}
                  hitSlop={4}
                >
                  {oshiAvatars[term.keyword] ? (
                    <Image source={{ uri: oshiAvatars[term.keyword] }} style={s.avatarImg} resizeMode="cover" />
                  ) : (
                    <Text style={s.avatarPlaceholder}>🎨</Text>
                  )}
                </Pressable>
                <View style={s.termLeft}>
                  <Text style={s.termKeyword}>{term.keyword}</Text>
                </View>
                <Pressable
                  style={s.modeChip}
                  onPress={() => updateTerm(term.id, { collection_mode: term.collection_mode === 'all_info' ? 'media_only' : 'all_info' }).then(invalidate)}
                  hitSlop={8}
                >
                  <Text style={s.modeChipText}>{term.collection_mode === 'media_only' ? '📹' : '📄'}</Text>
                </Pressable>
                <Pressable
                  style={[s.bellBtn, term.notify_on_new && s.bellBtnOn]}
                  onPress={() => toggleNotify.mutate({ id: term.id, notify_on_new: !term.notify_on_new })}
                >
                  <Text style={s.bellIcon}>{term.notify_on_new ? '🔔' : '🔕'}</Text>
                </Pressable>
                <Pressable
                  onPress={() => toggleActive.mutate({ id: term.id, is_active: !term.is_active })}
                  style={[s.checkCircle, term.is_active && s.checkCircleOn]}
                >
                  {term.is_active && <Text style={s.checkMark}>✓</Text>}
                </Pressable>
                <Pressable
                  style={s.delBtn}
                  onPress={() =>
                    Alert.alert(
                      t('deleteConfirmTitle'),
                      t('stopWatchingBody').replace('%s', term.keyword),
                      [
                        { text: t('cancel'), style: 'cancel' },
                        { text: t('delete'), style: 'destructive', onPress: () => remove.mutate(term.id) },
                      ],
                    )
                  }
                >
                  <Text style={s.delText}>{t('delete')}</Text>
                </Pressable>
              </View>
            ))}
          </View>
        ) : (
          <View style={[s.card, { marginTop: 8 }]}>
            <Text style={s.emptyTerms}>(´• ω •`) {t('noKeywords')}</Text>
          </View>
        )}

        {/* Sources */}
        <Text style={[s.section, { marginTop: 24 }]}>📡 {t('sources')}</Text>
        <View style={s.card}>
          {SOURCES.map((src, i) => {
            const on = subscribedPlatforms.includes(src.id)
            return (
              <Pressable
                key={src.id}
                style={[s.sourceRow, i === SOURCES.length - 1 && s.rowLast]}
                onPress={() => togglePlatform(src.id)}
              >
                <View style={[s.checkCircle, on && s.checkCircleOn]}>
                  {on && <Text style={s.checkMark}>✓</Text>}
                </View>
                <Text style={s.sourceIcon}>{src.icon}</Text>
                <Text style={s.sourceLabel}>{src.label}</Text>
              </Pressable>
            )
          })}
        </View>

        {/* Appearance */}
        <Text style={[s.section, { marginTop: 24 }]}>🎨 {t('appearance')}</Text>
        <View style={s.card}>
          {/* Theme row */}
          <Pressable
            style={s.row}
            onPress={() => setExpandedAppearance(expandedAppearance === 'theme' ? null : 'theme')}
          >
            <Text style={s.rowLabel}>{t('theme')}</Text>
            <Text style={[s.rowValue, { color: theme.primary }]}>
              {pref === 'system' ? t('systemTheme') : pref === 'dark' ? t('darkTheme') : t('lightTheme')}
              {' '}{expandedAppearance === 'theme' ? '▲' : '▼'}
            </Text>
          </Pressable>
          {expandedAppearance === 'theme' && (
            <View style={s.optionRow}>
              <Pressable style={[s.optionBtn, pref === 'light' && s.optionBtnOn]} onPress={() => { setThemePref('light'); setExpandedAppearance(null) }}>
                <Text style={[s.optionBtnText, pref === 'light' && s.optionBtnTextOn]}>{t('lightTheme')}</Text>
              </Pressable>
              <Pressable style={[s.optionBtn, pref === 'dark' && s.optionBtnOn]} onPress={() => { setThemePref('dark'); setExpandedAppearance(null) }}>
                <Text style={[s.optionBtnText, pref === 'dark' && s.optionBtnTextOn]}>{t('darkTheme')}</Text>
              </Pressable>
              <Pressable style={[s.optionBtn, pref === 'system' && s.optionBtnOn]} onPress={() => { setThemePref('system'); setExpandedAppearance(null) }}>
                <Text style={[s.optionBtnText, pref === 'system' && s.optionBtnTextOn]}>{t('systemTheme')}</Text>
              </Pressable>
            </View>
          )}

          {/* Style row */}
          <Pressable
            style={s.row}
            onPress={() => setExpandedAppearance(expandedAppearance === 'style' ? null : 'style')}
          >
            <Text style={s.rowLabel}>Style</Text>
            <Text style={[s.rowValue, { color: theme.primary }]}>
              {style === 'standard' ? 'Standard' : 'Colourful'}
              {' '}{expandedAppearance === 'style' ? '▲' : '▼'}
            </Text>
          </Pressable>
          {expandedAppearance === 'style' && (
            <View style={s.optionRow}>
              <Pressable style={[s.optionBtn, style === 'colourful' && s.optionBtnOn]} onPress={() => { setStyle('colourful'); setExpandedAppearance(null) }}>
                <Text style={[s.optionBtnText, style === 'colourful' && s.optionBtnTextOn]}>Colourful</Text>
              </Pressable>
              <Pressable style={[s.optionBtn, style === 'standard' && s.optionBtnOn]} onPress={() => { setStyle('standard'); setExpandedAppearance(null) }}>
                <Text style={[s.optionBtnText, style === 'standard' && s.optionBtnTextOn]}>Standard</Text>
              </Pressable>
            </View>
          )}

          {/* Language row */}
          <Pressable
            style={s.row}
            onPress={() => setExpandedAppearance(expandedAppearance === 'language' ? null : 'language')}
          >
            <Text style={s.rowLabel}>{t('language')}</Text>
            <Text style={[s.rowValue, { color: theme.primary }]}>
              {LANG_LABELS[lang]} {expandedAppearance === 'language' ? '▲' : '▼'}
            </Text>
          </Pressable>
          {expandedAppearance === 'language' && (
            <View style={[s.optionRow, { borderBottomWidth: 1, borderColor: theme.divider }]}>
              {LANGS.map(l => (
                <Pressable
                  key={l.code}
                  style={[s.optionBtn, lang === l.code && s.optionBtnOn]}
                  onPress={() => { setLang(l.code as Lang); setExpandedAppearance(null) }}
                >
                  <Text style={[s.optionBtnText, lang === l.code && s.optionBtnTextOn]}>{l.label}</Text>
                </Pressable>
              ))}
            </View>
          )}

          {/* Font size */}
          <View style={s.stepperRow}>
            <Text style={s.rowLabel}>Aa {t('fontSize')}</Text>
            <View style={s.stepperBtns}>
              <Pressable
                style={s.stepperBtn}
                onPress={() => {
                  const next = Math.max(12, settings.fontSize - 1)
                  saveSettings({ ...settings, fontSize: next })
                }}
              >
                <Text style={s.stepperBtnText}>−</Text>
              </Pressable>
              <Text style={s.stepperValue}>{settings.fontSize}</Text>
              <Pressable
                style={s.stepperBtn}
                onPress={() => {
                  const next = Math.min(24, settings.fontSize + 1)
                  saveSettings({ ...settings, fontSize: next })
                }}
              >
                <Text style={s.stepperBtnText}>+</Text>
              </Pressable>
            </View>
          </View>

          {/* Auto-translate */}
          <Pressable
            style={[s.toggleRow, s.rowLast]}
            onPress={() => saveSettings({ ...settings, autoTranslate: !settings.autoTranslate })}
          >
            <Text style={s.rowLabel}>🌐 {t('autoTranslate')}</Text>
            <View style={[s.toggleTrack, settings.autoTranslate && s.toggleTrackOn]}>
              <View style={[s.toggleThumb, settings.autoTranslate && s.toggleThumbOn]} />
            </View>
          </Pressable>
        </View>

        {/* Wallpaper */}
        <View style={[s.card, { marginTop: 12 }]}>
          <View style={[s.row, s.rowLast]}>
            <Text style={s.rowLabel}>🌸 壁紙</Text>
            {wallpaper ? (
              <Pressable
                onPress={() => { setWallpaper(null).then(() => qc.invalidateQueries({ queryKey: ['wallpaper'] })) }}
                style={{ backgroundColor: '#FEE2E2', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8 }}
              >
                <Text style={{ color: '#DC2626', fontSize: 12, fontWeight: '600' }}>✕ 削除</Text>
              </Pressable>
            ) : (
              <Text style={s.rowValue}>なし</Text>
            )}
          </View>
        </View>

        {/* Twitter Account */}
        <Text style={[s.section, { marginTop: 24 }]}>🐦 Twitter</Text>
        <View style={s.card}>
          {twStatus?.logged_in ? (
            <>
              <View style={s.twitterConnectedRow}>
                <Text style={s.twitterConnectedLabel}>Connected as</Text>
                <Text style={s.twitterConnectedValue}>@{twStatus.username}</Text>
              </View>
              <Pressable
                style={s.twitterDisconnectBtn}
                onPress={() => twDisconnect.mutate()}
                disabled={twDisconnect.isPending}
              >
                <Text style={s.twitterDisconnectText}>
                  {twDisconnect.isPending ? 'Disconnecting…' : '✕ Disconnect Twitter'}
                </Text>
              </Pressable>
            </>
          ) : (
            <View style={s.twitterFormRow}>
              <TextInput
                style={s.twitterInput}
                placeholder="Twitter username"
                placeholderTextColor={theme.textMuted}
                value={twUsername}
                onChangeText={setTwUsername}
                autoCapitalize="none"
                autoCorrect={false}
              />
              <TextInput
                style={s.twitterInput}
                placeholder="Password"
                placeholderTextColor={theme.textMuted}
                value={twPassword}
                onChangeText={setTwPassword}
                secureTextEntry
                autoCapitalize="none"
                autoCorrect={false}
              />
              <TextInput
                style={s.twitterInput}
                placeholder="Email (optional, for 2FA accounts)"
                placeholderTextColor={theme.textMuted}
                value={twEmail}
                onChangeText={setTwEmail}
                autoCapitalize="none"
                keyboardType="email-address"
                autoCorrect={false}
              />
              <Pressable
                style={[s.twitterConnectBtn, (!twUsername.trim() || !twPassword || twLogin.isPending) && s.twitterConnectBtnOff]}
                onPress={() => twLogin.mutate()}
                disabled={!twUsername.trim() || !twPassword || twLogin.isPending}
              >
                {twLogin.isPending
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Text style={s.twitterConnectBtnText}>Connect Twitter Account</Text>
                }
              </Pressable>
              <Text style={s.twitterHint}>
                Your credentials are sent to your backend and used to log into Twitter's internal API. Login may take 10–30 seconds.
              </Text>
            </View>
          )}
        </View>

        {/* API Keys */}
        <Text style={[s.section, { marginTop: 24 }]}>🔑 API</Text>
        <View style={s.card}>
          <View style={[s.apiKeyRow, s.rowLast]}>
            <Text style={s.apiKeyLabel}>YouTube Data API v3</Text>
            <TextInput
              style={s.apiKeyInput}
              value={ytKey}
              onChangeText={setYtKey}
              placeholder="AIza..."
              placeholderTextColor={theme.textMuted}
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="done"
              onSubmitEditing={() => saveSettings({ ...settings, youtubeApiKey: ytKey.trim() })}
              onBlur={() => saveSettings({ ...settings, youtubeApiKey: ytKey.trim() })}
            />
          </View>
        </View>

        {/* Data */}
        <Text style={[s.section, { marginTop: 24 }]}>🗑️ {t('data')}</Text>
        <View style={s.card}>
          <Pressable
            style={({ pressed }) => [s.row, s.rowLast, pressed && { opacity: 0.6 }]}
            onPress={clearAll}
          >
            <Text style={[s.rowLabel, { color: '#DC2626' }]}>{t('clearAll')}</Text>
          </Pressable>
        </View>

        <View style={s.notice}>
          <Text style={s.noticeText}>{t('noticeText')}</Text>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}
