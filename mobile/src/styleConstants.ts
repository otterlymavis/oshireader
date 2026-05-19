export type ColorStyle = 'colourful' | 'standard'

// "Standard" style — single bright indigo accent, uniform badge surfaces.
// Applied on top of any theme (light/dark/android).
export const STANDARD_STYLE = {
  light: {
    badgeBg: '#F0F4FF',   // very light indigo tint — bright and airy
    badgeFg: '#3730A3',   // indigo-700 — clear, readable
    accent:  '#5B48E8',   // vivid indigo-purple — modern, energetic
  },
  dark: {
    badgeBg: '#1E1A3A',   // deep indigo-tinted surface
    badgeFg: '#A8B5FD',   // light blue-purple — visible on dark
    accent:  '#7C72F0',   // bright indigo — vibrant on dark backgrounds
  },
} as const
