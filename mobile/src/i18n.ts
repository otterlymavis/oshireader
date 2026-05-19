export type Lang = 'ja' | 'en' | 'zh-TW' | 'zh-CN'

interface Strings {
  // Tabs
  tabFeed: string; tabSaved: string; tabSettings: string; tabOshi: string; appTitle: string
  // Filter bar
  filter: string; period: string
  allTime: string; days3: string; month1: string; months3: string; months6: string
  keyword: string; all: string; addUrl: string
  // Feed empty
  feedEmpty: string; feedEmptyBody: string
  // Saved screen
  savedEmpty: string; savedEmptyBody: string; manual: string
  // Settings – watch keywords
  watchKeywords: string; keywordPlaceholder: string
  allInfo: string; mediaOnly: string; add: string; searching: string; noKeywords: string
  // Settings – sources
  sources: string; comingSoon: string; comingSoonTag: string
  // Settings – appearance / language
  appearance: string; language: string
  theme: string; lightTheme: string; darkTheme: string; systemTheme: string
  // Settings – storage
  storage: string; savedArticles: string; keywordCount: string; byPlatform: string
  // Settings – data
  data: string; clearAll: string; clearAllTitle: string; clearAllBody: string
  cancel: string; delete: string
  // Settings – misc
  lastFetch: string; noticeText: string
  deleteConfirmTitle: string; stopWatchingBody: string  // %s = keyword
  // Reader
  save: string; savedLabel: string; offlineBanner: string
  pageErrorTitle: string; pageErrorBody: string; openBrowser: string
  // Alerts
  errorAlert: string; notifPermTitle: string; notifPermBody: string
  // Time ago (%d = number)
  justNow: string; minAgo: string; hourAgo: string; dayAgo: string
  // Reader / appearance
  fontSize: string; autoTranslate: string
}

export const translations: Record<Lang, Strings> = {
  ja: {
    tabFeed: 'フィード', tabSaved: '保存済み', tabSettings: '設定', tabOshi: '推し', appTitle: 'Otterpia 🦦',
    filter: 'フィルター', period: '期間',
    allTime: '全期間', days3: '3日間', month1: '1ヶ月', months3: '3ヶ月', months6: '6ヶ月',
    keyword: 'キーワード', all: 'すべて', addUrl: '＋ URL追加',
    feedEmpty: 'まだ何もありません',
    feedEmptyBody: '「設定」からキーワードを追加すると、すぐに結果を取得します。',
    savedEmpty: 'まだ保存したページがありません',
    savedEmptyBody: 'フィードの記事のブックマークアイコンをタップして保存してください。',
    manual: '手動追加',
    watchKeywords: '推し', keywordPlaceholder: 'キーワード（例：山田裕貴）',
    allInfo: '全情報', mediaOnly: 'メディアのみ', add: '追加', searching: '検索中…', noKeywords: 'まだキーワードがありません',
    sources: 'ソース', comingSoon: '近日公開', comingSoonTag: '近日',
    appearance: '外観', language: '言語',
    theme: 'テーマ', lightTheme: '☀️ ライト', darkTheme: '🌙 ダーク', systemTheme: '📱 システム',
    storage: 'ストレージ', savedArticles: '保存済み記事', keywordCount: 'キーワード数', byPlatform: 'プラットフォーム別記事数',
    data: 'データ', clearAll: '全データを削除',
    clearAllTitle: '全データを削除しますか？',
    clearAllBody: '保存された全ての記事とキーワードが削除されます。',
    cancel: 'キャンセル', delete: '削除',
    lastFetch: '最終取得',
    noticeText: 'Otterpiaはすべてデバイス上で動作します。サーバー不要です。フィードタブの↻ボタンで最新情報を取得できます。',
    deleteConfirmTitle: '削除', stopWatchingBody: '「%s」の監視を停止しますか？',
    save: '保存', savedLabel: '保存済み',
    offlineBanner: '📥 保存済み — オフラインで読めます',
    pageErrorTitle: 'ページを開けませんでした',
    pageErrorBody: 'このページはアプリ内では表示できません。\n外部ブラウザで開いてください。',
    openBrowser: 'ブラウザで開く',
    errorAlert: 'エラー', notifPermTitle: '通知の許可が必要です',
    notifPermBody: '通知を使用するには端末の設定で通知を有効にしてください。',
    justNow: 'たった今', minAgo: '%d分前', hourAgo: '%d時間前', dayAgo: '%d日前',
    fontSize: '文字サイズ', autoTranslate: '自動翻訳',
  },
  en: {
    tabFeed: 'Feed', tabSaved: 'Saved', tabSettings: 'Settings', tabOshi: 'My Oshi', appTitle: 'Otterpia 🦦',
    filter: 'Filter', period: 'Period',
    allTime: 'All time', days3: '3 days', month1: '1 month', months3: '3 months', months6: '6 months',
    keyword: 'Keyword', all: 'All', addUrl: '+ Add URL',
    feedEmpty: 'Nothing here yet',
    feedEmptyBody: 'Add keywords in Settings to start fetching results.',
    savedEmpty: 'No saved pages yet',
    savedEmptyBody: 'Tap the bookmark icon on a feed item to save it.',
    manual: 'Manual',
    watchKeywords: 'Oshi', keywordPlaceholder: 'Keyword (e.g. idol name)',
    allInfo: 'All info', mediaOnly: 'Media only', add: 'Add', searching: 'Searching…', noKeywords: 'No keywords yet',
    sources: 'Sources', comingSoon: 'Coming Soon', comingSoonTag: 'Soon',
    appearance: 'Appearance', language: 'Language',
    theme: 'Theme', lightTheme: '☀️ Light', darkTheme: '🌙 Dark', systemTheme: '📱 System',
    storage: 'Storage', savedArticles: 'Saved articles', keywordCount: 'Keywords', byPlatform: 'Articles by platform',
    data: 'Data', clearAll: 'Clear all data',
    clearAllTitle: 'Clear all data?',
    clearAllBody: 'All saved articles and keywords will be deleted.',
    cancel: 'Cancel', delete: 'Delete',
    lastFetch: 'Last fetch',
    noticeText: 'Otterpia runs entirely on your device. No server needed. Use the ↻ button on the feed tab to refresh.',
    deleteConfirmTitle: 'Delete', stopWatchingBody: 'Stop watching "%s"?',
    save: 'Save', savedLabel: 'Saved',
    offlineBanner: '📥 Saved — readable offline',
    pageErrorTitle: 'Could not load page',
    pageErrorBody: 'This page cannot be displayed in-app.\nOpen it in your browser instead.',
    openBrowser: 'Open in browser',
    errorAlert: 'Error', notifPermTitle: 'Permission required',
    notifPermBody: 'Please enable notifications in your device settings.',
    justNow: 'Just now', minAgo: '%dm ago', hourAgo: '%dh ago', dayAgo: '%dd ago',
    fontSize: 'Font size', autoTranslate: 'Auto-translate',
  },
  'zh-TW': {
    tabFeed: '動態', tabSaved: '已儲存', tabSettings: '設定', tabOshi: '推', appTitle: 'Otterpia 🦦',
    filter: '篩選', period: '時間',
    allTime: '全部', days3: '3天', month1: '1個月', months3: '3個月', months6: '6個月',
    keyword: '關鍵字', all: '全部', addUrl: '＋ 新增URL',
    feedEmpty: '尚無內容',
    feedEmptyBody: '在「設定」中新增關鍵字以開始獲取結果。',
    savedEmpty: '尚無儲存頁面',
    savedEmptyBody: '點擊動態中文章的書籤圖示以儲存。',
    manual: '手動新增',
    watchKeywords: '推し', keywordPlaceholder: '關鍵字（例：偶像名稱）',
    allInfo: '全部資訊', mediaOnly: '僅媒體', add: '新增', searching: '搜尋中…', noKeywords: '尚無關鍵字',
    sources: '來源', comingSoon: '即將推出', comingSoonTag: '即將',
    appearance: '外觀', language: '語言',
    theme: '主題', lightTheme: '☀️ 淺色', darkTheme: '🌙 深色', systemTheme: '📱 系統',
    storage: '儲存空間', savedArticles: '已儲存文章', keywordCount: '關鍵字數', byPlatform: '各來源文章數',
    data: '資料', clearAll: '清除所有資料',
    clearAllTitle: '確認清除所有資料？',
    clearAllBody: '所有已儲存的文章和關鍵字將被刪除。',
    cancel: '取消', delete: '刪除',
    lastFetch: '最後擷取',
    noticeText: 'Otterpia完全在您的裝置上運行，無需伺服器。使用動態標籤的↻按鈕刷新。',
    deleteConfirmTitle: '刪除', stopWatchingBody: '停止監控「%s」？',
    save: '儲存', savedLabel: '已儲存',
    offlineBanner: '📥 已儲存 — 可離線閱讀',
    pageErrorTitle: '無法開啟頁面',
    pageErrorBody: '此頁面無法在應用程式內顯示。\n請在瀏覽器中開啟。',
    openBrowser: '在瀏覽器中開啟',
    errorAlert: '錯誤', notifPermTitle: '需要通知權限',
    notifPermBody: '請在裝置設定中開啟通知。',
    justNow: '剛剛', minAgo: '%d分鐘前', hourAgo: '%d小時前', dayAgo: '%d天前',
    fontSize: '字體大小', autoTranslate: '自動翻譯',
  },
  'zh-CN': {
    tabFeed: '动态', tabSaved: '已保存', tabSettings: '设置', tabOshi: '推', appTitle: 'Otterpia 🦦',
    filter: '筛选', period: '时间',
    allTime: '全部', days3: '3天', month1: '1个月', months3: '3个月', months6: '6个月',
    keyword: '关键字', all: '全部', addUrl: '＋ 添加URL',
    feedEmpty: '暂无内容',
    feedEmptyBody: '在「设置」中添加关键字以开始获取结果。',
    savedEmpty: '暂无保存页面',
    savedEmptyBody: '点击动态中文章的书签图标以保存。',
    manual: '手动添加',
    watchKeywords: '推し', keywordPlaceholder: '关键字（例：偶像名字）',
    allInfo: '全部信息', mediaOnly: '仅媒体', add: '添加', searching: '搜索中…', noKeywords: '暂无关键字',
    sources: '来源', comingSoon: '即将推出', comingSoonTag: '即将',
    appearance: '外观', language: '语言',
    theme: '主题', lightTheme: '☀️ 浅色', darkTheme: '🌙 深色', systemTheme: '📱 系统',
    storage: '存储空间', savedArticles: '已保存文章', keywordCount: '关键字数', byPlatform: '各来源文章数',
    data: '数据', clearAll: '清除所有数据',
    clearAllTitle: '确认清除所有数据？',
    clearAllBody: '所有已保存的文章和关键字将被删除。',
    cancel: '取消', delete: '删除',
    lastFetch: '最后获取',
    noticeText: 'Otterpia完全在您的设备上运行，无需服务器。使用动态标签的↻按钮刷新。',
    deleteConfirmTitle: '删除', stopWatchingBody: '停止监控「%s」？',
    save: '保存', savedLabel: '已保存',
    offlineBanner: '📥 已保存 — 可离线阅读',
    pageErrorTitle: '无法打开页面',
    pageErrorBody: '此页面无法在应用程序内显示。\n请在浏览器中打开。',
    openBrowser: '在浏览器中打开',
    errorAlert: '错误', notifPermTitle: '需要通知权限',
    notifPermBody: '请在设备设置中开启通知。',
    justNow: '刚刚', minAgo: '%d分钟前', hourAgo: '%d小时前', dayAgo: '%d天前',
    fontSize: '字体大小', autoTranslate: '自动翻译',
  },
}

export type TranslationKey = keyof Strings

export function getT(lang: Lang) {
  const s = translations[lang] ?? translations.ja
  return (key: TranslationKey): string =>
    (s as unknown as Record<string, string>)[key] ?? (translations.ja as unknown as Record<string, string>)[key] ?? key
}

export function timeAgoI18n(iso: string, t: ReturnType<typeof getT>): string {
  const mins = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60_000))
  if (mins < 1) return t('justNow')
  if (mins < 60) return t('minAgo').replace('%d', String(mins))
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return t('hourAgo').replace('%d', String(hrs))
  return t('dayAgo').replace('%d', String(Math.floor(hrs / 24)))
}

export const LANG_LABELS: Record<Lang, string> = {
  ja: '日本語',
  en: 'English',
  'zh-TW': '繁體中文',
  'zh-CN': '简体中文',
}
