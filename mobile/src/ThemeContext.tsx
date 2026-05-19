import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import { Platform, useColorScheme } from 'react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { light, dark, androidLight, androidDark, type Theme, type ThemeMode } from './theme'
import type { ColorStyle } from './styleConstants'

export type ThemePref = 'light' | 'dark' | 'system'

function resolveTheme(mode: ThemeMode): Theme {
  if (Platform.OS === 'android') return mode === 'dark' ? androidDark : androidLight
  return mode === 'dark' ? dark : light
}

interface ThemeCtx {
  theme: Theme
  mode: ThemeMode
  pref: ThemePref
  setThemePref: (pref: ThemePref) => void
  toggle: () => void
  style: ColorStyle
  setStyle: (s: ColorStyle) => void
}

const Ctx = createContext<ThemeCtx>({
  theme: light,
  mode: 'light',
  pref: 'system',
  setThemePref: () => {},
  toggle: () => {},
  style: 'colourful',
  setStyle: () => {},
})

export function ThemeProvider({ children }: { children: ReactNode }) {
  const systemScheme = useColorScheme()
  const [pref, setPref] = useState<ThemePref>('system')
  const [style, setStyleState] = useState<ColorStyle>('colourful')

  useEffect(() => {
    AsyncStorage.multiGet(['@otterpia:theme', '@otterpia:style']).then(pairs => {
      const themePref = pairs[0][1]
      const stylePref = pairs[1][1]
      if (themePref === 'light' || themePref === 'dark' || themePref === 'system') setPref(themePref)
      if (stylePref === 'colourful' || stylePref === 'standard') setStyleState(stylePref)
    })
  }, [])

  const setThemePref = (next: ThemePref) => {
    setPref(next)
    AsyncStorage.setItem('@otterpia:theme', next)
  }

  const setStyle = (next: ColorStyle) => {
    setStyleState(next)
    AsyncStorage.setItem('@otterpia:style', next)
  }

  const toggle = () => {
    setThemePref(pref === 'light' ? 'dark' : pref === 'dark' ? 'system' : 'light')
  }

  const mode: ThemeMode = pref === 'system'
    ? (systemScheme === 'dark' ? 'dark' : 'light')
    : pref

  return (
    <Ctx.Provider value={{ theme: resolveTheme(mode), mode, pref, setThemePref, toggle, style, setStyle }}>
      {children}
    </Ctx.Provider>
  )
}

export const useTheme = () => useContext(Ctx)
