import { useMemo, useState } from 'react'
import {
  KeyboardAvoidingView, Modal, Platform,
  Pressable, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useQueryClient } from '@tanstack/react-query'
import { addCustomUrl, mergeItems } from '../localDb'
import { scrapeCustomUrls } from '../scraper/custom'
import { useTheme } from '../ThemeContext'
import type { Theme } from '../theme'

interface Props {
  visible: boolean
  onClose: () => void
}

function makeStyles(t: Theme) {
  return StyleSheet.create({
    overlay: { flex: 1 },
    sheet: {
      backgroundColor: t.card, borderTopLeftRadius: 24, borderTopRightRadius: 24,
      padding: 24, paddingBottom: 40,
      shadowColor: '#000', shadowOffset: { width: 0, height: -2 },
      shadowOpacity: 0.12, shadowRadius: 12, elevation: 10,
    },
    sheetHandle: {
      width: 40, height: 4, backgroundColor: t.border,
      borderRadius: 2, alignSelf: 'center', marginBottom: 20,
    },
    title: { fontSize: 17, fontWeight: '700', color: t.text, marginBottom: 20 },
    label: { fontSize: 12, fontWeight: '600', color: t.textMuted, marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5 },
    input: {
      borderWidth: 1.5, borderColor: t.border, borderRadius: 10,
      paddingHorizontal: 14, paddingVertical: 11,
      fontSize: 14, color: t.text, marginBottom: 16,
      backgroundColor: t.inputBg,
    },
    saveBtn: { backgroundColor: t.primary, borderRadius: 12, paddingVertical: 14, alignItems: 'center', marginTop: 4 },
    saveBtnDisabled: { opacity: 0.4 },
    saveBtnText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  })
}

export function AddUrlModal({ visible, onClose }: Props) {
  const { theme } = useTheme()
  const s = useMemo(() => makeStyles(theme), [theme])
  const qc = useQueryClient()
  const [urlInput, setUrlInput] = useState('')
  const [titleInput, setTitleInput] = useState('')
  const [saving, setSaving] = useState(false)

  const handleAdd = async () => {
    const url = urlInput.trim()
    if (!url) return
    setSaving(true)
    try {
      const fullUrl = url.startsWith('http') ? url : `https://${url}`
      await addCustomUrl(fullUrl, titleInput)
      setUrlInput('')
      setTitleInput('')
      onClose()
      scrapeCustomUrls().then(items => mergeItems(items)).then(() => {
        qc.invalidateQueries({ queryKey: ['feed'] })
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={s.overlay} onPress={onClose} />
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={s.sheet}>
        <View style={s.sheetHandle} />
        <Text style={s.title}>URLをフィードに追加</Text>

        <Text style={s.label}>URL</Text>
        <TextInput
          style={s.input}
          placeholder="https://..."
          placeholderTextColor={theme.textMuted}
          value={urlInput}
          onChangeText={setUrlInput}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          autoFocus
        />

        <Text style={s.label}>タイトル（任意）</Text>
        <TextInput
          style={s.input}
          placeholder="ページ名を入力"
          placeholderTextColor={theme.textMuted}
          value={titleInput}
          onChangeText={setTitleInput}
        />

        <Pressable
          style={[s.saveBtn, (!urlInput.trim() || saving) && s.saveBtnDisabled]}
          onPress={handleAdd}
          disabled={!urlInput.trim() || saving}
        >
          <Text style={s.saveBtnText}>{saving ? '保存中…' : '追加する'}</Text>
        </Pressable>
      </KeyboardAvoidingView>
    </Modal>
  )
}
