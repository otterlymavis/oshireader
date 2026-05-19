import { useEffect } from 'react'
import { Pressable, Text, View } from 'react-native'
import { NavigationContainer } from '@react-navigation/native'
import { createBottomTabNavigator, type BottomTabBarProps } from '@react-navigation/bottom-tabs'
import { createNativeStackNavigator } from '@react-navigation/native-stack'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context'
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
  Feed:     ['home',     'home-outline'    ],
  Saved:    ['bookmark', 'bookmark-outline'],
  Oshi:     ['star',     'star-outline'    ],
  Search:   ['search',   'search-outline'  ],
  Settings: ['settings', 'settings-outline'],
}

// M3 Navigation Bar — pill indicator behind active icon
function MaterialNavBar({ state, descriptors, navigation }: BottomTabBarProps) {
  const { theme } = useTheme()
  const { t } = useLang()
  const insets = useSafeAreaInsets()

  const TAB_LABELS: Record<string, string> = {
    Feed: t('tabFeed'),
    Saved: 'Saved',
    Oshi: 'My Oshi',
    Search: 'Search',
    Settings: 'Settings',
  }

  return (
    <View
      style={{
        flexDirection: 'row',
        backgroundColor: theme.card,
        paddingBottom: Math.max(insets.bottom, 8),
        paddingTop: 8,
        elevation: 8,
        shadowColor: '#000',
        shadowOffset: { width: 0, height: -1 },
        shadowOpacity: 0.08,
        shadowRadius: 4,
      }}
    >
      {state.routes.map((route, index) => {
        const focused = state.index === index
        const [activeIcon, inactiveIcon] = TAB_ICONS[route.name] ?? ['ellipse', 'ellipse-outline']

        const onPress = () => {
          const event = navigation.emit({ type: 'tabPress', target: route.key, canPreventDefault: true })
          if (!focused && !event.defaultPrevented) navigation.navigate(route.name)
        }

        return (
          <Pressable
            key={route.key}
            onPress={onPress}
            android_ripple={{ color: theme.primary + '28', borderless: true, radius: 48 }}
            accessibilityRole="button"
            accessibilityLabel={descriptors[route.key].options.tabBarAccessibilityLabel}
            accessibilityState={{ selected: focused }}
            style={{ flex: 1, alignItems: 'center', paddingVertical: 4 }}
          >
            {/* M3 pill indicator behind icon */}
            <View
              style={{
                width: 64,
                height: 32,
                borderRadius: 16,
                backgroundColor: focused ? theme.primaryBg : 'transparent',
                alignItems: 'center',
                justifyContent: 'center',
                marginBottom: 4,
              }}
            >
              <Ionicons
                name={focused ? activeIcon : inactiveIcon}
                size={22}
                color={focused ? theme.primary : theme.textMuted}
              />
            </View>
            <Text
              style={{
                fontSize: 12,
                fontWeight: focused ? '600' : '400',
                color: focused ? theme.primary : theme.textMuted,
                letterSpacing: 0.1,
              }}
              numberOfLines={1}
            >
              {TAB_LABELS[route.name] ?? route.name}
            </Text>
          </Pressable>
        )
      })}
    </View>
  )
}

function MainTabs() {
  const { theme } = useTheme()
  const { t } = useLang()
  return (
    <Tab.Navigator
      tabBar={(props) => <MaterialNavBar {...props} />}
      screenOptions={{
        headerStyle: { backgroundColor: theme.card },
        headerShadowVisible: false,
        headerTintColor: theme.primary,
        // M3 top app bar: title left-aligned (Android default in React Navigation)
        headerTitleStyle: { fontWeight: '700' as const, fontSize: 22 },
        headerTitleAlign: 'left',
      }}
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
      <RootStack.Navigator
        screenOptions={{
          headerShown: false,
          animation: 'slide_from_right',
        }}
      >
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
