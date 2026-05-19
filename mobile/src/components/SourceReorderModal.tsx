import { useEffect, useRef, useState } from 'react'
import { Dimensions, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { PLATFORM_META, PLATFORM_DISPLAY, DEFAULT_META } from './FeedCard'
import { useTheme } from '../ThemeContext'

const SCREEN_H = Dimensions.get('window').height
const ITEM_H = 58

interface Props {
  visible: boolean
  platforms: string[]
  onSave: (newOrder: string[]) => void
  onClose: () => void
}

export function SourceReorderModal({ visible, platforms, onSave, onClose }: Props) {
  const { theme } = useTheme()
  const [order, setOrder] = useState<string[]>(platforms)
  const orderRef = useRef<string[]>(order)

  useEffect(() => {
    if (!visible) return
    const next = [...platforms]
    setOrder(next)
    orderRef.current = next
  }, [visible, platforms])

  const move = (id: string, direction: -1 | 1) => {
    const cur = orderRef.current
    const index = cur.indexOf(id)
    const target = index + direction
    if (index < 0 || target < 0 || target >= cur.length) return
    const next = [...cur]
    const [removed] = next.splice(index, 1)
    next.splice(target, 0, removed)
    orderRef.current = next
    setOrder(next)
  }

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, justifyContent: 'flex-end' }}>
        <Pressable
          style={{ ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(0,0,0,0.45)' }}
          onPress={onClose}
        />
        <View
          style={{
            backgroundColor: theme.card,
            borderTopLeftRadius: 16,
            borderTopRightRadius: 16,
            paddingBottom: 34,
            maxHeight: SCREEN_H * 0.72,
          }}
        >
          <View
            style={{
              flexDirection: 'row',
              justifyContent: 'space-between',
              alignItems: 'center',
              paddingHorizontal: 20,
              paddingVertical: 14,
              borderBottomWidth: 1,
              borderColor: theme.divider,
            }}
          >
            <Pressable hitSlop={8} onPress={onClose}>
              <Text style={{ fontSize: 14, color: theme.textMuted }}>キャンセル</Text>
            </Pressable>
            <Text style={{ fontSize: 15, fontWeight: '700', color: theme.text }}>ソース順序</Text>
            <Pressable hitSlop={8} onPress={() => { onSave([...orderRef.current]); onClose() }}>
              <Text style={{ fontSize: 14, fontWeight: '700', color: theme.primary }}>保存</Text>
            </Pressable>
          </View>
          <ScrollView style={{ flexGrow: 0 }}>
            {order.map((id, index) => {
              const meta = PLATFORM_META[id] ?? DEFAULT_META
              const label = PLATFORM_DISPLAY[id] ?? id
              return (
                <View
                  key={id}
                  style={{
                    height: ITEM_H,
                    flexDirection: 'row',
                    alignItems: 'center',
                    paddingHorizontal: 16,
                    gap: 12,
                    backgroundColor: theme.card,
                    borderBottomWidth: 1,
                    borderColor: theme.divider,
                  }}
                >
                  <Text style={{ fontSize: 20 }}>{meta.icon}</Text>
                  <Text style={{ flex: 1, fontSize: 14, fontWeight: '600', color: theme.text }}>{label}</Text>
                  <Pressable
                    disabled={index === 0}
                    onPress={() => move(id, -1)}
                    style={{ padding: 8, opacity: index === 0 ? 0.25 : 1 }}
                    hitSlop={8}
                  >
                    <Text style={{ fontSize: 18, color: theme.primary }}>↑</Text>
                  </Pressable>
                  <Pressable
                    disabled={index === order.length - 1}
                    onPress={() => move(id, 1)}
                    style={{ padding: 8, opacity: index === order.length - 1 ? 0.25 : 1 }}
                    hitSlop={8}
                  >
                    <Text style={{ fontSize: 18, color: theme.primary }}>↓</Text>
                  </Pressable>
                </View>
              )
            })}
          </ScrollView>
        </View>
      </View>
    </Modal>
  )
}
