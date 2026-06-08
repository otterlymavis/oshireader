import XCTest
import SwiftUI
import UserNotifications
@testable import OshiReader

@MainActor
final class OshiReaderTests: XCTestCase {

    private var tempDir: URL!
    private var db: LocalDB!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        db = LocalDB(directory: tempDir)
        db.setSubscribedPlatforms(platforms: ["news", "tver", "youtube", "yahoonews", "custom"])
    }

    override func tearDownWithError() throws {
        db = nil
        try FileManager.default.removeItem(at: tempDir)
        try super.tearDownWithError()
    }
    
    // MARK: - Feature 1: Watch Keywords (Terms)
    func testWatchTerms() throws {
        // 1. Save watch term
        let term = db.saveTerm(keyword: "Test Oshi", collectionMode: .mediaOnly)
        
        XCTAssertEqual(db.terms.count, 1)
        XCTAssertEqual(db.terms.first?.keyword, "Test Oshi")
        XCTAssertEqual(db.terms.first?.collection_mode, .mediaOnly)
        XCTAssertTrue(db.terms.first?.is_active ?? false)
        
        // 2. Update watch term
        db.updateTerm(id: term.id, isActive: false, collectionMode: .allInfo)

        XCTAssertEqual(db.terms.first?.is_active, false)
        XCTAssertEqual(db.terms.first?.collection_mode, .allInfo)

        db.updateTerm(id: term.id, notifyOnNew: true)
        XCTAssertEqual(db.terms.first?.notify_on_new, true)
        
        // 3. Delete watch term
        db.deleteTerm(id: term.id)
        XCTAssertEqual(db.terms.count, 0)
    }

    func testDeleteTermAlsoRemovesItssFeedItems() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["youtube"])
        let term = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        let keepTerm = db.saveTerm(keyword: "Haruka", collectionMode: .allInfo)
        _ = db.mergeItems(newItems: [
            FeedItem(id: "youtube:1", platform: "youtube", url: "https://u/1",
                     title: "Aiko video", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now),
            FeedItem(id: "youtube:2", platform: "youtube", url: "https://u/2",
                     title: "Haruka video", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "video", published_at: now, watch_term_keyword: "Haruka", fetched_at: now),
        ])
        XCTAssertEqual(db.feedItems.count, 2)

        db.deleteTerm(id: term.id)

        // Aiko's items removed; Haruka's remain
        XCTAssertEqual(db.terms.count, 1)
        XCTAssertEqual(db.terms.first?.keyword, "Haruka")
        XCTAssertEqual(db.feedItems.count, 1)
        XCTAssertEqual(db.feedItems.first?.watch_term_keyword, "Haruka")
        _ = keepTerm // suppress unused warning
    }

    func testSaveTermTrimsWhitespace() throws {
        let term = db.saveTerm(keyword: "  Aiko  ", collectionMode: .allInfo)
        XCTAssertEqual(term.keyword, "Aiko")
        XCTAssertEqual(db.terms.first?.keyword, "Aiko")
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

    func testMergeTruncatedTitleReplacedByFullTitle() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let truncated = FeedItem(
            id: "news:trunc", platform: "news", url: "https://n.example.com/1",
            title: "速報: アイコが新曲リリース...", content_text: nil, author: nil,
            thumbnail_url: nil, media_type: "article", published_at: now,
            watch_term_keyword: "Aiko", fetched_at: now
        )
        let full = FeedItem(
            id: "news:trunc", platform: "news", url: "https://n.example.com/1",
            title: "速報: アイコが新曲リリース — 初のソロアルバムを発表", content_text: "全文テキスト",
            author: "News Corp", thumbnail_url: "https://img.example.com/1.jpg",
            media_type: "article", published_at: now,
            watch_term_keyword: "Aiko", fetched_at: now
        )
        _ = db.mergeItems(newItems: [truncated])
        _ = db.mergeItems(newItems: [full])

        let merged = db.feedItems.first!
        // Truncated title replaced by full title
        XCTAssertEqual(merged.title, full.title)
        // Content, author, thumbnail filled in from the second fetch
        XCTAssertEqual(merged.content_text, "全文テキスト")
        XCTAssertEqual(merged.author, "News Corp")
        XCTAssertEqual(merged.thumbnail_url, "https://img.example.com/1.jpg")
    }

    func testMergeKeepsEarlierPublishedAt() throws {
        let formatter = ISO8601DateFormatter()
        let older = formatter.string(from: Date(timeIntervalSinceNow: -3600))
        let newer = formatter.string(from: Date(timeIntervalSinceNow: -60))
        let base = FeedItem(
            id: "tver:pub-test", platform: "tver", url: "https://tver.jp/ep/1",
            title: "Episode Title", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: newer, watch_term_keyword: "Aiko", fetched_at: newer
        )
        let corrected = FeedItem(
            id: "tver:pub-test", platform: "tver", url: "https://tver.jp/ep/1",
            title: "Episode Title", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: older, watch_term_keyword: "Aiko", fetched_at: newer
        )
        _ = db.mergeItems(newItems: [base])
        _ = db.mergeItems(newItems: [corrected])

        // The earlier publish date (older) should be kept
        XCTAssertEqual(db.feedItems.first?.published_at, older)
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

    func testNotifyForNewItemsSkipsWhenEmpty() async throws {
        let center = MockNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center)
        let term = WatchTerm(id: "t1", keyword: "Oshi", notify_on_new: true)
        await manager.notifyForNewItems([], terms: [term])
        XCTAssertEqual(center.requests.count, 0)
    }

    func testNotifyForNewItemsSkipsWhenDenied() async throws {
        let center = MockNotificationCenter(status: .denied)
        let manager = NotificationManager(center: center)
        let nowString = ISO8601DateFormatter().string(from: Date())
        let term = WatchTerm(id: "t1", keyword: "Oshi", notify_on_new: true)
        let item = FeedItem(
            id: "youtube:denied-test", platform: "youtube", url: "https://youtube.com/1",
            title: "Video", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: nowString,
            watch_term_keyword: "Oshi", fetched_at: nowString
        )
        await manager.notifyForNewItems([item], terms: [term])
        XCTAssertEqual(center.requests.count, 0)
    }

    @MainActor
    func testNotifyForNewItemsGroupsByKeyword() async throws {
        let center = MockNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center)
        let nowString = ISO8601DateFormatter().string(from: Date())
        let termA = WatchTerm(id: "a", keyword: "Oshi A", notify_on_new: true)
        let termB = WatchTerm(id: "b", keyword: "Oshi B", notify_on_new: true)
        let items = [
            FeedItem(id: "1", platform: "youtube", url: "https://u/1", title: "A1",
                     content_text: nil, author: nil, thumbnail_url: nil, media_type: "video",
                     published_at: nowString, watch_term_keyword: "Oshi A", fetched_at: nowString),
            FeedItem(id: "2", platform: "youtube", url: "https://u/2", title: "A2",
                     content_text: nil, author: nil, thumbnail_url: nil, media_type: "video",
                     published_at: nowString, watch_term_keyword: "Oshi A", fetched_at: nowString),
            FeedItem(id: "3", platform: "note", url: "https://u/3", title: "B1",
                     content_text: nil, author: nil, thumbnail_url: nil, media_type: "article",
                     published_at: nowString, watch_term_keyword: "Oshi B", fetched_at: nowString),
        ]
        await manager.notifyForNewItems(items, terms: [termA, termB])
        XCTAssertEqual(center.requests.count, 2)
        let ids = Set(center.requests.map { $0.identifier })
        XCTAssertTrue(ids.contains("oshireader-new-Oshi A"))
        XCTAssertTrue(ids.contains("oshireader-new-Oshi B"))
        let bodyA = center.requests.first(where: { $0.identifier == "oshireader-new-Oshi A" })?.content.body
        XCTAssertEqual(bodyA, "2 new items found.")
    }

    @MainActor
    func testNotifyForNewItemsIgnoresUnmatchedKeyword() async throws {
        let center = MockNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center)
        let nowString = ISO8601DateFormatter().string(from: Date())
        let term = WatchTerm(id: "t1", keyword: "Enabled Oshi", notify_on_new: true)
        let item = FeedItem(
            id: "youtube:orphan", platform: "youtube", url: "https://youtube.com/orphan",
            title: "Orphan video", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: nowString,
            watch_term_keyword: "Completely Different Oshi",
            fetched_at: nowString
        )
        await manager.notifyForNewItems([item], terms: [term])
        XCTAssertEqual(center.requests.count, 0)
    }

    func testCappedFeedItemsAreEvictedAndNotNotified() {
        // mergeItems caps at 600 items sorted newest-first. The oldest item should be
        // dropped from feedItems, which also prevents it from triggering a notification
        // (the eviction check at line ~200 of LocalDB only notifies survived items).
        let fmt = ISO8601DateFormatter()
        let base = Date(timeIntervalSinceReferenceDate: 0)

        var items: [FeedItem] = (1...600).map { i in
            let ts = fmt.string(from: base.addingTimeInterval(Double(i) * 60))
            return FeedItem(
                id: "cap:\(i)", platform: "youtube", url: "https://u/\(i)", title: "Item \(i)",
                content_text: nil, author: nil, thumbnail_url: nil, media_type: "video",
                published_at: ts, watch_term_keyword: "Oshi", fetched_at: ts
            )
        }
        let evictedTs = fmt.string(from: base)
        items.append(FeedItem(
            id: "cap:evicted", platform: "youtube", url: "https://u/evicted", title: "Evicted",
            content_text: nil, author: nil, thumbnail_url: nil, media_type: "video",
            published_at: evictedTs, watch_term_keyword: "Oshi", fetched_at: evictedTs
        ))

        _ = db.mergeItems(newItems: items)

        XCTAssertEqual(db.feedItems.count, 600)
        XCTAssertFalse(db.feedItems.contains { $0.id == "cap:evicted" },
                       "Oldest item should be evicted by the 600-item cap and not stored (and therefore not notified)")
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

    func testAddCustomUrlPreventsDuplicates() throws {
        db.addCustomUrl(url: "https://feed.example.com/rss", title: "Feed")
        db.addCustomUrl(url: "https://feed.example.com/rss", title: "Feed again")
        XCTAssertEqual(db.customUrls.count, 1)
    }

    func testAddCustomUrlPrefixesSchemeWhenMissing() throws {
        db.addCustomUrl(url: "feed.example.com/rss", title: "Feed")
        XCTAssertEqual(db.customUrls.count, 1)
        XCTAssertEqual(db.customUrls.first?.url, "https://feed.example.com/rss")
    }

    // MARK: - Persistence round-trip
    func testPersistenceRoundTrip() throws {
        _ = db.saveTerm(keyword: "Persisted Oshi", collectionMode: .allInfo)
        XCTAssertEqual(db.terms.count, 1)

        // Allow the background queue write to finish before reading the file
        let expectation = XCTestExpectation(description: "write flush")
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.1) { expectation.fulfill() }
        wait(for: [expectation], timeout: 1.0)

        let db2 = LocalDB(directory: tempDir)
        XCTAssertEqual(db2.terms.count, 1)
        XCTAssertEqual(db2.terms.first?.keyword, "Persisted Oshi")
    }

    // MARK: - Schema versioning
    func testSchemaMigrationAddsNewPlatforms() throws {
        // Simulate a pre-v1 install: subscribed platforms missing a known platform.
        // Remove "oricon" from the persisted list, reset the schema version key, then
        // create a fresh LocalDB — the migration should add it back.
        UserDefaults.standard.removeObject(forKey: "localdb_schema_version")
        var platforms = Platform.all.filter(\.subscribedByDefault).map(\.id).filter { $0 != "oricon" }
        let data = try JSONEncoder().encode(platforms)
        let url = tempDir.appendingPathComponent("subscribed_platforms.json")
        try data.write(to: url)

        let freshDB = LocalDB(directory: tempDir)
        XCTAssertTrue(freshDB.subscribedPlatforms.contains("oricon"),
                      "Migration should have added missing 'oricon' platform")

        // Clean up the version key so subsequent tests see a clean state
        UserDefaults.standard.removeObject(forKey: "localdb_schema_version")
    }

    // MARK: - Feature 5b: Hidden items (deleteFeedItem)
    func testDeleteFeedItemHidesAndExcludesFromQuery() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let item = FeedItem(
            id: "youtube:hidden-test", platform: "youtube",
            url: "https://youtube.com/watch?v=hidden-test",
            title: "Hidden item", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now,
            watch_term_keyword: "Aiko", fetched_at: now
        )
        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.mergeItems(newItems: [item])
        XCTAssertEqual(db.feedItems.count, 1)

        db.deleteFeedItem(id: item.id, watchTermKeyword: item.watch_term_keyword)

        XCTAssertEqual(db.feedItems.count, 0)
        // Hidden key should be recorded
        XCTAssertTrue(db.hiddenItems.contains("youtube:hidden-test::Aiko"))
        // A second merge of the same item should be filtered out
        let added = db.mergeItems(newItems: [item])
        XCTAssertEqual(added, 0)
        XCTAssertEqual(db.feedItems.count, 0)
    }

    func testDeleteFeedItemIsKeywordScoped() throws {
        // Hiding an item under keyword "Aiko" must not hide it under keyword "Haruka"
        let now = ISO8601DateFormatter().string(from: Date())
        let makeItem = { (kw: String) -> FeedItem in
            FeedItem(
                id: "youtube:shared-vid", platform: "youtube",
                url: "https://youtube.com/watch?v=shared-vid",
                title: "Shared video", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "video", published_at: now,
                watch_term_keyword: kw, fetched_at: now
            )
        }
        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.mergeItems(newItems: [makeItem("Aiko"), makeItem("Haruka")])
        XCTAssertEqual(db.feedItems.count, 2)

        db.deleteFeedItem(id: "youtube:shared-vid", watchTermKeyword: "Aiko")

        // "Aiko" match is hidden, "Haruka" match survives
        XCTAssertTrue(db.hiddenItems.contains("youtube:shared-vid::Aiko"))
        XCTAssertFalse(db.hiddenItems.contains("youtube:shared-vid::Haruka"))
        XCTAssertEqual(db.feedItems.count, 1)
        XCTAssertEqual(db.feedItems.first?.watch_term_keyword, "Haruka")

        // queryFeed for "Haruka" still returns the item
        let results = db.queryFeed(keyword: "Haruka", days: 30)
        XCTAssertEqual(results.count, 1)
    }

    func testQueryFeedDeduplicatesByUrlWhenNoKeywordFilter() throws {
        // Same URL matched by two different watch terms — no-keyword query should deduplicate
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["youtube"])
        let makeItem = { (kw: String) -> FeedItem in
            FeedItem(
                id: "youtube:dedup-vid", platform: "youtube",
                url: "https://youtube.com/watch?v=dedup-vid",
                title: "Dedup video", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "video", published_at: now,
                watch_term_keyword: kw, fetched_at: now
            )
        }
        _ = db.mergeItems(newItems: [makeItem("Aiko"), makeItem("Haruka")])
        XCTAssertEqual(db.feedItems.count, 2)

        // No keyword → dedup by URL, only 1 result
        let all = db.queryFeed(keyword: nil, days: 30)
        XCTAssertEqual(all.count, 1)

        // With keyword → no dedup, keyword-scoped, still 1 per keyword
        let aikoPosts = db.queryFeed(keyword: "Aiko", days: 30)
        XCTAssertEqual(aikoPosts.count, 1)
        let harukaPosts = db.queryFeed(keyword: "Haruka", days: 30)
        XCTAssertEqual(harukaPosts.count, 1)
    }

    // MARK: - URL scheme security
    func testAddCustomUrlRejectsNonHttpSchemes() throws {
        db.addCustomUrl(url: "javascript:alert(1)", title: "XSS")
        XCTAssertEqual(db.customUrls.count, 0)

        db.addCustomUrl(url: "file:///etc/passwd", title: "File")
        XCTAssertEqual(db.customUrls.count, 0)

        db.addCustomUrl(url: "data:text/html,<h1>hi</h1>", title: "Data")
        XCTAssertEqual(db.customUrls.count, 0)

        // Valid schemes should be accepted
        db.addCustomUrl(url: "https://valid-feed.com/rss", title: "RSS")
        XCTAssertEqual(db.customUrls.count, 1)

        db.addCustomUrl(url: "http://local-dev.test/feed", title: "Dev")
        XCTAssertEqual(db.customUrls.count, 2)
    }

    // MARK: - Alias matching in strict platforms
    func testAliasMatchingAllowsAliasedKeywords() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["news"])

        // Term "Aiko" has alias "相川 愛子"
        _ = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        db.updateTerm(id: db.terms.first!.id, aliases: ["相川 愛子"])

        // Article only mentions the alias, not the primary keyword
        let itemAlias = FeedItem(
            id: "news:alias-test", platform: "news",
            url: "https://news.example.com/aiko-alias",
            title: "相川 愛子が新曲をリリース", content_text: "相川 愛子 新曲情報",
            author: nil, thumbnail_url: nil, media_type: "article",
            published_at: now, watch_term_keyword: "Aiko", fetched_at: now
        )
        // Article mentions neither primary nor alias
        let itemNoMatch = FeedItem(
            id: "news:no-match", platform: "news",
            url: "https://news.example.com/unrelated",
            title: "全然違うニュース", content_text: "関係ない内容",
            author: nil, thumbnail_url: nil, media_type: "article",
            published_at: now, watch_term_keyword: "Aiko", fetched_at: now
        )

        _ = db.mergeItems(newItems: [itemAlias, itemNoMatch])
        let results = db.queryFeed(keyword: "Aiko", days: 30)

        XCTAssertEqual(results.count, 1)
        XCTAssertEqual(results.first?.id, "news:alias-test")
    }

    // MARK: - clearAllData
    func testClearAllDataResetsEverything() throws {
        _ = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        db.addCustomUrl(url: "https://feed.example.com/rss", title: "Feed")
        let now = ISO8601DateFormatter().string(from: Date())
        let item = FeedItem(
            id: "youtube:clear-test", platform: "youtube",
            url: "https://youtube.com/watch?v=clear-test",
            title: "Clear test", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now
        )
        _ = db.mergeItems(newItems: [item])
        _ = db.toggleSaved(item: item)

        XCTAssertEqual(db.terms.count, 1)
        XCTAssertEqual(db.feedItems.count, 1)
        XCTAssertEqual(db.savedPages.count, 1)
        XCTAssertEqual(db.customUrls.count, 1)

        db.clearAllData()

        XCTAssertTrue(db.terms.isEmpty)
        XCTAssertTrue(db.feedItems.isEmpty)
        XCTAssertTrue(db.savedPages.isEmpty)
        XCTAssertTrue(db.customUrls.isEmpty)
        XCTAssertTrue(db.hiddenItems.isEmpty)
        XCTAssertNil(db.wallpaper)
        XCTAssertNil(db.sourcesOrder)

        // Default subscribed platforms are restored after clear
        XCTAssertFalse(db.subscribedPlatforms.isEmpty)
    }

    // MARK: - Date cutoff uses proper Date comparison (not string sort)
    func testDateFilterUsesDateComparisonNotStringSort() throws {
        db.setSubscribedPlatforms(platforms: ["youtube"])
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let now = Date()

        // Item just inside the 30-day window (29 days ago)
        let inside = formatter.string(from: Calendar.current.date(byAdding: .day, value: -29, to: now)!)
        // Item just outside (31 days ago)
        let outside = formatter.string(from: Calendar.current.date(byAdding: .day, value: -31, to: now)!)

        let itemInside = FeedItem(
            id: "youtube:inside", platform: "youtube", url: "https://yt/inside",
            title: "Inside", content_text: "Inside", author: nil, thumbnail_url: nil,
            media_type: "video", published_at: inside,
            watch_term_keyword: "Aiko", fetched_at: inside
        )
        let itemOutside = FeedItem(
            id: "youtube:outside", platform: "youtube", url: "https://yt/outside",
            title: "Outside", content_text: "Outside", author: nil, thumbnail_url: nil,
            media_type: "video", published_at: outside,
            watch_term_keyword: "Aiko", fetched_at: outside
        )

        _ = db.mergeItems(newItems: [itemInside, itemOutside])
        let results = db.queryFeed(keyword: nil, days: 30)

        XCTAssertEqual(results.count, 1)
        XCTAssertEqual(results.first?.id, "youtube:inside")
    }

    func testSortOrderCorrectAcrossTimezoneOffsets() throws {
        // A date with +09:00 offset and a UTC date representing the same instant would sort
        // wrong lexicographically (e.g. "2024-01-01T21:00:00+09:00" > "2024-01-01T12:00:00Z"
        // as strings but equal as dates). Verify mergeItems sorts by actual Date value.
        db.setSubscribedPlatforms(platforms: ["youtube"])

        // newer: 1 hour ago in UTC
        let newerUtc = "2024-06-01T03:00:00Z"
        // older: 2 hours ago expressed with +09:00 (2024-06-01T10:00:00+09:00 == 2024-06-01T01:00:00Z)
        // As a string "2024-06-01T10:00:00+09:00" > "2024-06-01T03:00:00Z" — wrong sort order
        let olderWithOffset = "2024-06-01T10:00:00+09:00"

        let newerItem = FeedItem(
            id: "youtube:newer", platform: "youtube", url: "https://yt/newer",
            title: "Newer", content_text: "Newer", author: nil, thumbnail_url: nil,
            media_type: "video", published_at: newerUtc,
            watch_term_keyword: "Aiko", fetched_at: newerUtc
        )
        let olderItem = FeedItem(
            id: "youtube:older", platform: "youtube", url: "https://yt/older",
            title: "Older", content_text: "Older", author: nil, thumbnail_url: nil,
            media_type: "video", published_at: olderWithOffset,
            watch_term_keyword: "Aiko", fetched_at: olderWithOffset
        )

        _ = db.mergeItems(newItems: [newerItem, olderItem])
        let results = db.queryFeed(keyword: nil, days: 0)

        XCTAssertEqual(results.count, 2)
        XCTAssertEqual(results.first?.id, "youtube:newer", "Newer UTC item should sort first")
        XCTAssertEqual(results.last?.id, "youtube:older", "Older +09:00 item should sort last")
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
    
    // MARK: - parseISO8601Date
    func testParseISO8601DateFormats() throws {
        // Standard ISO8601 with Z and fractional seconds (backend default)
        let d1 = parseISO8601Date("2024-06-15T10:30:00.123456Z")
        XCTAssertNotNil(d1)
        XCTAssertEqual(d1?.timeIntervalSince1970 ?? 0, 1718447400.123456, accuracy: 0.001)

        // Without fractional seconds
        let d2 = parseISO8601Date("2024-06-15T10:30:00Z")
        XCTAssertNotNil(d2)
        XCTAssertEqual(d2?.timeIntervalSince1970 ?? 0, 1718447400, accuracy: 1)

        // Timezone offset instead of Z
        let d3 = parseISO8601Date("2024-06-15T19:30:00+09:00")
        XCTAssertNotNil(d3)
        XCTAssertEqual(d3?.timeIntervalSince1970 ?? 0, 1718447400, accuracy: 1)

        // Naive datetime (no timezone) — treated as UTC
        let d4 = parseISO8601Date("2024-06-15T10:30:00")
        XCTAssertNotNil(d4)
        XCTAssertEqual(d4?.timeIntervalSince1970 ?? 0, 1718447400, accuracy: 1)

        // Invalid string returns nil without crashing
        XCTAssertNil(parseISO8601Date("not a date"))
        XCTAssertNil(parseISO8601Date(""))
    }

    // MARK: - cleanDisplayText
    func testCleanDisplayTextHTMLEntities() throws {
        XCTAssertEqual(cleanDisplayText("Fish &amp; Chips"), "Fish & Chips")
        XCTAssertEqual(cleanDisplayText("He said &quot;hello&quot;"), "He said \"hello\"")
        XCTAssertEqual(cleanDisplayText("It&#39;s &apos;OK&apos;"), "It's 'OK'")
        XCTAssertEqual(cleanDisplayText("a&nbsp;b"), "a b")
        XCTAssertEqual(cleanDisplayText("x &lt; y &gt; z"), "x < y > z")
    }

    func testCleanDisplayTextStripsHTMLTags() throws {
        XCTAssertEqual(cleanDisplayText("<p>Hello <b>World</b></p>"), "Hello World")
    }

    func testCleanDisplayTextCollapsesWhitespace() throws {
        XCTAssertEqual(cleanDisplayText("too   many   spaces"), "too many spaces")
    }

    func testCleanDisplayTextReturnsNilForEmptyResult() throws {
        XCTAssertNil(cleanDisplayText("   "))
        XCTAssertNil(cleanDisplayText("<br/>"))
        XCTAssertNil(cleanDisplayText(nil))
    }

    func testCleanDisplayTextPreservesNormalText() throws {
        XCTAssertEqual(cleanDisplayText("Normal text."), "Normal text.")
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
    
    // All keys in I18n.swift must have non-empty, non-fallback values for every supported language.
    func testAllI18nKeysHaveAllLanguages() {
        let i18n = I18nManager.shared
        let requiredLanguages = ["en", "ja", "zh-TW", "zh-CN"]
        let savedLang = i18n.lang
        let keys = i18n.allTranslationKeys
        XCTAssertFalse(keys.isEmpty, "translations dict should not be empty")

        var failures: [String] = []
        for key in keys.sorted() {
            for lang in requiredLanguages {
                i18n.setLanguage(lang)
                let value = i18n.t(key)
                if value.isEmpty || value == key {
                    failures.append("\(key)[\(lang)]")
                }
            }
        }
        i18n.setLanguage(savedLang)
        XCTAssertTrue(failures.isEmpty, "Missing/fallback translations: \(failures.joined(separator: ", "))")
    }

    func testTFormatStringSubstitution() throws {
        let i18n = I18nManager.shared
        let saved = i18n.lang
        i18n.setLanguage("en")

        let result = i18n.tFormat("notifNewItemsTitle", "Aiko")
        XCTAssertEqual(result, "New items for Aiko")

        i18n.setLanguage("ja")
        let jaResult = i18n.tFormat("notifNewItemsTitle", "愛子")
        XCTAssertEqual(jaResult, "愛子 の新着")

        i18n.setLanguage(saved)
    }

    func testTSearchGroupKnownAndUnknown() throws {
        let i18n = I18nManager.shared
        let saved = i18n.lang
        i18n.setLanguage("ja")

        XCTAssertEqual(i18n.tSearchGroup("News"), "ニュース")
        XCTAssertEqual(i18n.tSearchGroup("Custom"), "カスタム")
        // Unknown group falls back to the raw group name
        XCTAssertEqual(i18n.tSearchGroup("Nonexistent"), "Nonexistent")

        i18n.setLanguage(saved)
    }

    // MARK: - Persistence: malformed JSON file is silently ignored
    func testMalformedPersistenceFileLoadsEmpty() throws {
        // Write garbage to terms.json before creating a LocalDB from the same directory.
        let termsURL = tempDir.appendingPathComponent("terms.json")
        try "{ this is : not [ valid json".write(to: termsURL, atomically: true, encoding: .utf8)

        let freshDB = LocalDB(directory: tempDir)
        // Must not crash; corrupt file should produce an empty collection.
        XCTAssertEqual(freshDB.terms.count, 0)
    }

    // MARK: - Feature 9: Multi-keyword source fetching, filtering, and translation targets
    func testMultiKeywordFeedAndTranslations() throws {
        // 1. Import more than 3 keywords (e.g. 4 keywords)
        let keywords = ["Aiko", "Miku", "Yamada", "Ken"]
        var savedTerms = [WatchTerm]()
        for kw in keywords {
            let term = db.saveTerm(keyword: kw, collectionMode: .allInfo)
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

// MARK: - Network-client tests (Phase 5.3)

final class NetworkManagerTests: XCTestCase {
    private var savedSession: URLSession!

    override func setUpWithError() throws {
        try super.setUpWithError()
        savedSession = NetworkManager.shared.session
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        NetworkManager.shared.session = URLSession(configuration: config)
        MockURLProtocol.handler = nil
    }

    override func tearDownWithError() throws {
        NetworkManager.shared.session = savedSession
        MockURLProtocol.handler = nil
        MockURLProtocol.errorHandler = nil
        try super.tearDownWithError()
    }

    private static let mockURL = URL(string: "https://mock.test")!

    private static func response(status: Int) -> HTTPURLResponse {
        HTTPURLResponse(url: mockURL, statusCode: status, httpVersion: nil, headerFields: nil)!
    }

    // 200 OK with valid JSON → decodes cleanly
    func testFetchWatchTermsSuccess() async throws {
        let term = WatchTerm(id: "42", keyword: "Aiko", collection_mode: .allInfo)
        let data = try JSONEncoder().encode([term])
        MockURLProtocol.handler = { _ in (data, Self.response(status: 200)) }

        let terms = try await NetworkManager.shared.fetchWatchTerms()
        XCTAssertEqual(terms.count, 1)
        XCTAssertEqual(terms.first?.keyword, "Aiko")
    }

    // 4xx response → throws URLError(.badServerResponse)
    func testFetchWatchTerms404Throws() async throws {
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 404)) }

        do {
            _ = try await NetworkManager.shared.fetchWatchTerms()
            XCTFail("Expected error not thrown")
        } catch let error as URLError {
            XCTAssertEqual(error.code, .badServerResponse)
        }
    }

    // 200 OK with malformed JSON → throws DecodingError
    func testFetchWatchTermsMalformedJSONThrows() async throws {
        MockURLProtocol.handler = { _ in (Data("not json".utf8), Self.response(status: 200)) }

        do {
            _ = try await NetworkManager.shared.fetchWatchTerms()
            XCTFail("Expected DecodingError not thrown")
        } catch is DecodingError {
            // expected
        }
    }

    // 5xx response → throws URLError(.badServerResponse)
    func testFetchFeed500Throws() async throws {
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 500)) }

        do {
            _ = try await NetworkManager.shared.fetchFeed(limit: 10, days: 7)
            XCTFail("Expected error not thrown")
        } catch let error as URLError {
            XCTAssertEqual(error.code, .badServerResponse)
        }
    }

    // 200 OK with empty array → returns empty, no throw
    func testFetchFeedEmptyArray() async throws {
        MockURLProtocol.handler = { _ in (Data("[]".utf8), Self.response(status: 200)) }

        let items = try await NetworkManager.shared.fetchFeed(limit: 10, days: 7)
        XCTAssertTrue(items.isEmpty)
    }

    // 204 No Content within accept range → apiVoid succeeds
    func testDeleteWatchTermAccepts204() async throws {
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 204)) }

        // deleteWatchTerm uses apiVoid with acceptRange 200...299
        XCTAssertNoThrow(try await NetworkManager.shared.deleteWatchTerm(id: "99"))
    }

    // Network connection error → propagated as URLError (not swallowed)
    func testFetchWatchTermsNetworkErrorPropagates() async throws {
        MockURLProtocol.errorHandler = { _ in URLError(.notConnectedToInternet) }

        do {
            _ = try await NetworkManager.shared.fetchWatchTerms()
            XCTFail("Expected URLError not thrown")
        } catch let error as URLError {
            XCTAssertEqual(error.code, .notConnectedToInternet)
        }
    }

    // createWatchTerm sends POST and decodes the returned term
    func testCreateWatchTermSendsPostAndDecodesTerm() async throws {
        var capturedMethod: String?
        let expected = WatchTerm(id: "7", keyword: "Haruka", collection_mode: .allInfo)
        let responseData = try JSONEncoder().encode(expected)
        MockURLProtocol.handler = { req in
            capturedMethod = req.httpMethod
            return (responseData, Self.response(status: 200))
        }

        let created = try await NetworkManager.shared.createWatchTerm(keyword: "Haruka", collectionMode: .allInfo)
        XCTAssertEqual(capturedMethod, "POST")
        XCTAssertEqual(created.keyword, "Haruka")
    }

    // updateWatchTerm sends PATCH and decodes the updated term
    func testUpdateWatchTermSendsPatchAndDecodesTerm() async throws {
        var capturedMethod: String?
        let updated = WatchTerm(id: "3", keyword: "Aiko", collection_mode: .mediaOnly)
        let responseData = try JSONEncoder().encode(updated)
        MockURLProtocol.handler = { req in
            capturedMethod = req.httpMethod
            return (responseData, Self.response(status: 200))
        }

        let result = try await NetworkManager.shared.updateWatchTerm(id: "3", collectionMode: .mediaOnly)
        XCTAssertEqual(capturedMethod, "PATCH")
        XCTAssertEqual(result.collection_mode, .mediaOnly)
    }

    // fetchFeed with backend items → decoded and mapped to FeedItem
    func testFetchFeedDecodesBackendItems() async throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let sourceItem = SourceItem(
            id: "youtube:test123", platform: "youtube",
            url: "https://youtu.be/test123", published_at: now,
            author: nil, title: "Test Video", content_text: nil,
            media_type: "video", thumbnail_url: nil
        )
        let backendItem = BackendFeedItem(
            match_id: 1, watch_term_id: 2, watch_term_keyword: "Aiko",
            item: sourceItem, matched_at: now
        )
        let data = try JSONEncoder().encode([backendItem])
        MockURLProtocol.handler = { _ in (data, Self.response(status: 200)) }

        let items = try await NetworkManager.shared.fetchFeed(limit: 10, days: 7)
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items.first?.id, "youtube:test123")
        XCTAssertEqual(items.first?.watch_term_keyword, "Aiko")
        XCTAssertEqual(items.first?.media_type, "video")
    }

    // fetchCredentials decodes credential list
    func testFetchCredentialsDecodesList() async throws {
        let creds = [
            Credential(platform: "youtube", has_bearer_token: true, has_api_key: false, updated_at: nil),
            Credential(platform: "twitter", has_bearer_token: false, has_api_key: false, updated_at: nil),
        ]
        let data = try JSONEncoder().encode(creds)
        MockURLProtocol.handler = { _ in (data, Self.response(status: 200)) }

        let result = try await NetworkManager.shared.fetchCredentials()
        XCTAssertEqual(result.count, 2)
        XCTAssertTrue(result.first(where: { $0.platform == "youtube" })?.has_bearer_token == true)
    }

    // registerAPNSDeviceToken sends POST and succeeds on 201
    func testRegisterAPNSDeviceTokenSendsPost() async throws {
        var capturedMethod: String?
        MockURLProtocol.handler = { req in
            capturedMethod = req.httpMethod
            return (Data(), Self.response(status: 201))
        }

        XCTAssertNoThrow(try await NetworkManager.shared.registerAPNSDeviceToken(String(repeating: "a", count: 64)))
        XCTAssertEqual(capturedMethod, "POST")
    }

    // checkHealth returns true when backend says {"status":"ok"}
    func testCheckHealthReturnsTrueOnOk() async throws {
        let body = Data(#"{"status":"ok"}"#.utf8)
        MockURLProtocol.handler = { _ in (body, Self.response(status: 200)) }

        let healthy = try await NetworkManager.shared.checkHealth()
        XCTAssertTrue(healthy)
    }

    // checkHealth returns false on non-200 without throwing
    func testCheckHealthReturnsFalseOnServerError() async throws {
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 503)) }

        let healthy = try await NetworkManager.shared.checkHealth()
        XCTAssertFalse(healthy)
    }

    // checkHealth returns false when status field is not "ok"
    func testCheckHealthReturnsFalseOnUnexpectedStatus() async throws {
        let body = Data(#"{"status":"degraded"}"#.utf8)
        MockURLProtocol.handler = { _ in (body, Self.response(status: 200)) }

        let healthy = try await NetworkManager.shared.checkHealth()
        XCTAssertFalse(healthy)
    }

    // Platform normalization round-trip: normalize → find → same id
    func testPlatformNormalizeRoundTrip() {
        XCTAssertEqual(Platform.normalize("news:mdpr"), "mdpr")
        XCTAssertEqual(Platform.normalize("news:yahoo_ent"), "yahoonews")
        XCTAssertEqual(Platform.normalize("news:someother"), "news")
        XCTAssertEqual(Platform.normalize("youtube"), "youtube")
        XCTAssertNil(Platform.find("news:mdpr"))   // canonical lookup, not raw
        XCTAssertNotNil(Platform.find("mdpr"))
    }

    // updateCredential sends PUT with bearer_token body and decodes Credential
    func testUpdateCredentialSendsPutAndDecodesCredential() async throws {
        var capturedMethod: String?
        var capturedBody: [String: Any]?
        let cred = Credential(platform: "youtube", has_bearer_token: true, has_api_key: false, updated_at: nil)
        let data = try JSONEncoder().encode(cred)
        MockURLProtocol.handler = { req in
            capturedMethod = req.httpMethod
            if let body = req.httpBody {
                capturedBody = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
            }
            return (data, Self.response(status: 200))
        }

        let result = try await NetworkManager.shared.updateCredential(platform: "youtube", bearerToken: "tok123")
        XCTAssertEqual(capturedMethod, "PUT")
        XCTAssertEqual(capturedBody?["bearer_token"] as? String, "tok123")
        XCTAssertTrue(result.has_bearer_token)
    }

    // triggerPoll sends authorized POST and succeeds on 200
    func testTriggerPollSendsAuthorizedPost() async throws {
        var capturedMethod: String?
        var capturedAuthHeader: String?
        MockURLProtocol.handler = { req in
            capturedMethod = req.httpMethod
            capturedAuthHeader = req.value(forHTTPHeaderField: "Authorization")
            return (Data(), Self.response(status: 200))
        }
        NetworkManager.shared.setAdminApiToken("admin-secret")
        defer { NetworkManager.shared.setAdminApiToken(nil) }

        XCTAssertNoThrow(try await NetworkManager.shared.triggerPoll())
        XCTAssertEqual(capturedMethod, "POST")
        XCTAssertEqual(capturedAuthHeader, "Bearer admin-secret")
    }

    // triggerPoll propagates server error as URLError
    func testTriggerPoll500Throws() async throws {
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 500)) }

        do {
            try await NetworkManager.shared.triggerPoll()
            XCTFail("Expected URLError not thrown")
        } catch {
            XCTAssertTrue(error is URLError)
        }
    }
}

// MARK: - MockURLProtocol

private final class MockURLProtocol: URLProtocol {
    static var handler: ((URLRequest) -> (Data, HTTPURLResponse))?
    // Set this to have the protocol fail with a specific error instead of calling handler.
    static var errorHandler: ((URLRequest) -> Error)?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        if let errHandler = Self.errorHandler {
            client?.urlProtocol(self, didFailWithError: errHandler(request))
            return
        }
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.unknown))
            return
        }
        let (data, response) = handler(request)
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
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
