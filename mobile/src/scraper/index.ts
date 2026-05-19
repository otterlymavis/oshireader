import AsyncStorage from '@react-native-async-storage/async-storage'
import type { FeedItem } from '../localDb'
import { scrapeRSS } from './rss'
import { scrapeFiveCh } from './fivech'
import { scrapeGirlsChannel } from './girlschannel'
import { scrapeTogetter } from './togetter'
import { scrapeTVer } from './tver'
import { scrapeYouTube } from './youtube'
import { scrapeNicoNico } from './niconico'
import { scrapeNote } from './note'
import { scrapeYahooNews, scrapeMdpr } from './jpnews'

export interface ScraperLog {
  name: string
  count: number
  error: string | null
  ms: number
}

export interface ScrapeRun {
  keyword: string
  ran_at: string
  logs: ScraperLog[]
}

const NAMED: Array<[string, (k: string, m: string) => Promise<FeedItem[]>]> = [
  ['RSS',          scrapeRSS],
  ['5ch',          scrapeFiveCh],
  ['GirlsChannel', scrapeGirlsChannel],
  ['Togetter',     scrapeTogetter],
  ['TVer',         scrapeTVer],
  ['YouTube',      scrapeYouTube],
  ['NicoNico',     scrapeNicoNico],
  ['Note',         scrapeNote],
  ['YahooNews',    scrapeYahooNews],
  ['Mdpr',         scrapeMdpr],
]

export async function scrapeAll(
  keyword: string,
  mode: 'all_info' | 'media_only',
): Promise<FeedItem[]> {
  const logs: ScraperLog[] = []
  const items: FeedItem[] = []

  await Promise.all(
    NAMED.map(async ([name, fn]) => {
      const t0 = Date.now()
      try {
        const result = await fn(keyword, mode)
        logs.push({ name, count: result.length, error: null, ms: Date.now() - t0 })
        items.push(...result)
      } catch (e) {
        logs.push({ name, count: 0, error: String(e), ms: Date.now() - t0 })
      }
    }),
  )

  const run: ScrapeRun = { keyword, ran_at: new Date().toISOString(), logs }
  await AsyncStorage.setItem('@otterpia:last_scrape', JSON.stringify(run)).catch(() => {})

  return items
}
