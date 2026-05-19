import { useEffect } from 'react'
import { NavigationContainer } from '@react-navigation/native'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { createNativeStackNavigator } from '@react-navigation/native-stack'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import { StatusBar } from 'expo-status-bar'
import FeedScreen from './src/screens/FeedScreen'
import SearchScreen from './src/screens/SearchScreen'
import SavedScreen from './src/screens/SavedScreen'
import SettingsScreen from './src/screens/SettingsScreen'
import OshiScreen from './src/screens/OshiScreen'
import ReaderScreen from './src/screens/ReaderScreen'
import AvatarPickerScreen from './src/screens/AvatarPickerScreen'
import AvatarEditorScreen from './src/screens/AvatarEditorScreen'
import { setupBackgroundFetch } from './src/notifications'
import { ThemeProvider, useTheme } from './src/ThemeContext'
import { LangProvider, useLang } from './src/LangContext'

const queryClient = new QueryClient()
const Tab = createBottomTabNavigator()
const RootStack = createNativeStackNavigator()

type IoniconsName = React.ComponentProps<typeof Ionicons>['name']
const TAB_ICONS: Record<string, [IoniconsName, IoniconsName]> = {
  Feed:        ['home',      'home-outline'     ],
  Saved:       ['bookmark',  'bookmark-outline' ],
  Oshi:        ['star',      'star-outline'     ],
  Search:      ['search',    'search-outline'   ],
  Settings:    ['settings',  'settings-outline' ],
}

function MainTabs() {
  const { theme } = useTheme()
  const { t } = useLang()
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        tabBarIcon: ({ focused, color, size }) => {
          const [active, inactive] = TAB_ICONS[route.name] ?? ['ellipse', 'ellipse-outline']
          return <Ionicons name={focused ? active : inactive} size={size} color={color} />
        },
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.textMuted,
        tabBarStyle: { backgroundColor: theme.card, borderTopColor: theme.divider },
        headerStyle: { backgroundColor: theme.card },
        headerShadowVisible: false,
        headerTintColor: theme.primary,
        headerTitleStyle: { fontWeight: '700' as const },
      })}
    >
      <Tab.Screen name="Feed"     component={FeedScreen}     options={{ title: t('appTitle'), tabBarLabel: t('tabFeed') }} />
      <Tab.Screen name="Saved"    component={SavedScreen}    options={{ title: 'Saved', tabBarLabel: 'Saved' }} />
      <Tab.Screen name="Oshi"     component={OshiScreen}     options={{ title: 'My Oshi', tabBarLabel: 'My Oshi' }} />
      <Tab.Screen name="Search"   component={SearchScreen}   options={{ title: 'Search', tabBarLabel: 'Search' }} />
      <Tab.Screen name="Settings" component={SettingsScreen} options={{ title: 'Settings', tabBarLabel: 'Settings' }} />
    </Tab.Navigator>
  )
}

function AppContent() {
  useEffect(() => { setupBackgroundFetch() }, [])
  const { theme, mode } = useTheme()

  return (
    <NavigationContainer>
      <RootStack.Navigator screenOptions={{ headerShown: false }}>
        <RootStack.Screen name="Main" component={MainTabs} />
        <RootStack.Screen name="Reader" component={ReaderScreen} />
        <RootStack.Screen name="AvatarPicker" component={AvatarPickerScreen} />
        <RootStack.Screen name="AvatarEditor" component={AvatarEditorScreen} />
      </RootStack.Navigator>
      <StatusBar style={mode === 'dark' ? 'light' : 'dark'} />
    </NavigationContainer>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <SafeAreaProvider>
        <ThemeProvider>
          <LangProvider>
            <AppContent />
          </LangProvider>
        </ThemeProvider>
      </SafeAreaProvider>
    </QueryClientProvider>
  )
}
