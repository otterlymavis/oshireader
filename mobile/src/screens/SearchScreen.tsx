import { useEffect, useMemo, useState } from 'react'
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useQuery } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getTerms } from '../localDb'
import { useTheme } from '../ThemeContext'
import type { Theme } from '../theme'
import { useResponsive } from '../hooks/useResponsive'
import ReaderScreen from './ReaderScreen'

type IoniconsName = React.ComponentProps<typeof Ionicons>['name']

interface SearchLink {
  id: string
  group: string
  label: string
  domain: string
  platform: string
  makeUrl: (keyword: string) => string
}

const SEARCH_LINKS: SearchLink[] = [
  {
    id: 'yahoo-news',
    group: 'News',
    label: 'Yahoo! News Japan',
    domain: 'news.yahoo.co.jp',
    platform: 'yahoonews',
    makeUrl: q => `https://news.yahoo.co.jp/search?p=${encodeURIComponent(q)}&ei=utf-8`,
  },
  {
    id: 'google-news',
    group: 'News',
    label: 'Google News Japan',
    domain: 'news.google.com',
    platform: 'news',
    makeUrl: q => `https://news.google.com/search?q=${encodeURIComponent(q)}&hl=ja&gl=JP&ceid=JP%3Aja`,
  },
  {
    id: 'bing-news',
    group: 'News',
    label: 'Bing News Japan',
    domain: 'bing.com/news',
    platform: 'news',
    makeUrl: q => `https://www.bing.com/news/search?q=${encodeURIComponent(q)}&cc=JP&setlang=ja`,
  },
  {
    id: 'nhk',
    group: 'News',
    label: 'NHK News',
    domain: 'www3.nhk.or.jp',
    platform: 'news',
    makeUrl: q => `https://www.google.com/search?q=${encodeURIComponent(`${q} site:www3.nhk.or.jp/news`)}`,
  },
  {
    id: 'livedoor',
    group: 'News',
    label: 'Livedoor News',
    domain: 'news.livedoor.com',
    platform: 'news',
    makeUrl: q => `https://news.livedoor.com/search/article/?keyword=${encodeURIComponent(q)}`,
  },
  {
    id: 'line-news',
    group: 'News',
    label: 'LINE NEWS',
    domain: 'news.line.me',
    platform: 'news',
    makeUrl: q => `https://news.line.me/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'msn-news',
    group: 'News',
    label: 'MSN Japan',
    domain: 'msn.com/ja-jp',
    platform: 'news',
    makeUrl: q => `https://www.msn.com/ja-jp/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'modelpress',
    group: 'Entertainment',
    label: 'Modelpress',
    domain: 'mdpr.jp',
    platform: 'mdpr',
    makeUrl: q => `https://mdpr.jp/search?keyword=${encodeURIComponent(q)}`,
  },
  {
    id: 'natalie',
    group: 'Entertainment',
    label: 'Natalie',
    domain: 'natalie.mu',
    platform: 'news',
    makeUrl: q => `https://natalie.mu/search?query=${encodeURIComponent(q)}`,
  },
  {
    id: 'oricon',
    group: 'Entertainment',
    label: 'Oricon News',
    domain: 'oricon.co.jp',
    platform: 'news',
    makeUrl: q => `https://www.oricon.co.jp/search/?qs=${encodeURIComponent(q)}&cat=all`,
  },
  {
    id: 'cinematoday',
    group: 'Entertainment',
    label: 'Cinema Today',
    domain: 'cinematoday.jp',
    platform: 'news',
    makeUrl: q => `https://www.cinematoday.jp/search?keyword=${encodeURIComponent(q)}`,
  },
  {
    id: 'realsound',
    group: 'Entertainment',
    label: 'Real Sound',
    domain: 'realsound.jp',
    platform: 'news',
    makeUrl: q => `https://realsound.jp/?s=${encodeURIComponent(q)}`,
  },
  {
    id: 'sponichi',
    group: 'Entertainment',
    label: 'Sponichi',
    domain: 'sponichi.co.jp',
    platform: 'news',
    makeUrl: q => `https://www.sponichi.co.jp/search/index.html?Keywords=${encodeURIComponent(q)}`,
  },
  {
    id: 'daily',
    group: 'Entertainment',
    label: 'Daily Sports',
    domain: 'daily.co.jp',
    platform: 'news',
    makeUrl: q => `https://www.daily.co.jp/search/?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'hochi',
    group: 'Entertainment',
    label: 'Hochi Sports',
    domain: 'hochi.news',
    platform: 'news',
    makeUrl: q => `https://hochi.news/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'bunshun',
    group: 'Magazines',
    label: '文春オンライン',
    domain: 'bunshun.jp',
    platform: 'news',
    makeUrl: q => `https://bunshun.jp/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'joseiseven',
    group: 'Magazines',
    label: '女性セブン',
    domain: 'josei7.com',
    platform: 'news',
    makeUrl: q => `https://josei7.com/?s=${encodeURIComponent(q)}`,
  },
  {
    id: 'friday',
    group: 'Magazines',
    label: 'FRIDAY Digital',
    domain: 'friday.kodansha.co.jp',
    platform: 'news',
    makeUrl: q => `https://friday.kodansha.co.jp/search/articles?query=${encodeURIComponent(q)}`,
  },
  {
    id: 'jprime',
    group: 'Magazines',
    label: '週刊女性PRIME',
    domain: 'jprime.jp',
    platform: 'news',
    makeUrl: q => `https://www.jprime.jp/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'flash',
    group: 'Magazines',
    label: 'FLASH',
    domain: 'smart-flash.jp',
    platform: 'news',
    makeUrl: q => `https://smart-flash.jp/?s=${encodeURIComponent(q)}`,
  },
  {
    id: 'josei-jishin',
    group: 'Magazines',
    label: '女性自身',
    domain: 'jisin.jp',
    platform: 'news',
    makeUrl: q => `https://jisin.jp/search/?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'cyzo',
    group: 'Magazines',
    label: 'サイゾーウーマン',
    domain: 'cyzowoman.com',
    platform: 'news',
    makeUrl: q => `https://www.cyzowoman.com/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'tver',
    group: 'Video',
    label: 'TVer',
    domain: 'tver.jp',
    platform: 'tver',
    makeUrl: q => `https://tver.jp/search/${encodeURIComponent(q)}`,
  },
  {
    id: 'youtube',
    group: 'Video',
    label: 'YouTube',
    domain: 'youtube.com',
    platform: 'youtube',
    makeUrl: q => `https://www.youtube.com/results?search_query=${encodeURIComponent(q)}`,
  },
  {
    id: 'niconico',
    group: 'Video',
    label: 'Niconico',
    domain: 'nicovideo.jp',
    platform: 'niconico',
    makeUrl: q => `https://www.nicovideo.jp/search/${encodeURIComponent(q)}`,
  },
  {
    id: 'dailymotion',
    group: 'Video',
    label: 'Dailymotion',
    domain: 'dailymotion.com',
    platform: 'custom',
    makeUrl: q => `https://www.dailymotion.com/search/${encodeURIComponent(q)}/videos`,
  },
  {
    id: 'bilibili',
    group: 'Video',
    label: 'Bilibili',
    domain: 'bilibili.com',
    platform: 'custom',
    makeUrl: q => `https://search.bilibili.com/all?keyword=${encodeURIComponent(q)}`,
  },
  {
    id: 'note',
    group: 'Writing',
    label: 'note',
    domain: 'note.com',
    platform: 'note',
    makeUrl: q => `https://note.com/search?q=${encodeURIComponent(q)}&context=note`,
  },
  {
    id: 'ameblo',
    group: 'Writing',
    label: 'Ameba Blog',
    domain: 'ameblo.jp',
    platform: 'news',
    makeUrl: q => `https://search.ameba.jp/search.html?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'x',
    group: 'Social',
    label: 'X',
    domain: 'x.com',
    platform: 'twitter',
    makeUrl: q => `https://x.com/search?q=${encodeURIComponent(q)}&src=typed_query&f=live`,
  },
  {
    id: 'threads',
    group: 'Social',
    label: 'Threads',
    domain: 'threads.net',
    platform: 'custom',
    makeUrl: q => `https://www.threads.net/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'instagram',
    group: 'Social',
    label: 'Instagram',
    domain: 'instagram.com',
    platform: 'custom',
    makeUrl: q => `https://www.instagram.com/explore/search/keyword/?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'twitterviewer',
    group: 'Social',
    label: 'Twitter Viewer',
    domain: 'twitterviewer.net',
    platform: 'twitter',
    makeUrl: q => `https://twitterviewer.net/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'tiktok',
    group: 'Social',
    label: 'TikTok',
    domain: 'tiktok.com',
    platform: 'twitter',
    makeUrl: q => `https://www.tiktok.com/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'weibo',
    group: 'Social',
    label: 'Weibo',
    domain: 's.weibo.com',
    platform: 'twitter',
    makeUrl: q => `https://s.weibo.com/weibo?q=${encodeURIComponent(q)}`,
  },
  {
    id: '5ch',
    group: 'Community',
    label: '5ch',
    domain: 'find.5ch.net',
    platform: '5ch',
    makeUrl: q => `https://find.5ch.io/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'girlschannel',
    group: 'Community',
    label: 'GirlsChannel',
    domain: 'girlschannel.net',
    platform: 'girlschannel',
    makeUrl: q => `https://girlschannel.net/topics/search/?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'togetter',
    group: 'Community',
    label: 'Togetter',
    domain: 'togetter.com',
    platform: 'togetter',
    makeUrl: q => `https://togetter.com/search?q=${encodeURIComponent(q)}`,
  },
  {
    id: 'google-web',
    group: 'Web',
    label: 'Google Japan',
    domain: 'google.co.jp',
    platform: 'custom',
    makeUrl: q => `https://www.google.co.jp/search?q=${encodeURIComponent(q)}&hl=ja&gl=JP`,
  },
  {
    id: 'yahoo-web',
    group: 'Web',
    label: 'Yahoo! Japan Search',
    domain: 'search.yahoo.co.jp',
    platform: 'custom',
    makeUrl: q => `https://search.yahoo.co.jp/search?p=${encodeURIComponent(q)}`,
  },
  {
    id: 'yahoo-realtime',
    group: 'Web',
    label: 'Yahoo! Realtime',
    domain: 'search.yahoo.co.jp/realtime',
    platform: 'custom',
    makeUrl: q => `https://search.yahoo.co.jp/realtime/search?p=${encodeURIComponent(q)}`,
  },
  {
    id: 'mercari',
    group: 'Shopping',
    label: 'Mercari',
    domain: 'jp.mercari.com',
    platform: 'custom',
    makeUrl: q => `https://jp.mercari.com/search?keyword=${encodeURIComponent(q)}`,
  },
  {
    id: 'rakuten',
    group: 'Shopping',
    label: 'Rakuten Ichiba',
    domain: 'rakuten.co.jp',
    platform: 'custom',
    makeUrl: q => `https://search.rakuten.co.jp/search/mall/${encodeURIComponent(q)}/`,
  },
  {
    id: 'yahoo-auctions',
    group: 'Shopping',
    label: 'Yahoo! Auctions',
    domain: 'auctions.yahoo.co.jp',
    platform: 'custom',
    makeUrl: q => `https://auctions.yahoo.co.jp/search/search?p=${encodeURIComponent(q)}`,
  },
  {
    id: 'surugaya',
    group: 'Shopping',
    label: 'Surugaya',
    domain: 'suruga-ya.jp',
    platform: 'custom',
    makeUrl: q => `https://www.suruga-ya.jp/search?search_word=${encodeURIComponent(q)}`,
  },
  {
    id: 'animate',
    group: 'Shopping',
    label: 'Animate Online',
    domain: 'animate-onlineshop.jp',
    platform: 'custom',
    makeUrl: q => `https://www.animate-onlineshop.jp/products/list.php?mode=search&smt=${encodeURIComponent(q)}`,
  },
  {
    id: 'custom-google-site',
    group: 'Custom',
    label: 'Custom site search',
    domain: 'google.co.jp',
    platform: 'custom',
    makeUrl: q => `https://www.google.co.jp/search?q=${encodeURIComponent(q)}`,
  },
]

const GROUP_META: Record<string, { icon: IoniconsName; color: string }> = {
  News: { icon: 'newspaper-outline', color: '#D92323' },
  Entertainment: { icon: 'sparkles-outline', color: '#7A3FF2' },
  Magazines: { icon: 'book-outline', color: '#BE185D' },
  Video: { icon: 'play-circle-outline', color: '#0077CC' },
  Writing: { icon: 'document-text-outline', color: '#15803D' },
  Social: { icon: 'chatbubble-ellipses-outline', color: '#0F766E' },
  Community: { icon: 'people-outline', color: '#C2410C' },
  Web: { icon: 'globe-outline', color: '#2563EB' },
  Shopping: { icon: 'bag-handle-outline', color: '#DB2777' },
  Custom: { icon: 'link-outline', color: '#52525B' },
}

function makeStyles(t: Theme) {
  return StyleSheet.create({
    root: { flex: 1, backgroundColor: t.bg },
    content: { padding: 16, paddingBottom: 32, gap: 12 },
    searchBox: {
      backgroundColor: t.card, borderWidth: 1, borderColor: t.border,
      borderRadius: 8, padding: 12, gap: 10,
    },
    inputRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    input: {
      flex: 1, minHeight: 42, paddingHorizontal: 12, borderRadius: 8,
      backgroundColor: t.inputBg, borderWidth: 1, borderColor: t.border,
      color: t.text, fontSize: 15,
    },
    sectionHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
    sectionTitle: { color: t.text, fontSize: 15, fontWeight: '700' },
    sectionMeta: { color: t.textMuted, fontSize: 12, fontWeight: '500' },
    chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
    chip: {
      paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999,
      backgroundColor: t.divider, borderWidth: 1, borderColor: t.border,
    },
    chipActive: { backgroundColor: t.primary, borderColor: t.primary },
    chipText: { color: t.textSub, fontSize: 12, fontWeight: '500' },
    chipTextActive: { color: '#fff' },
    categoryScroller: { marginHorizontal: -16, paddingHorizontal: 16 },
    categoryRow: { gap: 8, paddingRight: 16 },
    category: {
      minHeight: 38, flexDirection: 'row', alignItems: 'center', gap: 7,
      paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999,
      backgroundColor: t.card, borderWidth: 1, borderColor: t.border,
    },
    categoryActive: { backgroundColor: t.primaryBg, borderColor: t.primary },
    categoryText: { color: t.textSub, fontSize: 13, fontWeight: '600' },
    categoryTextActive: { color: t.primary, fontWeight: '700' },
    group: { gap: 8 },
    groupTitle: {
      marginTop: 4, color: t.textMuted, fontSize: 10, fontWeight: '700',
      textTransform: 'uppercase', letterSpacing: 0.7,
    },
    row: {
      minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: 12,
      paddingHorizontal: 12, paddingVertical: 10, borderRadius: 8,
      backgroundColor: t.card, borderWidth: 1, borderColor: t.border,
    },
    rowIcon: {
      width: 34, height: 34, borderRadius: 17, alignItems: 'center',
      justifyContent: 'center', backgroundColor: t.primaryBg,
    },
    rowText: { flex: 1, gap: 2 },
    label: { color: t.text, fontSize: 14, fontWeight: '600' },
    domain: { color: t.textMuted, fontSize: 12 },
    empty: { color: t.textMuted, fontSize: 13, lineHeight: 19 },
  })
}

export default function SearchScreen() {
  const { theme } = useTheme()
  const s = useMemo(() => makeStyles(theme), [theme])
  const navigation = useNavigation<any>()
  const { data: terms = [] } = useQuery({ queryKey: ['watch-terms'], queryFn: getTerms })
  const activeTerms = terms.filter(term => term.is_active)
  const [keyword, setKeyword] = useState(activeTerms[0]?.keyword ?? '')
  const trimmed = keyword.trim()
  const [selectedGroup, setSelectedGroup] = useState('News')
  const [selectedLink, setSelectedLink] = useState<SearchLink | null>(null)
  const { isTablet } = useResponsive()

  useEffect(() => {
    if (!trimmed && activeTerms[0]?.keyword) setKeyword(activeTerms[0].keyword)
  }, [activeTerms, trimmed])

  useEffect(() => {
    setSelectedLink(null)
  }, [trimmed])

  const grouped = useMemo(() => {
    const map = new Map<string, SearchLink[]>()
    for (const link of SEARCH_LINKS) {
      const group = map.get(link.group) ?? []
      group.push(link)
      map.set(link.group, group)
    }
    return Array.from(map.entries())
  }, [])
  const selectedLinks = grouped.find(([group]) => group === selectedGroup)?.[1] ?? grouped[0]?.[1] ?? []

  const openLink = (link: SearchLink) => {
    if (!trimmed) return
    if (isTablet) {
      setSelectedLink(link)
      return
    }
    const url = link.makeUrl(trimmed)
    navigation.navigate('Reader', {
      url,
      title: `${link.label}: ${trimmed}`,
      id: `search:${link.id}:${encodeURIComponent(trimmed)}`,
      platform: link.platform,
    })
  }

  const mainContent = (
    <ScrollView style={s.root} contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
      <View style={s.searchBox}>
        <View style={s.inputRow}>
          <Ionicons name="search" size={20} color={theme.textMuted} />
          <TextInput
            value={keyword}
            onChangeText={setKeyword}
            placeholder="Keyword"
            placeholderTextColor={theme.textMuted}
            autoCapitalize="none"
            autoCorrect={false}
            style={s.input}
            returnKeyType="search"
          />
        </View>
        {activeTerms.length > 0 ? (
          <View style={s.chipRow}>
            {activeTerms.map(term => {
              const active = term.keyword === trimmed
              return (
                <Pressable
                  key={term.id}
                  style={[s.chip, active && s.chipActive]}
                  onPress={() => setKeyword(term.keyword)}
                >
                  <Text style={[s.chipText, active && s.chipTextActive]} numberOfLines={1}>
                    {term.keyword}
                  </Text>
                </Pressable>
              )
            })}
          </View>
        ) : (
          <Text style={s.empty}>Add watch keywords in Settings, or type a keyword here.</Text>
        )}
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={s.categoryScroller}
        contentContainerStyle={s.categoryRow}
      >
        {grouped.map(([group, links]) => {
          const meta = GROUP_META[group] ?? GROUP_META.News
          const active = group === selectedGroup
          return (
            <Pressable
              key={group}
              onPress={() => setSelectedGroup(group)}
              style={[s.category, active && s.categoryActive]}
            >
              <Ionicons name={meta.icon} size={16} color={active ? theme.primary : meta.color} />
              <Text style={[s.categoryText, active && s.categoryTextActive]}>{group}</Text>
              <Text style={[s.categoryText, active && s.categoryTextActive]}>{links.length}</Text>
            </Pressable>
          )
        })}
      </ScrollView>

      <View style={s.group}>
        <View style={s.sectionHeader}>
          <Text style={s.sectionTitle}>{selectedGroup}</Text>
          <Text style={s.sectionMeta}>{trimmed ? trimmed : 'Enter keyword'}</Text>
        </View>
        {selectedLinks.map(link => {
          const meta = GROUP_META[link.group] ?? GROUP_META.News
          const isSelected = selectedLink?.id === link.id
          return (
            <Pressable
              key={link.id}
              disabled={!trimmed}
              onPress={() => openLink(link)}
              style={({ pressed }) => [
                s.row,
                { opacity: !trimmed ? 0.45 : pressed ? 0.72 : 1 },
                isTablet && isSelected && { borderWidth: 2, borderColor: theme.primary }
              ]}
            >
              <View style={[s.rowIcon, { backgroundColor: `${meta.color}1A` }]}>
                <Ionicons name={meta.icon} size={18} color={meta.color} />
              </View>
              <View style={s.rowText}>
                <Text style={s.label}>{link.label}</Text>
                <Text style={s.domain}>{link.domain}</Text>
              </View>
              <Ionicons name="open-outline" size={18} color={theme.textMuted} />
            </Pressable>
          )
        })}
      </View>
    </ScrollView>
  )

  if (isTablet) {
    return (
      <View style={{ flex: 1, flexDirection: 'row', backgroundColor: theme.bg }}>
        <View style={{ width: 380, borderRightWidth: 1, borderColor: theme.divider }}>
          {mainContent}
        </View>
        <View style={{ flex: 1 }}>
          {selectedLink && trimmed ? (
            <ReaderScreen
              key={`${selectedLink.id}:${trimmed}`}
              embeddedParams={{
                url: selectedLink.makeUrl(trimmed),
                title: `${selectedLink.label}: ${trimmed}`,
                id: `search:${selectedLink.id}:${encodeURIComponent(trimmed)}`,
                platform: selectedLink.platform,
              }}
              isEmbedded={true}
            />
          ) : (
            <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: theme.bg }}>
              <Ionicons name="search-outline" size={64} color={theme.textMuted} />
              <Text style={{ marginTop: 16, color: theme.textMuted, fontSize: 15, fontWeight: '600' }}>
                Select a search site to view results
              </Text>
            </View>
          )}
        </View>
      </View>
    )
  }

  return mainContent
}
