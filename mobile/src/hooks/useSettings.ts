import AsyncStorage from '@react-native-async-storage/async-storage'
import { useState, useEffect, useCallback } from 'react'

export interface Settings {
  youtubeApiKey: string
  twitterBearerToken: string
  autoTranslate: boolean
  fontSize: number
}

export const DEFAULT_FONT_SIZE = 17
const KEYS = ['@otterpia/yt_key', '@otterpia/tw_token', '@otterpia/auto_translate', '@otterpia/font_size'] as const

export function useSettings() {
  const [settings, setSettings] = useState<Settings>({
    youtubeApiKey: '',
    twitterBearerToken: '',
    autoTranslate: false,
    fontSize: DEFAULT_FONT_SIZE,
  })
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    AsyncStorage.multiGet([...KEYS]).then((pairs) => {
      setSettings({
        youtubeApiKey: pairs[0][1] ?? '',
        twitterBearerToken: pairs[1][1] ?? '',
        autoTranslate: pairs[2][1] === '1',
        fontSize: pairs[3][1] ? parseInt(pairs[3][1], 10) : DEFAULT_FONT_SIZE,
      })
      setLoaded(true)
    })
  }, [])

  const save = useCallback(async (updated: Settings) => {
    await AsyncStorage.multiSet([
      [KEYS[0], updated.youtubeApiKey],
      [KEYS[1], updated.twitterBearerToken],
      [KEYS[2], updated.autoTranslate ? '1' : '0'],
      [KEYS[3], String(updated.fontSize)],
    ])
    setSettings(updated)
  }, [])

  return { settings, save, loaded }
}
