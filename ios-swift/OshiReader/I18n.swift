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
            "en": "Oshi Reader",
            "ja": "推しリーダー",
            "zh-TW": "推特選讀 🦦",
            "zh-CN": "推特选读 🦦"
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
        ]
    ]
    
    func t(_ key: String) -> String {
        guard let item = translations[key] else { return key }
        return item[lang] ?? item["en"] ?? key
    }
}
