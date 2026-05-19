export type ThemeMode = 'light' | 'dark'
export type ThemePlatform = 'ios' | 'android'

export interface Theme {
  mode: ThemeMode
  bg: string
  card: string
  text: string
  textSub: string
  textMuted: string
  border: string
  divider: string
  primary: string
  primaryBg: string
  inputBg: string
}

export const light: Theme = {
  mode: 'light',
  bg: '#F7F5FA',
  card: '#FFFFFF',
  text: '#1A1625',
  textSub: '#3D3550',
  textMuted: '#9E94AF',
  border: '#E4DFF0',
  divider: '#F0EDF8',
  primary: '#9B7FA8',
  primaryBg: '#F0EBF7',
  inputBg: '#FFFFFF',
}

export const dark: Theme = {
  mode: 'dark',
  bg: '#110F18',
  card: '#1E1A28',
  text: '#F5F2FF',
  textSub: '#CEC8DE',
  textMuted: '#7A7290',
  border: '#302A40',
  divider: '#27223A',
  primary: '#C0A8D8',
  primaryBg: '#2A2040',
  inputBg: '#252030',
}

// Material Design 3 (Material You) themes for Android
// Seed color: amethyst smoke — tonal palette derived from #9B7FA8
export const androidLight: Theme = {
  mode: 'light',
  bg: '#FFFBFF',        // M3 Surface
  card: '#F7F2FF',      // M3 Surface Container Low
  text: '#1C1B1F',      // M3 On Surface
  textSub: '#49454F',   // M3 On Surface Variant
  textMuted: '#79747E', // M3 Outline
  border: '#CAC4D0',    // M3 Outline Variant
  divider: '#EAE4F0',   // M3 Surface Container
  primary: '#6B519A',   // M3 Primary
  primaryBg: '#EEDCFF', // M3 Primary Container
  inputBg: '#FFFBFF',
}

export const androidDark: Theme = {
  mode: 'dark',
  bg: '#1C1B1F',        // M3 Surface
  card: '#252329',      // M3 Surface Container Low
  text: '#E6E1E5',      // M3 On Surface
  textSub: '#CAC4D0',   // M3 On Surface Variant
  textMuted: '#938F99', // M3 Outline
  border: '#49454F',    // M3 Outline Variant
  divider: '#2B2930',   // M3 Surface Container
  primary: '#D0BCFF',   // M3 Primary
  primaryBg: '#4A3670', // M3 Primary Container
  inputBg: '#2B2930',
}

