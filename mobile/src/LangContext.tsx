import { createContext, ReactNode, useContext, useEffect, useState } from 'react'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { type Lang, getT } from './i18n'

interface LangCtx {
  lang: Lang
  setLang: (l: Lang) => void
  t: ReturnType<typeof getT>
}

const defaultLang: Lang = 'ja'
const Ctx = createContext<LangCtx>({ lang: defaultLang, setLang: () => {}, t: getT(defaultLang) })

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(defaultLang)

  useEffect(() => {
    AsyncStorage.getItem('@otterpia:lang').then(v => {
      if (v === 'ja' || v === 'en' || v === 'zh-TW' || v === 'zh-CN') setLangState(v)
    })
  }, [])

  const setLang = (l: Lang) => {
    setLangState(l)
    AsyncStorage.setItem('@otterpia:lang', l)
  }

  return <Ctx.Provider value={{ lang, setLang, t: getT(lang) }}>{children}</Ctx.Provider>
}

export function useLang() {
  return useContext(Ctx)
}
