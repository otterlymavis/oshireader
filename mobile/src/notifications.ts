import * as Notifications from 'expo-notifications'
import * as BackgroundTask from 'expo-background-task'
import * as TaskManager from 'expo-task-manager'
import { getTerms, mergeItemsDetailed, updateTerm } from './localDb'
import { scrapeAll } from './scraper'

const LEGACY_TASK = 'otterpia-bg-fetch'
const TASK = 'otterpia-bg-task'
const SOURCE_LABELS: Record<string, string> = {
  youtube: 'YouTube',
  niconico: 'NicoNico',
  tver: 'TVer',
  note: 'Note',
  girlschannel: 'GirlsChannel',
  '5ch': '5ch',
  togetter: 'Togetter',
  news: 'News',
  yahoonews: 'Yahoo News',
  mdpr: 'Modelpress',
  custom: 'Custom URLs',
}

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
      const { added, byPlatform } = await mergeItemsDetailed(items)
      if (added > 0) {
        const sources = formatSources(byPlatform)
        await Notifications.scheduleNotificationAsync({
          content: {
            title: `New results for "${term.keyword}"`,
            body: `${added} new result${added === 1 ? '' : 's'} from ${sources}`,
          },
          trigger: null,
        })
      }
    }
    return BackgroundTask.BackgroundTaskResult.Success
  } catch {
    return BackgroundTask.BackgroundTaskResult.Failed
  }
})

export async function requestNotificationPermission(): Promise<boolean> {
  const { status } = await Notifications.requestPermissionsAsync()
  return status === 'granted'
}

export async function setTermNotification(id: string, notifyOnNew: boolean): Promise<boolean> {
  if (notifyOnNew) {
    const granted = await requestNotificationPermission()
    if (!granted) return false
  }

  await updateTerm(id, { notify_on_new: notifyOnNew })
  await syncBackgroundTaskRegistration()
  return true
}

export async function syncBackgroundTaskRegistration(): Promise<void> {
  try {
    if (await TaskManager.isTaskRegisteredAsync(LEGACY_TASK)) {
      await TaskManager.unregisterTaskAsync(LEGACY_TASK)
    }

    const status = await BackgroundTask.getStatusAsync()
    if (status === BackgroundTask.BackgroundTaskStatus.Restricted) return

    const terms = await getTerms()
    const shouldRegister = terms.some(t => t.is_active && t.notify_on_new)
    const isRegistered = await TaskManager.isTaskRegisteredAsync(TASK)

    if (shouldRegister && !isRegistered) {
      await BackgroundTask.registerTaskAsync(TASK, {
        minimumInterval: 15,
      })
    } else if (!shouldRegister && isRegistered) {
      await BackgroundTask.unregisterTaskAsync(TASK)
    }
  } catch { /* unavailable in Expo Go and simulators */ }
}

export const setupBackgroundTask = syncBackgroundTaskRegistration

function formatSources(byPlatform: Record<string, number>): string {
  const sources = Object.entries(byPlatform)
    .filter(([, count]) => count > 0)
    .sort(([, a], [, b]) => b - a)
    .map(([platform]) => SOURCE_LABELS[platform] ?? platform)

  if (sources.length === 0) return 'any source'
  if (sources.length <= 3) return sources.join(', ')
  return `${sources.slice(0, 3).join(', ')} and ${sources.length - 3} more`
}
