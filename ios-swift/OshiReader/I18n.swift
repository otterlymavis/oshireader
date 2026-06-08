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
        "unsave": [
            "en": "Unsave",
            "ja": "保存解除",
            "zh-TW": "取消儲存",
            "zh-CN": "取消保存"
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
        "readerTitle": [
            "en": "Reader",
            "ja": "リーダー",
            "zh-TW": "閱讀器",
            "zh-CN": "阅读器"
        ],
        "invalidUrl": [
            "en": "Invalid URL",
            "ja": "無効なURL",
            "zh-TW": "無效的網址",
            "zh-CN": "无效的链接"
        ],
        "ok": [
            "en": "OK",
            "ja": "OK",
            "zh-TW": "好",
            "zh-CN": "好"
        ],
        "imageActions": [
            "en": "Image",
            "ja": "画像",
            "zh-TW": "圖片",
            "zh-CN": "图片"
        ],
        "shareImage": [
            "en": "Share Image",
            "ja": "画像を共有",
            "zh-TW": "分享圖片",
            "zh-CN": "分享图片"
        ],
        "saveImage": [
            "en": "Save Image",
            "ja": "画像を保存",
            "zh-TW": "儲存圖片",
            "zh-CN": "保存图片"
        ],
        "openImage": [
            "en": "Open Image",
            "ja": "画像を開く",
            "zh-TW": "開啟圖片",
            "zh-CN": "打开图片"
        ],
        "imageLoadError": [
            "en": "Could not read this image.",
            "ja": "画像を読み込めませんでした。",
            "zh-TW": "無法讀取此圖片。",
            "zh-CN": "无法读取此图片。"
        ],
        "photosAccessRequired": [
            "en": "Photos access is required to save images.",
            "ja": "画像を保存するには写真へのアクセスが必要です。",
            "zh-TW": "需要相簿存取權限才能儲存圖片。",
            "zh-CN": "需要照片访问权限才能保存图片。"
        ],
        "imageSavedToPhotos": [
            "en": "Saved to Photos.",
            "ja": "写真に保存しました。",
            "zh-TW": "已儲存到相簿。",
            "zh-CN": "已保存到照片。"
        ],
        "imageSaveError": [
            "en": "Could not save this image.",
            "ja": "画像を保存できませんでした。",
            "zh-TW": "無法儲存此圖片。",
            "zh-CN": "无法保存此图片。"
        ],
        "imageNoLargeImages": [
            "en": "No large images found on this page.",
            "ja": "このページに大きな画像が見つかりませんでした。",
            "zh-TW": "此頁面未找到大圖片。",
            "zh-CN": "此页面未找到大图片。"
        ],
        "savedImagesToPhotos": [
            "en": "Saved %d image(s) to Photos.",
            "ja": "%d 枚の画像を保存しました。",
            "zh-TW": "已儲存 %d 張圖片到相簿。",
            "zh-CN": "已保存 %d 张图片到照片。"
        ],
        "imageNoneSaved": [
            "en": "No images could be saved.",
            "ja": "画像を保存できませんでした。",
            "zh-TW": "沒有圖片可以儲存。",
            "zh-CN": "没有图片可以保存。"
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
        "pushNotifications": [
            "en": "Push Notifications",
            "ja": "プッシュ通知",
            "zh-TW": "推播通知",
            "zh-CN": "推送通知"
        ],
        "notifStatusEnabled": [
            "en": "Enabled",
            "ja": "有効",
            "zh-TW": "已啟用",
            "zh-CN": "已启用"
        ],
        "notifStatusProvisional": [
            "en": "Quietly enabled",
            "ja": "仮許可",
            "zh-TW": "靜默啟用",
            "zh-CN": "静默启用"
        ],
        "notifStatusDenied": [
            "en": "Disabled in iOS Settings",
            "ja": "iOS設定で無効",
            "zh-TW": "已在 iOS 設定中停用",
            "zh-CN": "已在 iOS 设置中停用"
        ],
        "notifStatusEphemeral": [
            "en": "Temporarily enabled",
            "ja": "一時的に許可",
            "zh-TW": "暫時啟用",
            "zh-CN": "临时启用"
        ],
        "notifStatusNotDetermined": [
            "en": "Not requested",
            "ja": "未設定",
            "zh-TW": "未要求",
            "zh-CN": "未请求"
        ],
        "notifStatusUnknown": [
            "en": "Unknown",
            "ja": "不明",
            "zh-TW": "未知",
            "zh-CN": "未知"
        ],
        "enableNotifications": [
            "en": "Enable Notifications",
            "ja": "通知を有効にする",
            "zh-TW": "啟用通知",
            "zh-CN": "启用通知"
        ],
        "openIOSSettings": [
            "en": "Open iOS Settings",
            "ja": "iOS設定を開く",
            "zh-TW": "開啟 iOS 設定",
            "zh-CN": "打开 iOS 设置"
        ],
        "sendTestNotification": [
            "en": "Send Test Notification",
            "ja": "テスト通知を送信",
            "zh-TW": "傳送測試通知",
            "zh-CN": "发送测试通知"
        ],
        "notifTestBody": [
            "en": "Notifications are ready.",
            "ja": "通知の準備ができました。",
            "zh-TW": "通知功能已就緒。",
            "zh-CN": "通知功能已准备就绪。"
        ],
        "notifNewItemsTitle": [
            "en": "New items for %@",
            "ja": "%@ の新着",
            "zh-TW": "%@ 的新內容",
            "zh-CN": "%@ 的新内容"
        ],
        "notifNewItemsBodyOne": [
            "en": "1 new item found.",
            "ja": "1件の新着があります。",
            "zh-TW": "1 個新項目。",
            "zh-CN": "1 个新内容。"
        ],
        "notifNewItemsBodyMany": [
            "en": "%d new items found.",
            "ja": "%d件の新着があります。",
            "zh-TW": "%d 個新項目。",
            "zh-CN": "%d 个新内容。"
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
        "youtubeApiKey": [
            "en": "YouTube API Key",
            "ja": "YouTube APIキー",
            "zh-TW": "YouTube API 金鑰",
            "zh-CN": "YouTube API 密钥"
        ],
        "xBearerToken": [
            "en": "X Bearer Token",
            "ja": "X Bearerトークン",
            "zh-TW": "X Bearer Token",
            "zh-CN": "X Bearer Token"
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
        "cropModeBtn": [
            "en": "Crop",
            "ja": "切り取り",
            "zh-TW": "裁切",
            "zh-CN": "裁剪"
        ],
        "moveModeBtn": [
            "en": "Move",
            "ja": "移動",
            "zh-TW": "移動",
            "zh-CN": "移动"
        ],
        "zoomIn": [
            "en": "Zoom +",
            "ja": "拡大",
            "zh-TW": "放大",
            "zh-CN": "放大"
        ],
        "zoomOut": [
            "en": "Zoom -",
            "ja": "縮小",
            "zh-TW": "縮小",
            "zh-CN": "缩小"
        ],
        "fitToCanvas": [
            "en": "Fit",
            "ja": "合わせる",
            "zh-TW": "符合",
            "zh-CN": "适应"
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
        ],

        // MARK: - Common actions
        "edit": [
            "en": "Edit",
            "ja": "編集",
            "zh-TW": "編輯",
            "zh-CN": "编辑"
        ],
        "privacyPolicy": [
            "en": "Privacy Policy",
            "ja": "プライバシーポリシー",
            "zh-TW": "隱私政策",
            "zh-CN": "隐私政策"
        ],
        "clearAllData": [
            "en": "Clear All Data",
            "ja": "データをすべて削除",
            "zh-TW": "清除所有資料",
            "zh-CN": "清除所有数据"
        ],
        "clearAllDataAlert": [
            "en": "Clear All Data?",
            "ja": "データをすべて削除しますか？",
            "zh-TW": "清除所有資料？",
            "zh-CN": "清除所有数据？"
        ],
        "clearAllDataMessage": [
            "en": "This removes keywords, feed items, saved pages, custom URLs, avatars, hidden items, wallpaper, and source order from this device.",
            "ja": "キーワード、フィードアイテム、保存ページ、カスタムURL、アバター、非表示アイテム、壁紙、ソース順序がこのデバイスから削除されます。",
            "zh-TW": "將從此裝置移除關鍵字、動態項目、已儲存頁面、自訂URL、頭貼、隱藏項目、壁紙及來源順序。",
            "zh-CN": "将从此设备移除关键词、动态项目、已保存页面、自定义URL、头像、隐藏项目、壁纸及来源顺序。"
        ],

        // MARK: - Appearance settings
        "themeStyle": [
            "en": "Style",
            "ja": "スタイル",
            "zh-TW": "樣式",
            "zh-CN": "样式"
        ],
        "font": [
            "en": "Font",
            "ja": "フォント",
            "zh-TW": "字體",
            "zh-CN": "字体"
        ],
        "fontSize": [
            "en": "Font Size",
            "ja": "文字サイズ",
            "zh-TW": "字體大小",
            "zh-CN": "字体大小"
        ],

        // MARK: - FeedView
        "feedSearchingOffline": [
            "en": "Searching offline sources…",
            "ja": "オフラインソースを検索中…",
            "zh-TW": "搜尋離線來源…",
            "zh-CN": "搜索离线来源…"
        ],
        "feedLoadMoreFmt": [
            "en": "Load more (%d remaining)",
            "ja": "もっと見る（残り%d件）",
            "zh-TW": "載入更多（剩餘%d則）",
            "zh-CN": "加载更多（剩余%d条）"
        ],
        "addCustomFeed": [
            "en": "Add Custom RSS/Web Feed",
            "ja": "カスタムRSS/Webフィードを追加",
            "zh-TW": "新增自訂RSS/網頁來源",
            "zh-CN": "添加自定义RSS/网页来源"
        ],
        "feedTitlePlaceholder": [
            "en": "Feed/Webpage Title…",
            "ja": "フィード/ページタイトル…",
            "zh-TW": "來源/網頁標題…",
            "zh-CN": "来源/网页标题…"
        ],
        "urlPlaceholder": [
            "en": "https://…",
            "ja": "https://…",
            "zh-TW": "https://…",
            "zh-CN": "https://…"
        ],
        "reorderSources": [
            "en": "Reorder Sources",
            "ja": "ソースを並び替え",
            "zh-TW": "重新排列來源",
            "zh-CN": "重新排列来源"
        ],

        // MARK: - Refresh error messages (keys returned by refreshErrorKey)
        "errorNoInternet": [
            "en": "No internet connection",
            "ja": "インターネット接続なし",
            "zh-TW": "無網路連線",
            "zh-CN": "无网络连接"
        ],
        "errorTimeout": [
            "en": "Connection timed out",
            "ja": "接続タイムアウト",
            "zh-TW": "連線逾時",
            "zh-CN": "连接超时"
        ],
        "errorUnreachable": [
            "en": "Server unreachable",
            "ja": "サーバーに接続できません",
            "zh-TW": "無法連接伺服器",
            "zh-CN": "无法连接服务器"
        ],
        "errorUnavailable": [
            "en": "Backend unavailable",
            "ja": "バックエンドが利用できません",
            "zh-TW": "後端暫時無法使用",
            "zh-CN": "后端暂时不可用"
        ],
        "errorRefreshFailed": [
            "en": "Refresh failed",
            "ja": "更新に失敗しました",
            "zh-TW": "更新失敗",
            "zh-CN": "刷新失败"
        ],
        "errorDecode": [
            "en": "Server response error",
            "ja": "サーバー応答エラー",
            "zh-TW": "伺服器回應錯誤",
            "zh-CN": "服务器响应错误"
        ],

        // MARK: - SearchView
        "searchSavedUrls": [
            "en": "Saved URLs",
            "ja": "保存済みURL",
            "zh-TW": "已儲存的URL",
            "zh-CN": "已保存的URL"
        ],
        "searchEnterKeyword": [
            "en": "Enter keyword",
            "ja": "キーワードを入力",
            "zh-TW": "輸入關鍵字",
            "zh-CN": "输入关键字"
        ],
        "searchAddKeywordHint": [
            "en": "Add watch keywords in Settings, or type a keyword here.",
            "ja": "設定で追跡キーワードを追加するか、ここでキーワードを入力してください。",
            "zh-TW": "請在設定中新增追蹤關鍵字，或在此輸入關鍵字。",
            "zh-CN": "请在设置中添加追踪关键词，或在此输入关键词。"
        ],

        // MARK: - Search group names
        "searchGroupNews": [
            "en": "News",
            "ja": "ニュース",
            "zh-TW": "新聞",
            "zh-CN": "新闻"
        ],
        "searchGroupEntertainment": [
            "en": "Entertainment",
            "ja": "エンタメ",
            "zh-TW": "娛樂",
            "zh-CN": "娱乐"
        ],
        "searchGroupMagazines": [
            "en": "Magazines",
            "ja": "雑誌",
            "zh-TW": "雜誌",
            "zh-CN": "杂志"
        ],
        "searchGroupVideo": [
            "en": "Video",
            "ja": "動画",
            "zh-TW": "影片",
            "zh-CN": "视频"
        ],
        "searchGroupWriting": [
            "en": "Writing",
            "ja": "ライター",
            "zh-TW": "寫作",
            "zh-CN": "写作"
        ],
        "searchGroupSocial": [
            "en": "Social",
            "ja": "SNS",
            "zh-TW": "社群",
            "zh-CN": "社交"
        ],
        "searchGroupCommunity": [
            "en": "Community",
            "ja": "コミュニティ",
            "zh-TW": "社區",
            "zh-CN": "社区"
        ],
        "searchGroupWeb": [
            "en": "Web",
            "ja": "ウェブ",
            "zh-TW": "網頁",
            "zh-CN": "网页"
        ],
        "searchGroupShopping": [
            "en": "Shopping",
            "ja": "ショッピング",
            "zh-TW": "購物",
            "zh-CN": "购物"
        ],
        "searchGroupCustom": [
            "en": "Custom",
            "ja": "カスタム",
            "zh-TW": "自訂",
            "zh-CN": "自定义"
        ]
    ]

    func t(_ key: String) -> String {
        guard let item = translations[key] else { return key }
        return item[lang] ?? item["en"] ?? key
    }

    func tFormat(_ key: String, _ value: Int) -> String {
        t(key).replacingOccurrences(of: "%d", with: String(value))
    }

    func tFormat(_ key: String, _ value: String) -> String {
        t(key).replacingOccurrences(of: "%@", with: value)
    }

    func tSearchGroup(_ group: String) -> String {
        let key = "searchGroup\(group)"
        let result = t(key)
        return result == key ? group : result
    }

    var allTranslationKeys: [String] { Array(translations.keys) }
}
