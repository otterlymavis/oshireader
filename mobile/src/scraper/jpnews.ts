import type { FeedItem } from '../localDb'
import {
  fetchDDGLite,
  fetchBingDesktop,
  fetchGoogleSearch,
  fetchHtml,
  fetchXml,
  fetchJinaReader,
  fetchBingNewsRSS,
  extractBingWebLinks,
  parseRssItems,
  decodeHtml,
  textMatchesKeyword,
  now,
} from './utils'

interface SiteConfig {
  platform: string
  domain: string
  urlTest: (url: string) => boolean
  idOf: (url: string) => string
  directSearch?: (keyword: string) => Promise<FeedItem[]>
  strictTitleMatch?: boolean
  preferDirect?: boolean
}

const MDPR_PATH_RE = /\/(?:news|article|interview|drama|cinema|music|entertainment|fashion|beauty|lifestyle)\/\d+(?:\/[^"?#]*)?/i
const RECENT_NEWS_DAYS = 45

function isRecentNewsDate(pubDate: string | null | undefined): boolean {
  if (!pubDate) return true
  const d = new Date(pubDate)
  if (isNaN(d.getTime())) return true
  return d.getTime() >= Date.now() - RECENT_NEWS_DAYS * 24 * 60 * 60 * 1000
}

async function scrapeSite(cfg: SiteConfig, keyword: string): Promise<FeedItem[]> {
  const toItem = (url: string, title: string | null, pubDate?: string, contentText?: string | null): FeedItem => {
    const cleanTitle = cleanArticleTitle(title, cfg.platform)
    const cleanContent = cleanArticleTitle(contentText, cfg.platform)
    return {
      id: `${cfg.platform}:${cfg.idOf(url)}`,
      platform: cfg.platform, url,
      title: cleanTitle || cleanContent || null,
      content_text: contentText || null, author: null, thumbnail_url: null,
      media_type: 'article',
      published_at: pubDate ?? now(),
      watch_term_keyword: keyword, fetched_at: now(),
    }
  }

  if (cfg.preferDirect && cfg.directSearch) {
    try {
      const items = await cfg.directSearch(keyword)
      if (items.length > 0) return items
    } catch (e) {
      console.log(`[${cfg.platform}:direct] failed:`, e)
    }
  }

  // 1. Bing News RSS â€” structured feed, no HTML parsing needed
  try {
    const rssItems = await fetchBingNewsRSS(`${keyword} site:${cfg.domain} when:${RECENT_NEWS_DAYS}d`)
    const matched = rssItems.filter(i =>
      cfg.urlTest(i.url) &&
      isRecentNewsDate(i.pubDate) &&
      textMatchesKeyword(`${i.title} ${i.description}`, keyword),
    )
    console.log(`[${cfg.platform}:rss] rss=${rssItems.length} relevant=${matched.length}`)
    if (matched.length > 0) {
      const items: FeedItem[] = []
      for (const i of matched.slice(0, 12)) {
        const pageTitle = await fetchArticleTitle(i.url, cfg.platform)
        const title = pageTitle && textMatchesKeyword(pageTitle, keyword) ? pageTitle : i.title
        items.push(toItem(i.url, title, i.pubDate, i.description))
      }
      if (items.length > 0) {
        return items
      }
    }
  } catch (e) {
    console.log(`[${cfg.platform}:rss] failed:`, e)
  }

  // 2. DDG Lite / Bing HTML site: search
  try {
    let html: string
    try {
      html = await fetchDDGLite(`site:${cfg.domain} ${keyword}`)
    } catch {
      html = await fetchBingDesktop(`site:${cfg.domain} ${keyword}`)
    }
    const links = extractBingWebLinks(html, cfg.urlTest)
    console.log(`[${cfg.platform}:ddg] links=${links.length}`)
    const relevant = links.filter(({ title }) => textMatchesKeyword(title, keyword))
    if (relevant.length > 0) {
      const items = await hydrateSearchTitles(relevant, cfg, keyword, toItem)
      if (items.length > 0) return items
    }
  } catch (e) {
    console.log(`[${cfg.platform}:ddg] failed:`, e)
  }

  // 3. Google web search fallback. This helps on networks where DDG/Bing HTML
  // comes back empty or mobile-shaped.
  try {
    const html = await fetchGoogleSearch(`site:${cfg.domain} ${keyword}`)
    const links = extractBingWebLinks(html, cfg.urlTest)
    const relevant = links.filter(({ title }) => textMatchesKeyword(title, keyword))
    console.log(`[${cfg.platform}:google] links=${links.length} relevant=${relevant.length}`)
    if (relevant.length > 0) {
      const items = await hydrateSearchTitles(relevant, cfg, keyword, toItem)
      if (items.length > 0) return items
    }
  } catch (e) {
    console.log(`[${cfg.platform}:google] failed:`, e)
  }

  // 4. Direct site search page
  if (!cfg.preferDirect && cfg.directSearch) {
    try {
      const items = await cfg.directSearch(keyword)
      if (items.length > 0) return items
    } catch (e) {
      console.log(`[${cfg.platform}:direct] failed:`, e)
    }
  }

  return googleNewsSiteFallback(cfg, keyword)
}

async function hydrateSearchTitles(
  links: Array<{ url: string; title: string }>,
  cfg: SiteConfig,
  keyword: string,
  toItem: (url: string, title: string | null, pubDate?: string) => FeedItem,
): Promise<FeedItem[]> {
  const items: FeedItem[] = []
  for (const { url, title } of links.slice(0, 12)) {
    const pageTitle = await fetchArticleTitle(url, cfg.platform)
    const finalTitle = pageTitle || title
    if (cfg.platform === 'yahoonews' && !cleanArticleTitle(finalTitle, cfg.platform)) continue
    if (cfg.strictTitleMatch && !textMatchesKeyword(finalTitle, keyword)) continue
    items.push(toItem(url, finalTitle))
  }
  return items
}

async function fetchArticleTitle(url: string, platform: string): Promise<string | null> {
  if (!['yahoonews', 'mdpr', 'linenews'].includes(platform)) return null
  try {
    const html = await fetchHtml(url)
    const raw =
      html.match(/<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i)?.[1] ??
      html.match(/<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:title["']/i)?.[1] ??
      html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] ??
      ''
    return cleanArticleTitle(raw, platform)
  } catch {
    try {
      const text = await fetchJinaReader(url)
      const raw = text.match(/^Title:\s*(.+)$/m)?.[1] ?? text.match(/^#\s+(.+)$/m)?.[1] ?? ''
      return cleanArticleTitle(raw, platform)
    } catch {
      return null
    }
  }
}

function cleanArticleTitle(title: string | null | undefined, platform: string): string {
  let text = decodeHtml(title ?? '')
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return ''
  if (isUrlLikeTitle(text)) return ''
  if (platform === 'yahoonews') {
    text = text
      .replace(/\s*-\s*Yahoo!ãƒ‹ãƒ¥ãƒ¼ã‚¹\s*$/i, '')
      .replace(/\s*\([^)]*ãƒ‹ãƒ¥ãƒ¼ã‚¹\)\s*-\s*Yahoo!ãƒ‹ãƒ¥ãƒ¼ã‚¹\s*$/i, '')
      .replace(/\s*-\s*Yahoo!\u30cb\u30e5\u30fc\u30b9\s*$/i, '')
      .replace(/\s*\([^)]*\u30cb\u30e5\u30fc\u30b9\)\s*-\s*Yahoo!\u30cb\u30e5\u30fc\u30b9\s*$/i, '')
  }
  if (platform === 'mdpr') {
    text = text
      .replace(/\s*-\s*ãƒ¢ãƒ‡ãƒ«ãƒ—ãƒ¬ã‚¹\s*$/, '')
      .replace(/\s*\|\s*ãƒ¢ãƒ‡ãƒ«ãƒ—ãƒ¬ã‚¹\s*$/, '')
      .replace(/\s*ãƒ¢ãƒ‡ãƒ«ãƒ—ãƒ¬ã‚¹\s*$/, '')
  }
  if (platform === 'linenews') {
    text = text
      .replace(/\s*[-|]\s*LINE NEWS\s*$/i, '')
      .replace(/\s*[-|]\s*LINEãƒ‹ãƒ¥ãƒ¼ã‚¹\s*$/i, '')
      .replace(/\s*[-|]\s*LINE\u30cb\u30e5\u30fc\u30b9\s*$/i, '')
  }
  return text.trim()
}

function isUrlLikeTitle(text: string): boolean {
  return /^https?:\/\//i.test(text) || /(?:news\.yahoo\.co\.jp|news\.google\.com)\/\S+/i.test(text)
}

// â”€â”€ Direct search page scrapers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function extractDirectLinks(
  html: string,
  re: RegExp,
  platform: string,
  idOf: (url: string) => string,
  keyword: string,
  limit = 20,
): FeedItem[] {
  const results: FeedItem[] = []
  const seen = new Set<string>()
  let m: RegExpExecArray | null
  while (results.length < limit && (m = re.exec(html)) !== null) {
    const url = m[1]
    const id = idOf(url)
    if (!id || seen.has(id)) continue
    seen.add(id)
    const nearby = html.slice(Math.max(0, m.index - 20), m.index + 500)
    const titleM = nearby.match(/alt="([^"]{4,})"/)
      ?? nearby.match(/title="([^"]{4,})"/)
      ?? nearby.match(/>([^<]{5,80})<\/(?:h[1-6]|strong|span|div|p|a)>/)
    const title = decodeHtml(titleM?.[1]?.replace(/\s+/g, ' ').trim() ?? '')
    if (!textMatchesKeyword(`${title} ${nearby}`, keyword)) continue
    results.push({
      id: `${platform}:${id}`, platform, url,
      title: title || null,
      content_text: null, author: null, thumbnail_url: null,
      media_type: 'article', published_at: now(),
      watch_term_keyword: keyword, fetched_at: now(),
    })
  }
  return results
}

async function yahooNewsDirect(keyword: string): Promise<FeedItem[]> {
  let directResults: FeedItem[] = []
  try {
    const html = await fetchHtml(
      `https://news.yahoo.co.jp/search/?p=${encodeURIComponent(keyword)}&ei=UTF-8`,
    )
    const re = /href="(https?:\/\/news\.yahoo\.co\.jp\/articles\/[A-Za-z0-9]+)"/gi
    const idOf = (url: string) => url.match(/\/articles\/([A-Za-z0-9]+)/)?.[1] ?? encodeURIComponent(url).slice(-20)
    const results: FeedItem[] = []
    const seen = new Set<string>()
    let m: RegExpExecArray | null
    while (results.length < 20 && (m = re.exec(html)) !== null) {
      const url = m[1]
      const id = idOf(url)
      if (!id || seen.has(id)) continue
      seen.add(id)
      const nearby = html.slice(Math.max(0, m.index - 120), m.index + 700)
      const titleM = nearby.match(/alt="([^"]{4,})"/)
        ?? nearby.match(/title="([^"]{4,})"/)
        ?? nearby.match(/aria-label="([^"]{4,})"/)
        ?? nearby.match(/>([^<]{5,120})<\/(?:h[1-6]|strong|span|div|p|a)>/)
      const title = cleanArticleTitle(
        await fetchArticleTitle(url, 'yahoonews') || titleM?.[1]?.replace(/\s+/g, ' ').trim(),
        'yahoonews',
      )
      if (!title || !textMatchesKeyword(`${title} ${nearby}`, keyword)) continue
      results.push({
        id: `yahoonews:${id}`,
        platform: 'yahoonews',
        url,
        title,
        content_text: null,
        author: null,
        thumbnail_url: null,
        media_type: 'article',
        published_at: now(),
        watch_term_keyword: keyword,
        fetched_at: now(),
      })
    }
    directResults = results
    console.log(`[yahoonews:direct] results=${directResults.length}`)
  } catch (e) {
    console.log('[yahoonews:direct] failed:', e)
  }
  if (directResults.length > 0) return directResults
  return yahooNewsGoogleRss(keyword)
}

async function yahooNewsGoogleRss(keyword: string): Promise<FeedItem[]> {
  const queries = [
    `${keyword} site:news.yahoo.co.jp/articles`,
    `${keyword} Yahoo!ニュース`,
    `${keyword} Yahooニュース`,
  ]
  const results: FeedItem[] = []
  const seen = new Set<string>()
  let fetched = 0
  for (const query of queries) {
    try {
      const xml = await fetchXml(
      `https://news.google.com/rss/search?q=${encodeURIComponent(`${query} when:${RECENT_NEWS_DAYS}d`)}&hl=ja&gl=JP&ceid=JP:ja`,
      )
      const items = parseRssItems(xml)
      fetched += items.length
      for (const item of items) {
        if (results.length >= 20) break
        const rawTitle = decodeHtml(item.title)
        const rawDescription = decodeHtml(item.description)
        if (!isRecentNewsDate(item.pubDate)) continue
        const mentionsYahoo = /Yahoo!?ニュース|Yahoo!\s*News|news\.yahoo\.co\.jp/i.test(`${rawTitle} ${rawDescription} ${item.link}`)
        if (!mentionsYahoo && query.includes('site:') && !textMatchesKeyword(rawTitle, keyword)) continue
        const title = cleanArticleTitle(rawTitle, 'yahoonews')
        if (!title || !textMatchesKeyword(`${title} ${rawDescription}`, keyword)) continue
        const rawLink = decodeHtml(item.link).replace(/&amp;/g, '&')
        const id = item.guid || rawLink
        const key = encodeURIComponent(id).slice(-36)
        if (seen.has(key)) continue
        seen.add(key)
        const d = new Date(item.pubDate)
        results.push({
          id: `yahoonews:gnews:${key}`,
          platform: 'yahoonews',
          url: rawLink,
          title,
          content_text: rawDescription || null,
          author: null,
          thumbnail_url: null,
          media_type: 'article',
          published_at: !isNaN(d.getTime()) ? d.toISOString() : now(),
          watch_term_keyword: keyword,
          fetched_at: now(),
        })
      }
      if (results.length > 0) break
    } catch (e) {
      console.log(`[yahoonews:gnews] failed query="${query}":`, e)
    }
  }
  console.log(`[yahoonews:gnews] rss=${fetched} results=${results.length}`)
  return results
}

async function googleNewsSiteFallback(cfg: SiteConfig, keyword: string): Promise<FeedItem[]> {
  try {
    const xml = await fetchXml(
      `https://news.google.com/rss/search?q=${encodeURIComponent(`${keyword} site:${cfg.domain} when:${RECENT_NEWS_DAYS}d`)}&hl=ja&gl=JP&ceid=JP:ja`,
    )
    const items = parseRssItems(xml)
    const results: FeedItem[] = []
    const seen = new Set<string>()
    for (const item of items) {
      if (results.length >= 20) break
      const rawTitle = decodeHtml(item.title)
      const rawDescription = decodeHtml(item.description)
      if (!isRecentNewsDate(item.pubDate)) continue
      const title = cleanArticleTitle(rawTitle, cfg.platform)
      if (!title || !textMatchesKeyword(`${title} ${rawDescription}`, keyword)) continue
      const rawLink = decodeHtml(item.link).replace(/&amp;/g, '&')
      const key = encodeURIComponent(item.guid || rawLink).slice(-36)
      if (seen.has(key)) continue
      seen.add(key)
      const d = new Date(item.pubDate)
      results.push({
        id: `${cfg.platform}:gnews:${key}`,
        platform: cfg.platform,
        url: rawLink,
        title,
        content_text: rawDescription || null,
        author: null,
        thumbnail_url: null,
        media_type: 'article',
        published_at: !isNaN(d.getTime()) ? d.toISOString() : now(),
        watch_term_keyword: keyword,
        fetched_at: now(),
      })
    }
    console.log(`[${cfg.platform}:gnews] rss=${items.length} results=${results.length}`)
    return results
  } catch (e) {
    console.log(`[${cfg.platform}:gnews] failed:`, e)
    return []
  }
}

async function mdprDirectSearch(keyword: string): Promise<FeedItem[]> {
  const idOf = (url: string) => url.match(/\/(?:news|article|interview|drama|cinema|music|entertainment|fashion|beauty|lifestyle)\/(\d+)/)?.[1] ?? encodeURIComponent(url).slice(-20)
  for (const searchUrl of [
    `https://mdpr.jp/search?keyword=${encodeURIComponent(keyword)}`,
    `https://mdpr.jp/search/?q=${encodeURIComponent(keyword)}`,
  ]) {
    try {
      const html = await fetchHtml(searchUrl)
      const results: FeedItem[] = []
      const seen = new Set<string>()
      const itemRe = /<li class="p-articleListItem">([\s\S]*?)<\/li>/gi
      let itemMatch: RegExpExecArray | null
      while (results.length < 20 && (itemMatch = itemRe.exec(html)) !== null) {
        const block = itemMatch[1]
        const href = block.match(/href="((?:https?:\/\/mdpr\.jp)?\/[^"#?]+)"/i)?.[1] ?? ''
        if (!href || !MDPR_PATH_RE.test(href)) continue
        const url = href.startsWith('http') ? href : `https://mdpr.jp${href}`
        const id = idOf(url)
        if (!id || seen.has(id)) continue
        const titleRaw =
          block.match(/class="p-articleListItem__title"[^>]*>\s*<span>([\s\S]*?)<\/span>/i)?.[1] ??
          block.match(/alt="([^"]{4,})"/i)?.[1] ??
          block.match(/title="([^"]{4,})"/i)?.[1] ??
          ''
        const title = cleanArticleTitle(titleRaw.replace(/\s+/g, ' ').trim(), 'mdpr')
        if (!title || !textMatchesKeyword(`${title} ${block}`, keyword)) continue
        seen.add(id)
        results.push({
          id: `mdpr:${id}`, platform: 'mdpr', url,
          title: title || null,
          content_text: null, author: null, thumbnail_url: null,
          media_type: 'article', published_at: now(),
          watch_term_keyword: keyword, fetched_at: now(),
        })
      }
      console.log(`[mdpr:direct] url=${searchUrl} results=${results.length}`)
      if (results.length > 0) return results
    } catch (e) {
      console.log(`[mdpr:direct] failed ${searchUrl}:`, e)
    }
  }
  return []
}

const CONFIGS: SiteConfig[] = [
  {
    platform: 'yahoonews',
    domain: 'news.yahoo.co.jp',
    urlTest: url => /news\.yahoo\.co\.jp\/articles\/[A-Za-z0-9]/.test(url),
    idOf: url => url.match(/\/articles\/([A-Za-z0-9]+)/)?.[1] ?? encodeURIComponent(url).slice(-20),
    directSearch: yahooNewsDirect,
    preferDirect: true,
  },
  {
    platform: 'mdpr',
    domain: 'mdpr.jp',
    urlTest: url => /mdpr\.jp\/(?:news|article|interview|drama|cinema|music|entertainment|fashion|beauty|lifestyle)\/\d/.test(url),
    idOf: url => url.match(/\/(?:news|article|interview|drama|cinema|music|entertainment|fashion|beauty|lifestyle)\/(\d+)/)?.[1] ?? encodeURIComponent(url).slice(-20),
    directSearch: mdprDirectSearch,
  },
  {
    platform: 'linenews',
    domain: 'news.line.me',
    urlTest: url => /news\.line\.me\/(?:detail|vision|graffiti\/detail)\//.test(url),
    idOf: url => url.match(/news\.line\.me\/(?:detail|vision|graffiti\/detail)\/([^?#]+)/)?.[1]?.replace(/\W+/g, ':') ?? encodeURIComponent(url).slice(-36),
    strictTitleMatch: true,
  },
]

function makeScraper(cfg: SiteConfig) {
  return async (keyword: string, mode: string): Promise<FeedItem[]> => {
    try {
      const items = await scrapeSite(cfg, keyword)
      if (items.length > 0) return items
    } catch (e) {
      console.log(`[${cfg.platform}:bing] failed:`, e)
    }
    return []
  }
}

export const scrapeYahooNews = makeScraper(CONFIGS[0])
export const scrapeMdpr      = makeScraper(CONFIGS[1])
export const scrapeLineNews  = makeScraper(CONFIGS[2])
