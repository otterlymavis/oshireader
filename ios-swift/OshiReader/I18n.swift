import Foundation

class I18nManager: ObservableObject {
    static let shared = I18nManager()
    
    @Published var lang: String = "ja" // Default to ja
    
    private init() {
        self.lang = UserDefaults.standard.string(forKey: "selected_lang") ?? "ja"
    }
    
    func setLanguage(_ language: String) {
        self.lang = language
        UserDefaults.standard.set(language, forKey: "selected_lang")
    }
    
    private let translations: [String: [String: String]] = [
        "appTitle": [
            "en": "OshiReader+",
            "ja": "推しリーダー+",
            "zh-TW": "OshiReader+",
            "zh-CN": "OshiReader+"
        ],
        "tabFeed": [
            "en": "Feed",
            "ja": "フィード",
            "zh-TW": "動態",
            "zh-CN": "动态"
        ],
        "tabSaved": [
            "en": "Saved",
            "ja": "ブックマーク",
            "zh-TW": "已儲存",
            "zh-CN": "已保存"
        ],
        "tabOshi": [
            "en": "My Oshi",
            "ja": "推しリスト",
            "zh-TW": "推",
            "zh-CN": "推"
        ],
        "tabSearch": [
            "en": "Search",
            "ja": "検索",
            "zh-TW": "篩選",
            "zh-CN": "筛选"
        ],
        "tabSettings": [
            "en": "Settings",
            "ja": "設定",
            "zh-TW": "設定",
            "zh-CN": "设置"
        ],
        "all": [
            "en": "All",
            "ja": "すべて",
            "zh-TW": "全部",
            "zh-CN": "全部"
        ],
        "filter": [
            "en": "Filter",
            "ja": "フィルター",
            "zh-TW": "篩選",
            "zh-CN": "筛选"
        ],
        "allInfo": [
            "en": "All Info",
            "ja": "全情報",
            "zh-TW": "全部資訊",
            "zh-CN": "全部信息"
        ],
        "mediaOnly": [
            "en": "Media Only",
            "ja": "メディア",
            "zh-TW": "僅媒體",
            "zh-CN": "仅媒体"
        ],
        "period": [
            "en": "Period",
            "ja": "期間",
            "zh-TW": "時間",
            "zh-CN": "时间"
        ],
        "allTime": [
            "en": "All Time",
            "ja": "全期間",
            "zh-TW": "全部",
            "zh-CN": "全部"
        ],
        "days3": [
            "en": "3 Days",
            "ja": "3日間",
            "zh-TW": "3天",
            "zh-CN": "3天"
        ],
        "month1": [
            "en": "1 Month",
            "ja": "1ヶ月",
            "zh-TW": "1個月",
            "zh-CN": "1个月"
        ],
        "months3": [
            "en": "3 Months",
            "ja": "3ヶ月",
            "zh-TW": "3個月",
            "zh-CN": "3个月"
        ],
        "months6": [
            "en": "6 Months",
            "ja": "6ヶ月",
            "zh-TW": "6個月",
            "zh-CN": "6个月"
        ],
        "keyword": [
            "en": "Keyword",
            "ja": "キーワード",
            "zh-TW": "關鍵字",
            "zh-CN": "关键字"
        ],
        "feedEmpty": [
            "en": "Feed is Empty",
            "ja": "フィードが空です",
            "zh-TW": "尚無內容",
            "zh-CN": "暂无内容"
        ],
        "feedEmptyBody": [
            "en": "Register watch keywords in Settings to track your Oshi.",
            "ja": "「設定」タブからキーワードを登録するとここに情報が表示されます",
            "zh-TW": "在「設定」中新增關鍵字以開始獲取結果。",
            "zh-CN": "在「设置」中添加关键字以开始获取结果。"
        ],
        "cancel": [
            "en": "Cancel",
            "ja": "キャンセル",
            "zh-TW": "取消",
            "zh-CN": "取消"
        ],
        "delete": [
            "en": "Delete",
            "ja": "削除",
            "zh-TW": "刪除",
            "zh-CN": "删除"
        ],
        "save": [
            "en": "Save",
            "ja": "保存",
            "zh-TW": "儲存",
            "zh-CN": "保存"
        ],
        "offlineSaved": [
            "en": "Saved for Offline",
            "ja": "オフライン保存済み",
            "zh-TW": "已儲存 — 可離線閱讀",
            "zh-CN": "已保存 — 可离线阅读"
        ],
        "savedTitle": [
            "en": "Bookmarked Pages",
            "ja": "ブックマーク一覧",
            "zh-TW": "已儲存",
            "zh-CN": "已保存"
        ],
        "addAlias": [
            "en": "+ alias",
            "ja": "+ 別名",
            "zh-TW": "+ 別名",
            "zh-CN": "+ 别名"
        ],
        "oshiEmpty": [
            "en": "Add your Oshi!",
            "ja": "推しを追加しよう！",
            "zh-TW": "新增你的推吧！",
            "zh-CN": "添加你的推吧！"
        ],
        "oshiEmptyBody": [
            "en": "Register a keyword in Settings and profile canvas will appear here.",
            "ja": "「設定」からキーワードを登録するとアバタープロフィールが表示されます",
            "zh-TW": "在「設定」中新增關鍵字以開始獲取結果。",
            "zh-CN": "在「设置」中添加关键字以开始获取结果。"
        ],
        "searchPlaceholder": [
            "en": "Search articles...",
            "ja": "記事を検索...",
            "zh-TW": "搜尋文章...",
            "zh-CN": "搜索文章..."
        ],
        "settingsTitle": [
            "en": "Settings",
            "ja": "設定",
            "zh-TW": "設定",
            "zh-CN": "设置"
        ],
        "wallpaper": [
            "en": "Wallpaper",
            "ja": "壁紙設定",
            "zh-TW": "壁紙設定",
            "zh-CN": "壁纸设置"
        ],
        "selectWallpaper": [
            "en": "Choose from stickers",
            "ja": "ステッカー画像から設定",
            "zh-TW": "從貼紙選擇",
            "zh-CN": "从贴纸选择"
        ],
        "clearWallpaper": [
            "en": "Clear Wallpaper",
            "ja": "壁紙をクリア",
            "zh-TW": "清除壁紙",
            "zh-CN": "清除壁纸"
        ],
        "language": [
            "en": "Language",
            "ja": "言語",
            "zh-TW": "語言",
            "zh-CN": "语言"
        ],
        "stats": [
            "en": "Statistics",
            "ja": "統計情報",
            "zh-TW": "儲存空間",
            "zh-CN": "存储空间"
        ],
        "watchTerms": [
            "en": "Keywords",
            "ja": "キーワード管理",
            "zh-TW": "關鍵字管理",
            "zh-CN": "关键字管理"
        ],
        "addKeyword": [
            "en": "Add Keyword",
            "ja": "キーワード追加",
            "zh-TW": "新增關鍵字",
            "zh-CN": "新增关键字"
        ],
        "inputKeyword": [
            "en": "Enter keyword...",
            "ja": "キーワードを入力...",
            "zh-TW": "關鍵字（例：偶像名稱）",
            "zh-CN": "关键字（例：偶像名字）"
        ],
        "backendUrl": [
            "en": "Backend Server Base URL",
            "ja": "バックエンドサーバーURL",
            "zh-TW": "後台伺服器URL",
            "zh-CN": "后台服务器URL"
        ],
        "readerModeText": [
            "en": "Reader Text Mode",
            "ja": "リーダーテキスト表示",
            "zh-TW": "閱讀器文字模式",
            "zh-CN": "阅读器文字模式"
        ],
        "readerModeWeb": [
            "en": "Original Web Mode",
            "ja": "オリジナルウェブ表示",
            "zh-TW": "原始網頁模式",
            "zh-CN": "原始网页模式"
        ],
        "avatarEditor": [
            "en": "Avatar Editor",
            "ja": "アバターエディタ",
            "zh-TW": "頭像編輯器",
            "zh-CN": "头像编辑器"
        ],
        "cropMode": [
            "en": "Crop / Move Mode",
            "ja": "切り抜き / 移動",
            "zh-TW": "裁剪 / 移動",
            "zh-CN": "裁剪 / 移动"
        ],
        "popularStickers": [
            "en": "Popular ✨",
            "ja": "おすすめ ✨",
            "zh-TW": "熱門 ✨",
            "zh-CN": "热门 ✨"
        ],
        "searchStickers": [
            "en": "Search Irasutoya...",
            "ja": "いらすとやで検索...",
            "zh-TW": "搜尋貼紙...",
            "zh-CN": "搜索贴纸..."
        ],

        // MARK: - Settings section headers
        "platformSettings": [
            "en": "Source Platforms",
            "ja": "配信プラットフォーム設定",
            "zh-TW": "訂閱平台",
            "zh-CN": "订阅平台"
        ],
        "notificationsSection": [
            "en": "Notifications",
            "ja": "通知",
            "zh-TW": "通知",
            "zh-CN": "通知"
        ],
        "appearanceSection": [
            "en": "Theme & Appearance",
            "ja": "テーマとカスタマイズ",
            "zh-TW": "外觀設定",
            "zh-CN": "外观设置"
        ],
        "readerSection": [
            "en": "Reader",
            "ja": "リーダー",
            "zh-TW": "閱讀器",
            "zh-CN": "阅读器"
        ],
        "credentialsSection": [
            "en": "API Credentials",
            "ja": "API設定",
            "zh-TW": "API設定",
            "zh-CN": "API设置"
        ],
        "privacySection": [
            "en": "Privacy",
            "ja": "プライバシー",
            "zh-TW": "隱私",
            "zh-CN": "隐私"
        ],
        "dataSection": [
            "en": "Data",
            "ja": "データ",
            "zh-TW": "資料",
            "zh-CN": "数据"
        ],

        // MARK: - Settings controls
        "appTheme": [
            "en": "App Theme",
            "ja": "アプリテーマ",
            "zh-TW": "主題",
            "zh-CN": "主题"
        ],
        "themeLight": [
            "en": "Light",
            "ja": "ライト",
            "zh-TW": "淺色",
            "zh-CN": "浅色"
        ],
        "themeDark": [
            "en": "Dark",
            "ja": "ダーク",
            "zh-TW": "深色",
            "zh-CN": "深色"
        ],
        "themeSepia": [
            "en": "Sepia",
            "ja": "セピア",
            "zh-TW": "復古",
            "zh-CN": "复古"
        ],
        "collectionMode": [
            "en": "Collection Mode",
            "ja": "収集モード",
            "zh-TW": "收集模式",
            "zh-CN": "收集模式"
        ],
        "add": [
            "en": "Add",
            "ja": "追加",
            "zh-TW": "新增",
            "zh-CN": "添加"
        ],
        "autoTranslate": [
            "en": "Auto Translate Articles",
            "ja": "記事を自動翻訳",
            "zh-TW": "自動翻譯文章",
            "zh-CN": "自动翻译文章"
        ],

        // MARK: - OshiView
        "oshiListTitle": [
            "en": "My Oshi ✨",
            "ja": "推しリスト ✨",
            "zh-TW": "推清單 ✨",
            "zh-CN": "推清单 ✨"
        ],
        "oshiTrackingCount": [
            "en": "%d tracked",
            "ja": "%d人の推しを追跡中",
            "zh-TW": "追蹤中：%d",
            "zh-CN": "追踪中：%d"
        ],
        "tapToAddToCanvas": [
            "en": "Tap an image below to add to canvas",
            "ja": "下の画像をタップしてキャンバスに追加",
            "zh-TW": "點擊下方圖片加入畫布",
            "zh-CN": "点击下方图片添加到画布"
        ],

        // MARK: - AvatarEditorView
        "saveAvatar": [
            "en": "💾 Save",
            "ja": "💾 保存",
            "zh-TW": "💾 儲存",
            "zh-CN": "💾 保存"
        ],
        "setAsWallpaper": [
            "en": "🌸 Set Wallpaper",
            "ja": "🌸 壁紙にする",
            "zh-TW": "🌸 設為壁紙",
            "zh-CN": "🌸 设为壁纸"
        ],
        "noStickersFound": [
            "en": "No stickers found",
            "ja": "イラストが見つかりません",
            "zh-TW": "找不到貼圖",
            "zh-CN": "找不到贴图"
        ],
        "layerForward": [
            "en": "↑Fwd",
            "ja": "↑前",
            "zh-TW": "↑前",
            "zh-CN": "↑前"
        ],
        "layerBack": [
            "en": "↓Back",
            "ja": "↓後",
            "zh-TW": "↓後",
            "zh-CN": "↓后"
        ],

        // MARK: - SavedView
        "savedSelectArticle": [
            "en": "Select a saved article",
            "ja": "保存した記事を選択してください",
            "zh-TW": "請選擇已儲存的文章",
            "zh-CN": "请选择已保存的文章"
        ],
        "savedEmptyTitle": [
            "en": "No Saved Articles",
            "ja": "ブックマークがありません",
            "zh-TW": "尚無儲存文章",
            "zh-CN": "暂无保存文章"
        ],
        "savedEmptyBody": [
            "en": "Save articles from your feed to read them here, even offline.",
            "ja": "フィードから気になる記事を保存すると、ここにオフラインでも読めるように表示されます。",
            "zh-TW": "從動態儲存文章，可在此離線閱讀。",
            "zh-CN": "从动态保存文章，可在此离线阅读。"
        ],

        // MARK: - FeedView / SearchView empty states
        "feedSelectArticle": [
            "en": "Select an article from your feed",
            "ja": "フィードから記事を選択してください",
            "zh-TW": "請從動態選擇文章",
            "zh-CN": "请从动态选择文章"
        ],
        "noCustomUrlsAdded": [
            "en": "No custom URLs added yet",
            "ja": "カスタムURLが登録されていません",
            "zh-TW": "尚未新增自訂網址",
            "zh-CN": "尚未添加自定义网址"
        ],
        "searchSelectArticle": [
            "en": "Select a search result to read",
            "ja": "検索リンクから記事を選択してください",
            "zh-TW": "請選擇搜尋結果閱讀",
            "zh-CN": "请选择搜索结果阅读"
        ]
    ]

    func t(_ key: String) -> String {
        guard let item = translations[key] else { return key }
        return item[lang] ?? item["en"] ?? key
    }

    func tFormat(_ key: String, _ value: Int) -> String {
        t(key).replacingOccurrences(of: "%d", with: String(value))
    }
}
