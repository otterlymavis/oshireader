import * as Notifications from 'expo-notifications'
import * as BackgroundFetch from 'expo-background-fetch'
import * as TaskManager from 'expo-task-manager'
import Constants from 'expo-constants'
import { getTerms, mergeItems } from './localDb'
import { scrapeAll } from './scraper'
import { registerPushToken, unregisterPushToken } from './api'

const TASK = 'otterpia-bg-fetch'

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
})

// Must be defined at module level — called by the OS in the background
TaskManager.defineTask(TASK, async () => {
  try {
    const terms = await getTerms()
    const watching = terms.filter(t => t.is_active && t.notify_on_new)

    for (const term of watching) {
      const items = await scrapeAll(term.keyword, term.collection_mode)
      const added = await mergeItems(items)
      if (added > 0) {
        await Notifications.scheduleNotificationAsync({
          content: {
            title: `New results for "${term.keyword}"`,
            body: `${added} new article${added === 1 ? '' : 's'} found`,
          },
          trigger: null,
        })
      }
    }
    return BackgroundFetch.BackgroundFetchResult.NewData
  } catch {
    return BackgroundFetch.BackgroundFetchResult.Failed
  }
})

export async function requestNotificationPermission(): Promise<boolean> {
  const { status } = await Notifications.requestPermissionsAsync()
  return status === 'granted'
}

/**
 * Get this device's Expo push token.
 * Returns null if unavailable (Expo Go dev client, simulator, etc.).
 */
export async function getDevicePushToken(): Promise<string | null> {
  try {
    const projectId =
      Constants.expoConfig?.extra?.eas?.projectId as string | undefined
    const result = await Notifications.getExpoPushTokenAsync(
      projectId ? { projectId } : undefined,
    )
    return result.data
  } catch {
    return null
  }
}

/**
 * Register this device with the backend so it receives push notifications.
 * Safe to call multiple times — the backend deduplicates by token.
 */
export async function registerDevicePushToken(): Promise<void> {
  try {
    const { status } = await Notifications.getPermissionsAsync()
    if (status !== 'granted') return
    const token = await getDevicePushToken()
    if (token) await registerPushToken(token)
  } catch {
    // Non-fatal — push is best-effort
  }
}

/**
 * Unregister this device from backend push notifications.
 */
export async function unregisterDevicePushToken(): Promise<void> {
  try {
    const token = await getDevicePushToken()
    if (token) await unregisterPushToken(token)
  } catch {}
}

export async function setupBackgroundFetch(): Promise<void> {
  try {
    const status = await BackgroundFetch.getStatusAsync()
    if (
      status === BackgroundFetch.BackgroundFetchStatus.Restricted ||
      status === BackgroundFetch.BackgroundFetchStatus.Denied
    ) return
    const isRegistered = await TaskManager.isTaskRegisteredAsync(TASK)
    if (!isRegistered) {
      await BackgroundFetch.registerTaskAsync(TASK, {
        minimumInterval: 60 * 15,
        stopOnTerminate: false,
        startOnBoot: true,
      })
    }
  } catch { /* unavailable in Expo Go and simulators */ }
}
