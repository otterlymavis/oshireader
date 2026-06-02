import { useWindowDimensions } from 'react-native'

export function useResponsive() {
  const { width, height } = useWindowDimensions()
  const isTablet = width >= 768
  return { width, height, isTablet }
}
