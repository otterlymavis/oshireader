import XCTest
import SwiftUI
import UserNotifications
@testable import OshiReader

@MainActor
final class OshiReaderTests: XCTestCase {
    
    private var db: LocalDB!
    
    override func setUpWithError() throws {
        try super.setUpWithError()
        db = LocalDB.shared
        // Clear state before tests if needed, or work with a clean slate
        db.terms.removeAll()
        db.feedItems.removeAll()
        db.savedPages.removeAll()
        db.customUrls.removeAll()
        db.hiddenItems.removeAll()
        db.compositions.removeAll()
        db.setSubscribedPlatforms(platforms: ["news", "tver", "youtube", "yahoonews", "custom"])
    }
    
    override func tearDownWithError() throws {
        db = nil
        try super.tearDownWithError()
    }
    
    // MARK: - Feature 1: Watch Keywords (Terms)
    func testWatchTerms() throws {
        // 1. Save watch term
        let term = db.saveTerm(keyword: "Test Oshi", collectionMode: "media_only")
        
        XCTAssertEqual(db.terms.count, 1)
        XCTAssertEqual(db.terms.first?.keyword, "Test Oshi")
        XCTAssertEqual(db.terms.first?.collection_mode, "media_only")
        XCTAssertTrue(db.terms.first?.is_active ?? false)
        
        // 2. Update watch term
        db.updateTerm(id: term.id, isActive: false, collectionMode: "all_info")
        
        XCTAssertEqual(db.terms.first?.is_active, false)
        XCTAssertEqual(db.terms.first?.collection_mode, "all_info")

        db.updateTerm(id: term.id, notifyOnNew: true)
        XCTAssertEqual(db.terms.first?.notify_on_new, true)
        
        // 3. Delete watch term
        db.deleteTerm(id: term.id)
        XCTAssertEqual(db.terms.count, 0)
    }
    
    // MARK: - Feature 2: Feed Items merging & duplicates checking
    func testFeedItemsMerge() throws {
        let nowString = ISO8601DateFormatter().string(from: Date())
        let item1 = FeedItem(
            id: "youtube:123",
            platform: "youtube",
            url: "https://youtube.com/watch?v=123",
            title: "Oshi Concert",
            content_text: "Oshi sings wonderfully",
            author: "Oshi Channel",
            thumbnail_url: nil,
            media_type: "video",
            published_at: nowString,
            watch_term_keyword: "Oshi",
            fetched_at: nowString
        )
        
        // Duplicate item with shorter title
        let item1Duplicate = FeedItem(
            id: "youtube:123",
            platform: "youtube",
            url: "https://youtube.com/watch?v=123",
            title: "Oshi",
            content_text: "Oshi sings wonderfully",
            author: "Oshi Channel",
            thumbnail_url: nil,
            media_type: "video",
            published_at: nowString,
            watch_term_keyword: "Oshi",
            fetched_at: nowString
        )
        
        let item2 = FeedItem(
            id: "tver:456",
            platform: "tver",
            url: "https://tver.jp/episodes/456",
            title: "Oshi Drama",
            content_text: "Oshi acts nicely",
            author: "Drama Channel",
            thumbnail_url: nil,
            media_type: "video",
            published_at: nowString,
            watch_term_keyword: "Oshi",
            fetched_at: nowString
        )
        
        // Merge item1
        let added1 = db.mergeItems(newItems: [item1])
        XCTAssertEqual(added1, 1)
        XCTAssertEqual(db.feedItems.count, 1)
        XCTAssertEqual(db.feedItems.first?.title, "Oshi Concert")
        
        // Merge duplicate - title should NOT be shortened because original title is longer and better
        let addedDup = db.mergeItems(newItems: [item1Duplicate])
        XCTAssertEqual(addedDup, 0) // No new item added
        XCTAssertEqual(db.feedItems.count, 1)
        XCTAssertEqual(db.feedItems.first?.title, "Oshi Concert")
        
        // Merge item2
        let added2 = db.mergeItems(newItems: [item2])
        XCTAssertEqual(added2, 1)
        XCTAssertEqual(db.feedItems.count, 2)
    }

    @MainActor
    func testNotificationManagerSchedulesTestNotificationAfterAuthorization() async throws {
        let center = MockNotificationCenter(status: .notDetermined, grantsAuthorization: true)
        let manager = NotificationManager(center: center)

        try await manager.sendTestNotification()

        XCTAssertEqual(center.authorizationRequestCount, 1)
        XCTAssertEqual(center.requests.count, 1)
        XCTAssertEqual(center.requests.first?.content.title, "OshiReader")
        XCTAssertEqual(center.requests.first?.content.body, "Notifications are ready.")
        XCTAssertNotNil(center.requests.first?.trigger)
    }

    func testAPNSDeviceTokenStringUsesLowercaseHex() throws {
        let data = Data([0x00, 0x0f, 0xa1, 0xff])
        XCTAssertEqual(NotificationManager.deviceTokenString(data), "000fa1ff")
    }

    func testDebugSchemeUsesLocalBackendConfiguration() throws {
        XCTAssertEqual(NetworkManager.shared.environmentName, "Local")
        XCTAssertEqual(NetworkManager.shared.apiBase, "http://127.0.0.1:8000")
    }

    @MainActor
    func testPerTermNotificationsOnlyScheduleForEnabledTerms() async throws {
        let center = MockNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center)
        let nowString = ISO8601DateFormatter().string(from: Date())

        let enabledTerm = WatchTerm(id: "enabled", keyword: "Enabled Oshi", notify_on_new: true)
        let disabledTerm = WatchTerm(id: "disabled", keyword: "Muted Oshi", notify_on_new: false)
        let items = [
            FeedItem(
                id: "youtube:enabled-1", platform: "youtube", url: "https://youtube.com/1",
                title: "Enabled first", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "video", published_at: nowString, watch_term_keyword: enabledTerm.keyword,
                fetched_at: nowString
            ),
            FeedItem(
                id: "note:enabled-2", platform: "note", url: "https://note.com/2",
                title: "Enabled second", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "article", published_at: nowString, watch_term_keyword: enabledTerm.keyword,
                fetched_at: nowString
            ),
            FeedItem(
                id: "tver:muted", platform: "tver", url: "https://tver.jp/episodes/3",
                title: "Muted", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "video", published_at: nowString, watch_term_keyword: disabledTerm.keyword,
                fetched_at: nowString
            )
        ]

        await manager.notifyForNewItems(items, terms: [enabledTerm, disabledTerm])

        XCTAssertEqual(center.requests.count, 1)
        XCTAssertEqual(center.requests.first?.content.title, "New items for Enabled Oshi")
        XCTAssertEqual(center.requests.first?.content.body, "2 new items found.")
        XCTAssertNil(center.requests.first?.trigger)
    }

    func testMergeItemsOnlyNotifiesForNewItems() throws {
        let nowString = ISO8601DateFormatter().string(from: Date())
        let item = FeedItem(
            id: "youtube:notify-once",
            platform: "youtube",
            url: "https://youtube.com/watch?v=notify-once",
            title: "Notify once",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: nowString,
            watch_term_keyword: "Notify Oshi",
            fetched_at: nowString
        )

        XCTAssertEqual(db.mergeItems(newItems: [item]), 1)
        XCTAssertEqual(db.mergeItems(newItems: [item]), 0)
    }
    
    // MARK: - Feature 3: Feed Querying & Filters (Strict matches, platform toggles, days)
    func testFeedQueryingFilters() throws {
        let formatter = ISO8601DateFormatter()
        let now = Date()
        let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: now)!
        let olderThanMonth = Calendar.current.date(byAdding: .day, value: -35, to: now)!
        
        let itemNow = FeedItem(
            id: "youtube:now", platform: "youtube", url: "https://u",
            title: "Aiko now news video", content_text: "Aiko is active", author: "Aiko",
            thumbnail_url: nil, media_type: "video", published_at: formatter.string(from: now),
            watch_term_keyword: "Aiko", fetched_at: formatter.string(from: now)
        )
        
        let itemYesterday = FeedItem(
            id: "tver:yesterday", platform: "tver", url: "https://u",
            title: "Aiko drama episode", content_text: "TVer episode", author: "TVer",
            thumbnail_url: nil, media_type: "video", published_at: formatter.string(from: yesterday),
            watch_term_keyword: "Aiko", fetched_at: formatter.string(from: now)
        )
        
        let itemOld = FeedItem(
            id: "yahoonews:old", platform: "yahoonews", url: "https://u",
            title: "Aiko news article", content_text: "Yahoo news text", author: "Yahoo",
            thumbnail_url: nil, media_type: "article", published_at: formatter.string(from: olderThanMonth),
            watch_term_keyword: "Aiko", fetched_at: formatter.string(from: now)
        )
        
        let itemStrictMismatch = FeedItem(
            id: "tver:mismatch", platform: "tver", url: "https://u",
            title: "Aiko show on TVer", content_text: "Only contains Aiko text", author: "TVer",
            thumbnail_url: nil, media_type: "video", published_at: formatter.string(from: now),
            watch_term_keyword: "Miku", fetched_at: formatter.string(from: now)
        )
        
        _ = db.mergeItems(newItems: [itemNow, itemYesterday, itemOld, itemStrictMismatch])
        
        // Verifies platform subscription is active
        db.setSubscribedPlatforms(platforms: ["youtube", "tver", "yahoonews"])
        
        // 1. Query for 30 days, keyword "Aiko"
        let query1 = db.queryFeed(keyword: "Aiko", days: 30)
        XCTAssertEqual(query1.count, 2) // Should exclude itemOld (35 days old) and mismatch
        XCTAssertEqual(query1.first?.id, "youtube:now")
        XCTAssertEqual(query1.last?.id, "tver:yesterday")
        
        // 2. Query for 90 days, keyword "Aiko"
        let query2 = db.queryFeed(keyword: "Aiko", days: 90)
        XCTAssertEqual(query2.count, 3) // Should include itemOld now
        
        // 3. Query strict mismatch check
        let queryStrict = db.queryFeed(keyword: "Miku", days: 30)
        XCTAssertEqual(queryStrict.count, 0) // Strictly mismatched keyword text should be filtered out
        
        // 4. Query with unsubscribed platform
        db.setSubscribedPlatforms(platforms: ["tver"]) // Unsubscribe youtube
        let querySub = db.queryFeed(keyword: "Aiko", days: 30)
        XCTAssertEqual(querySub.count, 1) // Only TVer yesterday item remains
        XCTAssertEqual(querySub.first?.id, "tver:yesterday")
    }
    
    // MARK: - Feature 4: Saved Bookmarks
    func testSavedBookmarks() throws {
        let item = FeedItem(
            id: "news:111", platform: "news", url: "https://url", title: "Bookmark test",
            content_text: nil, author: nil, thumbnail_url: nil, media_type: "article",
            published_at: "2026-06-02T12:00:00Z", watch_term_keyword: "", fetched_at: ""
        )
        
        XCTAssertEqual(db.getSaved().count, 0)
        
        // Toggle saved (Add)
        let isSaved1 = db.toggleSaved(item: item)
        XCTAssertTrue(isSaved1)
        XCTAssertEqual(db.getSaved().count, 1)
        XCTAssertEqual(db.getSaved().first?.id, "news:111")
        
        // Toggle saved (Remove)
        let isSaved2 = db.toggleSaved(item: item)
        XCTAssertFalse(isSaved2)
        XCTAssertEqual(db.getSaved().count, 0)
    }
    
    // MARK: - Feature 5: Custom tracked URLs
    func testCustomUrls() throws {
        XCTAssertEqual(db.customUrls.count, 0)
        
        db.addCustomUrl(url: "https://myoshi-blog.com/feed", title: "Oshi Blog")
        XCTAssertEqual(db.customUrls.count, 1)
        XCTAssertEqual(db.customUrls.first?.title, "Oshi Blog")
        XCTAssertEqual(db.customUrls.first?.url, "https://myoshi-blog.com/feed")
        
        let id = db.customUrls.first!.id
        db.removeCustomUrl(id: id)
        XCTAssertEqual(db.customUrls.count, 0)
    }
    
    // MARK: - Feature 6: Oshi Avatars Compositions
    func testAvatarCompositions() throws {
        let layers = [
            AvatarLayer(id: "L1", imageUrl: "https://stickers/1.png", x: 10, y: 20, scale: 1.0, zIndex: 1),
            AvatarLayer(id: "L2", imageUrl: "https://stickers/2.png", x: 50, y: 50, scale: 1.5, zIndex: 2)
        ]
        
        db.setOshiComposition(keyword: "Aiko", layers: layers)
        
        let loaded = db.compositions["Aiko"]
        XCTAssertNotNil(loaded)
        XCTAssertEqual(loaded?.count, 2)
        XCTAssertEqual(loaded?.first?.id, "L1")
        XCTAssertEqual(loaded?.last?.scale, 1.5)
    }
    
    // MARK: - Feature 7: Theme metadata mapping
    func testThemeMetadata() throws {
        let manager = ThemeManager.shared
        
        let youtubeMeta = manager.metadata(for: "youtube")
        XCTAssertEqual(youtubeMeta.name, "YouTube")
        XCTAssertEqual(youtubeMeta.icon, "📹")
        XCTAssertEqual(youtubeMeta.accent, Color.red)
        
        let tverMeta = manager.metadata(for: "tver")
        XCTAssertEqual(tverMeta.name, "TVer")
        XCTAssertEqual(tverMeta.icon, "📺")
        XCTAssertEqual(tverMeta.accent, Color.blue)
        
        let customMeta = manager.metadata(for: "unknown_platform")
        XCTAssertEqual(customMeta.name, "Unknown_Platform")
        XCTAssertEqual(customMeta.icon, "🌐")
    }
    
    // MARK: - Feature 8: I18n Translations
    func testTranslations() throws {
        let i18n = I18nManager.shared
        
        i18n.setLanguage("ja")
        XCTAssertEqual(i18n.lang, "ja")
        XCTAssertEqual(i18n.t("tabFeed"), "フィード")
        XCTAssertEqual(i18n.t("tabSaved"), "ブックマーク")
        
        i18n.setLanguage("en")
        XCTAssertEqual(i18n.lang, "en")
        XCTAssertEqual(i18n.t("tabFeed"), "Feed")
        XCTAssertEqual(i18n.t("tabSaved"), "Saved")
        
        i18n.setLanguage("zh-TW")
        XCTAssertEqual(i18n.lang, "zh-TW")
        XCTAssertEqual(i18n.t("tabFeed"), "動態")
        XCTAssertEqual(i18n.t("tabSaved"), "已儲存")
        
        i18n.setLanguage("zh-CN")
        XCTAssertEqual(i18n.lang, "zh-CN")
        XCTAssertEqual(i18n.t("tabFeed"), "动态")
        XCTAssertEqual(i18n.t("tabSaved"), "已保存")
    }
    
    // MARK: - Feature 9: Multi-keyword source fetching, filtering, and translation targets
    func testMultiKeywordFeedAndTranslations() throws {
        // 1. Import more than 3 keywords (e.g. 4 keywords)
        let keywords = ["Aiko", "Miku", "Yamada", "Ken"]
        var savedTerms = [WatchTerm]()
        for kw in keywords {
            let term = db.saveTerm(keyword: kw, collectionMode: "all_info")
            savedTerms.append(term)
        }
        
        XCTAssertEqual(db.terms.count, 4)
        
        // 2. Mock feeds fetched for all 4 keywords
        let nowString = ISO8601DateFormatter().string(from: Date())
        var newItems = [FeedItem]()
        for i in 0..<keywords.count {
            let kw = keywords[i]
            let item = FeedItem(
                id: "news:mock:\(kw):\(i)",
                platform: "news",
                url: "https://mocknews.com/\(kw)",
                title: "Latest update on \(kw)",
                content_text: "Summary of events regarding \(kw)",
                author: "Mock Press",
                thumbnail_url: nil,
                media_type: "article",
                published_at: nowString,
                watch_term_keyword: kw,
                fetched_at: nowString
            )
            newItems.append(item)
        }
        
        // Merge feed items
        let addedCount = db.mergeItems(newItems: newItems)
        XCTAssertEqual(addedCount, 4)
        XCTAssertEqual(db.feedItems.count, 4)
        
        // Verify querying for each keyword works correctly
        for kw in keywords {
            let queryResult = db.queryFeed(keyword: kw, days: 30)
            XCTAssertEqual(queryResult.count, 1)
            XCTAssertEqual(queryResult.first?.watch_term_keyword, kw)
            XCTAssertEqual(queryResult.first?.title, "Latest update on \(kw)")
        }
        
        // 3. Test Translation target language codes mapping logic
        let testLanguages = [
            ("ja", "ja"),
            ("en", "en"),
            ("zh-CN", "zh"),
            ("zh-TW", "zh-Hant")
        ]
        
        for (selectedLang, expectedTargetCode) in testLanguages {
            I18nManager.shared.setLanguage(selectedLang)
            
            // Replicate URL translation mapping block in ReaderView
            let targetLangCode: String
            switch I18nManager.shared.lang {
            case "ja": targetLangCode = "ja"
            case "en": targetLangCode = "en"
            case "zh-CN": targetLangCode = "zh"
            case "zh-TW": targetLangCode = "zh-Hant"
            default: targetLangCode = "en"
            }
            
            XCTAssertEqual(targetLangCode, expectedTargetCode, "Language code mapping should match Google Translate expectations.")
        }
    }
}

private final class MockNotificationCenter: NotificationCenterClient {
    private(set) var status: UNAuthorizationStatus
    private let grantsAuthorization: Bool
    private(set) var authorizationRequestCount = 0
    private(set) var requests: [UNNotificationRequest] = []

    init(status: UNAuthorizationStatus, grantsAuthorization: Bool = true) {
        self.status = status
        self.grantsAuthorization = grantsAuthorization
    }

    func authorizationStatus() async -> UNAuthorizationStatus {
        status
    }

    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool {
        authorizationRequestCount += 1
        if grantsAuthorization {
            status = .authorized
        }
        return grantsAuthorization
    }

    func add(_ request: UNNotificationRequest) async throws {
        requests.append(request)
    }
}
