import { useMemo, useState } from 'react'
import {
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getTerms, saveTerm, deleteTerm, updateTerm, mergeItems, type WatchTerm } from '../localDb'
import { scrapeAll } from '../scraper'
import { requestNotificationPermission } from '../notifications'
import { useTheme } from '../ThemeContext'
import type { Theme } from '../theme'

function makeStyles(t: Theme) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: t.bg },
    form: { backgroundColor: t.card, padding: 16, gap: 10, borderBottomWidth: 1, borderColor: t.divider },
    input: {
      borderWidth: 1, borderColor: t.border, borderRadius: 10,
      paddingHorizontal: 12, paddingVertical: 10,
      fontSize: 15, color: t.text, backgroundColor: t.inputBg,
    },
    formRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
    modeBtn: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 8, borderWidth: 1, borderColor: t.border },
    modeBtnOn: { backgroundColor: t.primaryBg, borderColor: t.primary },
    modeBtnText: { fontSize: 13, color: t.textMuted, fontWeight: '500' },
    modeBtnTextOn: { color: t.primary },
    addBtn: { marginLeft: 'auto', backgroundColor: t.primary, paddingHorizontal: 18, paddingVertical: 8, borderRadius: 8 },
    addBtnOff: { opacity: 0.4 },
    addBtnText: { color: '#fff', fontWeight: '600', fontSize: 14 },
    list: { paddingBottom: 40 },
    empty: { textAlign: 'center', color: t.textMuted, marginTop: 40, fontSize: 14 },
    row: {
      flexDirection: 'row', alignItems: 'center', backgroundColor: t.card,
      paddingHorizontal: 16, paddingVertical: 14, gap: 12,
      borderBottomWidth: 1, borderColor: t.divider,
    },
    rowInactive: { opacity: 0.45 },
    rowLeft: { flex: 1 },
    termKeyword: { fontSize: 15, fontWeight: '600', color: t.text },
    termMode: { fontSize: 12, color: t.textMuted, marginTop: 2 },
    bellBtn: { padding: 6, borderRadius: 8, backgroundColor: t.divider },
    bellBtnOn: { backgroundColor: t.primaryBg },
    bellIcon: { fontSize: 18 },
    delBtn: { backgroundColor: '#FEE2E2', paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8 },
    delBtnPressed: { opacity: 0.7 },
    delText: { color: '#DC2626', fontSize: 13, fontWeight: '600' },
  })
}

export default function WatchTermsScreen() {
  const { theme } = useTheme()
  const s = useMemo(() => makeStyles(theme), [theme])
  const qc = useQueryClient()
  const [keyword, setKeyword] = useState('')
  const [mode, setMode] = useState<'all_info' | 'media_only'>('all_info')

  const { data: terms = [] } = useQuery({ queryKey: ['watch-terms'], queryFn: getTerms })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['watch-terms'] })
    qc.invalidateQueries({ queryKey: ['feed'] })
  }

  const add = useMutation({
    mutationFn: async (kw: string) => {
      const term = await saveTerm({ keyword: kw, collection_mode: mode })
      scrapeAll(term.keyword, term.collection_mode)
        .then(items => mergeItems(items))
        .then(() => qc.invalidateQueries({ queryKey: ['feed'] }))
        .catch(() => {})
      return term
    },
    onSuccess: () => { invalidate(); setKeyword('') },
    onError: (e: Error) => Alert.alert('エラー', e.message),
  })

  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      updateTerm(id, { is_active }),
    onSuccess: invalidate,
  })

  const toggleNotify = useMutation({
    mutationFn: async ({ id, notify_on_new }: { id: string; notify_on_new: boolean }) => {
      if (notify_on_new) {
        const granted = await requestNotificationPermission()
        if (!granted) {
          Alert.alert('通知の許可が必要です', '通知を使用するには端末の設定で通知を有効にしてください。')
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
  })

  return (
    <KeyboardAvoidingView style={s.container} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={s.form}>
        <TextInput
          style={s.input}
          placeholder="キーワード（例：山田裕貴）"
          placeholderTextColor={theme.textMuted}
          value={keyword}
          onChangeText={setKeyword}
          returnKeyType="done"
          onSubmitEditing={() => { if (keyword.trim()) add.mutate(keyword.trim()) }}
        />
        <View style={s.formRow}>
          <Pressable style={[s.modeBtn, mode === 'all_info' && s.modeBtnOn]} onPress={() => setMode('all_info')}>
            <Text style={[s.modeBtnText, mode === 'all_info' && s.modeBtnTextOn]}>全情報</Text>
          </Pressable>
          <Pressable style={[s.modeBtn, mode === 'media_only' && s.modeBtnOn]} onPress={() => setMode('media_only')}>
            <Text style={[s.modeBtnText, mode === 'media_only' && s.modeBtnTextOn]}>メディアのみ</Text>
          </Pressable>
          <Pressable
            style={[s.addBtn, (!keyword.trim() || add.isPending) && s.addBtnOff]}
            onPress={() => { if (keyword.trim()) add.mutate(keyword.trim()) }}
            disabled={!keyword.trim() || add.isPending}
          >
            <Text style={s.addBtnText}>{add.isPending ? '検索中…' : '追加'}</Text>
          </Pressable>
        </View>
      </View>

      <FlatList
        data={terms}
        keyExtractor={(t) => t.id}
        contentContainerStyle={s.list}
        ListEmptyComponent={<Text style={s.empty}>まだキーワードがありません</Text>}
        ItemSeparatorComponent={() => <View style={{ height: 1, backgroundColor: theme.divider }} />}
        renderItem={({ item: t }: { item: WatchTerm }) => (
          <View style={[s.row, !t.is_active && s.rowInactive]}>
            <View style={s.rowLeft}>
              <Text style={s.termKeyword}>{t.keyword}</Text>
              <Text style={s.termMode}>{t.collection_mode === 'media_only' ? 'メディアのみ' : '全情報'}</Text>
            </View>
            <Pressable
              style={[s.bellBtn, t.notify_on_new && s.bellBtnOn]}
              onPress={() => toggleNotify.mutate({ id: t.id, notify_on_new: !(t.notify_on_new ?? false) })}
            >
              <Text style={s.bellIcon}>{t.notify_on_new ? '🔔' : '🔕'}</Text>
            </Pressable>
            <Switch
              value={t.is_active}
              onValueChange={(v) => toggle.mutate({ id: t.id, is_active: v })}
              trackColor={{ false: theme.border, true: theme.primary }}
              thumbColor="#fff"
            />
            <Pressable
              style={({ pressed }) => [s.delBtn, pressed && s.delBtnPressed]}
              onPress={() =>
                Alert.alert('削除', `「${t.keyword}」の監視を停止しますか？`, [
                  { text: 'キャンセル', style: 'cancel' },
                  { text: '削除', style: 'destructive', onPress: () => remove.mutate(t.id) },
                ])
              }
            >
              <Text style={s.delText}>削除</Text>
            </Pressable>
          </View>
        )}
      />
    </KeyboardAvoidingView>
  )
}
