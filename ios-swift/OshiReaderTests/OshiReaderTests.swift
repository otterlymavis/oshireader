import XCTest
import SwiftUI
import UIKit
import UserNotifications
import BackgroundTasks
@testable import OshiReader

@MainActor
final class OshiReaderTests: XCTestCase {

    func testFeedThumbnailLoaderDownsamplesLargeImages() throws {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 800, height: 400))
        let data = renderer.pngData { context in
            UIColor.systemBlue.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 800, height: 400))
        }

        let image = try XCTUnwrap(
            FeedThumbnailLoader.downsample(data: data, maxPixelSize: 144)
        )

        XCTAssertLessThanOrEqual(max(image.size.width, image.size.height), 144)
    }

    func testFeedThumbnailLoaderCoalescesConcurrentRequests() async throws {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 800, height: 400))
        let data = renderer.pngData { context in
            UIColor.systemBlue.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 800, height: 400))
        }

        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let session = URLSession(configuration: config)
        let loader = FeedThumbnailLoader(session: session, maxPixelSize: 144)
        let url = try XCTUnwrap(URL(string: "https://thumbnail.test/image.png"))

        let lock = NSLock()
        var requestCount = 0
        MockURLProtocol.handler = { request in
            lock.lock()
            requestCount += 1
            lock.unlock()
            Thread.sleep(forTimeInterval: 0.1)
            let response = HTTPURLResponse(
                url: request.url ?? url,
                statusCode: 200,
                httpVersion: nil,
                headerFields: nil
            )!
            return (data, response)
        }
        defer {
            MockURLProtocol.handler = nil
            MockURLProtocol.errorHandler = nil
            session.invalidateAndCancel()
        }

        async let first = loader.image(for: url)
        async let second = loader.image(for: url)
        let images = await [first, second]

        XCTAssertEqual(requestCount, 1)
        XCTAssertNotNil(images[0])
        XCTAssertNotNil(images[1])
    }

    func testFeedThumbnailLoaderUsesMemoryCacheForSequentialRequests() async throws {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 800, height: 400))
        let data = renderer.pngData { context in
            UIColor.systemBlue.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 800, height: 400))
        }

        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let session = URLSession(configuration: config)
        let loader = FeedThumbnailLoader(session: session, maxPixelSize: 144)
        let url = try XCTUnwrap(URL(string: "https://thumbnail.test/cached.png"))

        let lock = NSLock()
        var requestCount = 0
        MockURLProtocol.handler = { request in
            lock.lock()
            requestCount += 1
            lock.unlock()
            let response = HTTPURLResponse(
                url: request.url ?? url,
                statusCode: 200,
                httpVersion: nil,
                headerFields: nil
            )!
            return (data, response)
        }
        defer {
            MockURLProtocol.handler = nil
            MockURLProtocol.errorHandler = nil
            session.invalidateAndCancel()
        }

        let first = await loader.image(for: url)
        let second = await loader.image(for: url)

        XCTAssertEqual(requestCount, 1)
        XCTAssertNotNil(first)
        XCTAssertNotNil(second)
    }

    private var tempDir: URL!
    private var db: LocalDB!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        db = LocalDB(directory: tempDir)
        db.setSubscribedPlatforms(platforms: ["news", "tver", "youtube", "yahoonews", "custom"])
        BackgroundRefreshPolicy.clearRecordedRefreshCompletionsForTesting()
    }

    override func tearDownWithError() throws {
        BackgroundRefreshPolicy.clearRecordedRefreshCompletionsForTesting()
        db = nil
        try? FileManager.default.removeItem(at: tempDir)
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
                     media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now, source: "youtube_api"),
            FeedItem(id: "youtube:2", platform: "youtube", url: "https://u/2",
                     title: "Haruka video", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "video", published_at: now, watch_term_keyword: "Haruka", fetched_at: now, source: "youtube_api"),
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

    func testDeleteTermByKeywordReturnsDeletedTermAndRemovesFeedItems() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["youtube"])
        let term = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        _ = db.mergeItems(newItems: [
            FeedItem(id: "youtube:1", platform: "youtube", url: "https://u/1",
                     title: "Aiko video", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now, source: "youtube_api")
        ])

        let deleted = db.deleteTerm(keyword: "Aiko")

        XCTAssertEqual(deleted?.id, term.id)
        XCTAssertTrue(db.terms.isEmpty)
        XCTAssertTrue(db.feedItems.isEmpty)
    }

    func testDeleteTermAlsoRemovesFeedItemsMatchingBackendWatchTermID() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let term = WatchTerm(id: "42", keyword: "Aiko", collection_mode: .allInfo)
        db.addTermFromBackend(term)
        db.feedItems = [
            FeedItem(id: "youtube:1", platform: "youtube", url: "https://u/1",
                     title: "Aiko video", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "video", published_at: now, watch_term_keyword: "Aiko old",
                     watch_term_id: 42, fetched_at: now, source: "youtube_api"),
            FeedItem(id: "youtube:2", platform: "youtube", url: "https://u/2",
                     title: "Haruka video", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "video", published_at: now, watch_term_keyword: "Haruka",
                     watch_term_id: 43, fetched_at: now, source: "youtube_api"),
        ]

        db.deleteTerm(id: term.id)

        XCTAssertEqual(db.feedItems.map(\.id), ["youtube:2"])
    }

    func testQueryFeedExcludesCachedItemsForDeletedTerms() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.saveTerm(keyword: "Haruka", collectionMode: .allInfo)
        db.feedItems = [
            FeedItem(id: "youtube:1", platform: "youtube", url: "https://u/1",
                     title: "Aiko video", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "video", published_at: now, watch_term_keyword: "Aiko",
                     fetched_at: now, source: "youtube_api"),
            FeedItem(id: "youtube:2", platform: "youtube", url: "https://u/2",
                     title: "Haruka video", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "video", published_at: now, watch_term_keyword: "Haruka",
                     fetched_at: now, source: "youtube_api"),
        ]

        let results = db.queryFeed(keyword: nil, days: 30)

        XCTAssertEqual(results.map(\.watch_term_keyword), ["Haruka"])
    }

    func testMergePreservesBackendWatchTermID() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        _ = db.mergeItems(newItems: [
            FeedItem(id: "youtube:1", platform: "youtube", url: "https://u/1",
                     title: "Aiko video", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "video", published_at: now, watch_term_keyword: "Aiko",
                     watch_term_id: 42, fetched_at: now, source: "youtube_api")
        ])
        _ = db.mergeItems(newItems: [
            FeedItem(id: "youtube:1", platform: "youtube", url: "https://u/1",
                     title: "Aiko video extended title", content_text: "more", author: nil,
                     thumbnail_url: nil, media_type: "video", published_at: now,
                     watch_term_keyword: "Aiko", fetched_at: now, source: "youtube_api")
        ])

        XCTAssertEqual(db.feedItems.first?.watch_term_id, 42)
    }

    func testDeleteTermTombstoneSkipsBackendSyncUntilKeywordIsSavedAgain() throws {
        let term = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        db.markTermDeleteConfirmed(term)
        db.deleteTerm(id: term.id)

        XCTAssertTrue(db.shouldSkipBackendTermAfterLocalDelete(term))
        XCTAssertTrue(
            db.shouldSkipBackendTermAfterLocalDelete(
                WatchTerm(id: "server-aiko", keyword: "Aiko", collection_mode: .mediaOnly)
            )
        )

        let newTerm = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        XCTAssertFalse(db.shouldSkipBackendTermAfterLocalDelete(newTerm))
    }

    func testUnconfirmedDeleteDoesNotCompleteLocalDeleteOnLoad() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let term = WatchTerm(id: "42", keyword: "Aiko", collection_mode: .allInfo)
        let item = FeedItem(id: "news:1", platform: "news", url: "https://news/1",
                            title: "Aiko news", content_text: nil, author: nil, thumbnail_url: nil,
                            media_type: "article", published_at: now, watch_term_keyword: "Aiko", fetched_at: now)
        try JSONEncoder().encode([term])
            .write(to: tempDir.appendingPathComponent("terms.json"))
        try JSONEncoder().encode([item])
            .write(to: tempDir.appendingPathComponent("feed_items.json"))

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertEqual(freshDB.terms.map(\.keyword), ["Aiko"])
        XCTAssertEqual(freshDB.feedItems.map(\.watch_term_keyword), ["Aiko"])
        XCTAssertFalse(freshDB.shouldSkipBackendTermAfterLocalDelete(term))
    }

    func testPersistedDeleteTombstoneCompletesLocalDeleteOnLoad() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let term = WatchTerm(id: "42", keyword: "Aiko", collection_mode: .allInfo)
        let items = [
            FeedItem(id: "news:1", platform: "news", url: "https://news/1",
                     title: "Aiko news", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "article", published_at: now, watch_term_keyword: "Aiko", fetched_at: now),
            FeedItem(id: "news:2", platform: "news", url: "https://news/2",
                     title: "Haruka news", content_text: nil, author: nil, thumbnail_url: nil,
                     media_type: "article", published_at: now, watch_term_keyword: "Haruka", fetched_at: now),
        ]
        try JSONEncoder().encode([term, WatchTerm(id: "43", keyword: "Haruka", collection_mode: .allInfo)])
            .write(to: tempDir.appendingPathComponent("terms.json"))
        try JSONEncoder().encode(items)
            .write(to: tempDir.appendingPathComponent("feed_items.json"))
        try JSONEncoder().encode(["Aiko": Date()])
            .write(to: tempDir.appendingPathComponent("term_delete_tombstones.json"))

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertEqual(freshDB.terms.map(\.keyword), ["Haruka"])
        XCTAssertEqual(freshDB.feedItems.map(\.watch_term_keyword), ["Haruka"])
        XCTAssertTrue(freshDB.shouldSkipBackendTermAfterLocalDelete(term))
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
            fetched_at: nowString,
            source: "youtube_api"
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
            fetched_at: nowString,
            source: "youtube_api"
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

    func testMergeReplacesBackendMatchRedirectWithSourceURL() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let preview = FeedItem(
            id: "youtube:redirected",
            platform: "youtube",
            url: "https://backend.example.com/api/feed/matches/99/redirect",
            title: "Preview video",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_api"
        )
        let backendFeedItem = FeedItem(
            id: "youtube:redirected",
            platform: "youtube",
            url: "https://youtube.com/watch?v=redirected",
            title: "Preview video",
            content_text: "Full description",
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_api"
        )

        _ = db.mergeItems(newItems: [preview])
        _ = db.mergeItems(newItems: [backendFeedItem])

        XCTAssertEqual(db.feedItems.first?.url, "https://youtube.com/watch?v=redirected")
        XCTAssertEqual(db.feedItems.first?.content_text, "Full description")
    }

    func testMergeKeepsExistingSourceURLWhenIncomingURLIsBackendMatchRedirect() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let source = FeedItem(
            id: "youtube:stable",
            platform: "youtube",
            url: "https://youtube.com/watch?v=stable",
            title: "Stable video",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_api"
        )
        let preview = FeedItem(
            id: "youtube:stable",
            platform: "youtube",
            url: "https://backend.example.com/api/feed/matches/101/redirect",
            title: "Stable video",
            content_text: "Preview details",
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_api"
        )

        _ = db.mergeItems(newItems: [source])
        _ = db.mergeItems(newItems: [preview])

        XCTAssertEqual(db.feedItems.first?.url, "https://youtube.com/watch?v=stable")
        XCTAssertEqual(db.feedItems.first?.content_text, "Preview details")
    }

    func testQueryFeedExcludesBackendMatchRedirectItems() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let redirectOnly = FeedItem(
            id: "youtube:redirect-only",
            platform: "youtube",
            url: "https://backend.example.com/api/feed/matches/102/redirect",
            title: "Redirect-only video",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_api"
        )
        let stable = FeedItem(
            id: "youtube:stable-visible",
            platform: "youtube",
            url: "https://youtube.com/watch?v=stable-visible",
            title: "Stable video",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_api"
        )

        _ = db.mergeItems(newItems: [redirectOnly, stable])

        XCTAssertEqual(db.queryFeed(keyword: nil, days: 30).map(\.id), ["youtube:stable-visible"])
    }

    func testMergeSearchFallbackItemsAreDropped() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        // Items whose id starts with "search:" come from local search fallbacks
        // and must not be persisted into the feed.
        let searchItem = FeedItem(
            id: "search:fallback", platform: "news",
            url: "https://example.com/search",
            title: "search: result", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: now,
            watch_term_keyword: "Aiko", fetched_at: now
        )
        let added = db.mergeItems(newItems: [searchItem])
        XCTAssertEqual(added, 0)
        XCTAssertTrue(db.feedItems.isEmpty, "search: prefix items must be silently dropped")
    }

    func testMergeContentAndThumbnailBackfill() throws {
        // When an existing item has no content_text or thumbnail_url, a later merge
        // should fill them in from the new item (nil-coalescing fill, not replace).
        let now = ISO8601DateFormatter().string(from: Date())
        let base = FeedItem(
            id: "youtube:fill-test", platform: "youtube",
            url: "https://youtube.com/1",
            title: "Video", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )
        let enriched = FeedItem(
            id: "youtube:fill-test", platform: "youtube",
            url: "https://youtube.com/1",
            title: "Video", content_text: "Description text", author: "Creator",
            thumbnail_url: "https://i.ytimg.com/thumb.jpg",
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )
        _ = db.mergeItems(newItems: [base])
        _ = db.mergeItems(newItems: [enriched])

        let item = db.feedItems.first
        XCTAssertEqual(item?.content_text, "Description text")
        XCTAssertEqual(item?.author, "Creator")
        XCTAssertEqual(item?.thumbnail_url, "https://i.ytimg.com/thumb.jpg")
    }

    func testMergeDoesNotOverwriteExistingContent() throws {
        // Once content_text and thumbnail_url are set, they should not be cleared
        // by a subsequent merge that brings nil values.
        let now = ISO8601DateFormatter().string(from: Date())
        let withContent = FeedItem(
            id: "youtube:keep-test", platform: "youtube",
            url: "https://youtube.com/2",
            title: "Video", content_text: "Original description", author: nil,
            thumbnail_url: "https://i.ytimg.com/original.jpg",
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )
        let noContent = FeedItem(
            id: "youtube:keep-test", platform: "youtube",
            url: "https://youtube.com/2",
            title: "Video", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )
        _ = db.mergeItems(newItems: [withContent])
        _ = db.mergeItems(newItems: [noContent])

        let item = db.feedItems.first
        XCTAssertEqual(item?.content_text, "Original description")
        XCTAssertEqual(item?.thumbnail_url, "https://i.ytimg.com/original.jpg")
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

    func testMergeUpdatesDiscussionPublishedAtToLatestActivity() throws {
        let formatter = ISO8601DateFormatter()
        let older = formatter.string(from: Date(timeIntervalSinceNow: -3600))
        let newer = formatter.string(from: Date(timeIntervalSinceNow: -60))
        let base = FeedItem(
            id: "girlschannel:thread-1", platform: "girlschannel", url: "https://girlschannel.net/topics/1",
            title: "Aiko thread", content_text: "Aiko", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: older, watch_term_keyword: "Aiko", fetched_at: older
        )
        let bumped = FeedItem(
            id: "girlschannel:thread-1", platform: "girlschannel", url: "https://girlschannel.net/topics/1",
            title: "Aiko thread", content_text: "Aiko latest reply", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: newer, watch_term_keyword: "Aiko", fetched_at: newer
        )

        _ = db.mergeItems(newItems: [base])
        _ = db.mergeItems(newItems: [bumped])

        XCTAssertEqual(db.feedItems.first?.published_at, newer)
        XCTAssertEqual(db.feedItems.first?.content_text, "Aiko latest reply")
    }

    @MainActor
    func testNotificationManagerSchedulesTestNotificationAfterAuthorization() async throws {
        let center = MockNotificationCenter(status: .notDetermined, grantsAuthorization: true)
        let manager = NotificationManager(center: center)

        try await manager.sendTestNotification()

        XCTAssertEqual(center.authorizationRequestCount, 1)
        XCTAssertEqual(center.requests.count, 1)
        XCTAssertEqual(center.requests.first?.content.title, I18nManager.shared.tFormat("notifNewItemsTitle", "OshiReader"))
        XCTAssertEqual(center.requests.first?.content.body, I18nManager.shared.t("notifTestBody"))
        XCTAssertEqual(center.requests.first?.content.categoryIdentifier, NotificationManager.resultPreviewCategoryIdentifier)
        XCTAssertEqual(center.requests.first?.content.targetContentIdentifier, "oshireader-test-preview")
        let previewItem = center.requests.first?.content.userInfo["preview_item"] as? [String: Any]
        XCTAssertEqual(previewItem?["id"] as? String, "oshireader-test-preview")
        XCTAssertEqual(previewItem?["title"] as? String, I18nManager.shared.t("notifTestBody"))
        XCTAssertNotNil(center.requests.first?.trigger)
    }

    func testAPNSDeviceTokenStringUsesLowercaseHex() throws {
        let data = Data([0x00, 0x0f, 0xa1, 0xff])
        XCTAssertEqual(NotificationManager.deviceTokenString(data), "000fa1ff")
    }

    func testAPNSEnvironmentNormalizationUsesAPNsHostNames() throws {
        XCTAssertEqual(NetworkManager.normalizedAPNSEnvironment("development"), "sandbox")
        XCTAssertEqual(NetworkManager.normalizedAPNSEnvironment("sandbox"), "sandbox")
        XCTAssertEqual(NetworkManager.normalizedAPNSEnvironment("production"), "production")
        XCTAssertEqual(NetworkManager.normalizedAPNSEnvironment("  PRODUCTION  "), "production")
        XCTAssertNil(NetworkManager.normalizedAPNSEnvironment("invalid"))
        XCTAssertNil(NetworkManager.normalizedAPNSEnvironment(nil))
    }

    func testAutomaticTermSyncPreservesNotificationOwnership() throws {
        let serverEnabled = WatchTerm(keyword: "Aiko", notify_on_new: true)
        let serverDisabled = WatchTerm(keyword: "Aiko", notify_on_new: false)
        let localEnabled = WatchTerm(keyword: "Aiko", notify_on_new: true)
        let localDisabled = WatchTerm(keyword: "Aiko", notify_on_new: false)

        XCTAssertNil(
            NetworkManager.automaticSyncNotifyOnNewUpdate(
                localTerm: localDisabled,
                serverTerm: serverEnabled
            )
        )
        XCTAssertEqual(
            NetworkManager.automaticSyncNotifyOnNewUpdate(
                localTerm: localEnabled,
                serverTerm: serverDisabled
            ),
            true
        )
        XCTAssertTrue(NetworkManager.automaticSyncNotifyOnNewCreate(localTerm: localEnabled))
        XCTAssertFalse(NetworkManager.automaticSyncNotifyOnNewCreate(localTerm: localDisabled))
    }

    @MainActor
    func testNotificationManagerUsesPersistedRegisteredToken() async throws {
        let manager = NotificationManager(
            center: MockNotificationCenter(status: .authorized),
            initialRegisteredDeviceToken: String(repeating: "a", count: 64)
        )

        let registered = await manager.ensureRemoteNotificationsRegisteredIfAllowed(timeout: 0.01)
        XCTAssertTrue(registered)
    }

    func testBackgroundRefreshUsesLocalFallbackOnlyWithoutRemoteToken() {
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldScheduleLocalFallback(
                hasRegisteredRemoteDeviceForCurrentEnvironment: false
            )
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldScheduleLocalFallback(
                hasRegisteredRemoteDeviceForCurrentEnvironment: true
            )
        )
    }

    func testBackgroundRefreshLocalNotificationRequiresFallbackState() {
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldNotifyLocallyFromBackground(
                hasRegisteredRemoteDeviceForCurrentEnvironment: false,
                hadItemsInitially: true,
                hasPendingNotificationItems: true
            )
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldNotifyLocallyFromBackground(
                hasRegisteredRemoteDeviceForCurrentEnvironment: true,
                hadItemsInitially: true,
                hasPendingNotificationItems: true
            )
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldNotifyLocallyFromBackground(
                hasRegisteredRemoteDeviceForCurrentEnvironment: false,
                hadItemsInitially: false,
                hasPendingNotificationItems: true
            )
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldNotifyLocallyFromBackground(
                hasRegisteredRemoteDeviceForCurrentEnvironment: false,
                hadItemsInitially: true,
                hasPendingNotificationItems: false
            )
        )
    }

    func testRemoteNotificationNewMatchPayloadSkipsDuplicatePoll() {
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldTriggerPoll(
                forRemoteNotification: [
                    "watch_term_keyword": "Aiko",
                    "new_count": 1,
                ]
            )
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldTriggerPoll(
                forRemoteNotification: [
                    "preview_item": ["id": "youtube:1"],
                ]
            )
        )
    }

    func testRemoteNotificationPreviewPayloadStillRefreshesFeed() {
        let userInfo: [AnyHashable: Any] = [
            "watch_term_keyword": "Aiko",
            "new_count": 3,
            "preview_item": ["id": "youtube:1"],
        ]

        XCTAssertFalse(BackgroundRefreshPolicy.shouldTriggerPoll(forRemoteNotification: userInfo))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldMergePreviewBeforeRefresh(forRemoteNotification: userInfo))
    }

    func testGenericRemoteNotificationStillTriggersPoll() {
        XCTAssertTrue(BackgroundRefreshPolicy.shouldTriggerPoll(forRemoteNotification: [:]))
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldTriggerPoll(
                forRemoteNotification: [
                    "aps": ["content-available": 1],
                ]
            )
        )
        XCTAssertFalse(BackgroundRefreshPolicy.shouldMergePreviewBeforeRefresh(forRemoteNotification: [:]))
    }

    @MainActor
    func testBackgroundRefreshRegistersAndSchedulesTask() throws {
        let scheduler = MockBackgroundTaskScheduler()
        let manager = BackgroundRefreshManager(scheduler: scheduler)
        let before = Date()

        manager.register()
        manager.schedule()

        XCTAssertEqual(scheduler.registeredIdentifier, BackgroundRefreshManager.taskIdentifier)
        XCTAssertEqual(scheduler.cancelledIdentifiers, [BackgroundRefreshManager.taskIdentifier])
        let request = try XCTUnwrap(scheduler.submittedRequests.first as? BGAppRefreshTaskRequest)
        XCTAssertEqual(request.identifier, BackgroundRefreshManager.taskIdentifier)
        let earliest = try XCTUnwrap(request.earliestBeginDate)
        XCTAssertGreaterThanOrEqual(earliest.timeIntervalSince(before), 14.9 * 60)
    }

    func testBackgroundRefreshBudgetLeavesTimeForCompletion() {
        XCTAssertLessThan(BackgroundRefreshPolicy.pollTimeout, BackgroundRefreshPolicy.operationDeadline)
        XCTAssertGreaterThanOrEqual(
            BackgroundRefreshPolicy.operationDeadline - BackgroundRefreshPolicy.pollTimeout,
            15
        )
        XCTAssertLessThanOrEqual(BackgroundRefreshPolicy.operationDeadline, 25)
        XCTAssertLessThan(
            BackgroundRefreshPolicy.minimumLocalRefreshWindow,
            BackgroundRefreshPolicy.operationDeadline
        )
    }

    func testBackgroundRefreshLocalFallbackOnlyStartsWithRemainingBudget() {
        let now = Date(timeIntervalSince1970: 1_000)
        let plentyOfTimeStartedAt = now.addingTimeInterval(
            -BackgroundRefreshPolicy.pollTimeout
        )
        let almostExpiredStartedAt = now.addingTimeInterval(
            -(BackgroundRefreshPolicy.operationDeadline - 1)
        )
        let expiredStartedAt = now.addingTimeInterval(
            -BackgroundRefreshPolicy.operationDeadline
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldStartLocalBackgroundRefresh(
                remainingTime: BackgroundRefreshPolicy.remainingOperationTime(
                    startedAt: plentyOfTimeStartedAt,
                    now: now
                )
            )
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldStartLocalBackgroundRefresh(
                remainingTime: BackgroundRefreshPolicy.remainingOperationTime(
                    startedAt: almostExpiredStartedAt,
                    now: now
                )
            )
        )
        XCTAssertEqual(
            BackgroundRefreshPolicy.remainingOperationTime(startedAt: expiredStartedAt, now: now),
            0
        )
    }

    func testForegroundDeviceRefreshBypassesThrottleWhenFeedScopeIsDirty() {
        let throttle: TimeInterval = 30 * 60
        let recentDeviceScrapeElapsed: TimeInterval = 60

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldStartForegroundDeviceRefresh(
                cacheIsEmpty: false,
                elapsedSinceLastDeviceScrape: recentDeviceScrapeElapsed,
                throttle: throttle
            ),
            "Warm caches should still respect the device scrape throttle by default."
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldStartForegroundDeviceRefresh(
                cacheIsEmpty: true,
                elapsedSinceLastDeviceScrape: recentDeviceScrapeElapsed,
                throttle: throttle
            ),
            "Cold caches should still bypass the device scrape throttle."
        )

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldStartForegroundDeviceRefresh(
                cacheIsEmpty: false,
                elapsedSinceLastDeviceScrape: recentDeviceScrapeElapsed,
                throttle: throttle
            ),
            "Changing terms, platforms, or custom URLs should let fallback sources refresh immediately."
        )
    }

    func testBackendConnectivityFailureSkipsForegroundBackendRetries() {
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldSkipBackendRetriesAfterFailure(
                URLError(.notConnectedToInternet)
            )
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldSkipBackendRetriesAfterFailure(
                URLError(.timedOut)
            )
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldSkipBackendRetriesAfterFailure(
                URLError(.cannotConnectToHost)
            )
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldSkipBackendRetriesAfterFailure(
                APIClientError.httpStatus(500, detail: nil)
            ),
            "Fast backend HTTP failures can still use the normal retry path."
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldSkipBackendRetriesAfterFailure(
                DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "bad json"))
            )
        )
    }

    func testForegroundRefreshPolicyRefreshesEmptyOrStaleCache() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recent = FeedItem(
            id: "youtube:recent", platform: "youtube", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:26:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:26:00Z",
            source: "youtube_api"
        )
        let stale = FeedItem(
            id: "youtube:stale", platform: "youtube", url: "https://example.com/stale",
            title: "Stale", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:20:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:20:00Z",
            source: "youtube_api"
        )
        let invalid = FeedItem(
            id: "youtube:invalid", platform: "youtube", url: "https://example.com/invalid",
            title: "Invalid", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "not-a-date",
            watch_term_keyword: "Aiko", fetched_at: "not-a-date",
            source: "youtube_api"
        )

        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [], now: now, lastRefreshAt: nil))
        XCTAssertFalse(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [recent], now: now, lastRefreshAt: nil))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [stale], now: now, lastRefreshAt: nil))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [invalid], now: now, lastRefreshAt: nil))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [], now: now, lastRefreshAt: nil))
        XCTAssertFalse(BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [recent], now: now, lastRefreshAt: nil))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [stale], now: now, lastRefreshAt: nil))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [invalid], now: now, lastRefreshAt: nil))

        let recentRefresh = now.addingTimeInterval(-60)
        let staleRefresh = now.addingTimeInterval(-10 * 60)
        XCTAssertFalse(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [], now: now, lastRefreshAt: recentRefresh))
        XCTAssertFalse(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [stale], now: now, lastRefreshAt: recentRefresh))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [stale], now: now, lastRefreshAt: staleRefresh))
        XCTAssertFalse(BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [], now: now, lastRefreshAt: recentRefresh))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [stale], now: now, lastRefreshAt: staleRefresh))
    }

    func testBackendCompletionDoesNotSuppressForegroundRefresh() {
        let now = Date(timeIntervalSince1970: 1_800)
        let stale = FeedItem(
            id: "yahoonews:stale", platform: "yahoonews", url: "https://example.com/stale",
            title: "Stale", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:20:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:20:00Z"
        )
        let recentCompletion = now.addingTimeInterval(-60)

        BackgroundRefreshPolicy.recordBackendRefreshCompleted(at: recentCompletion)

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [stale], now: now),
            "Backend-only refreshes must not suppress the foreground device fallback pass."
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [stale], now: now),
            "A recent backend refresh should still prevent duplicate suspension refreshes."
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [WatchTerm(keyword: "Aiko", collection_mode: .allInfo)],
                customUrls: [],
                items: [],
                pulledNewTerms: false,
                now: now
            ),
            "Backend-only refreshes must not suppress an empty foreground device fallback pass."
        )

        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["yahoonews"],
            customRefreshed: false,
            completedDevicePlatforms: ["yahoonews"],
            activeTerms: [WatchTerm(keyword: "Aiko", collection_mode: .allInfo)],
            customUrls: [],
            subscribedPlatforms: ["yahoonews"],
            at: recentCompletion
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [WatchTerm(keyword: "Aiko", collection_mode: .allInfo)],
                customUrls: [],
                items: [],
                pulledNewTerms: false,
                now: now
            ),
            "Background local/device checks should suppress duplicate foreground refreshes."
        )

        BackgroundRefreshPolicy.clearRecordedRefreshCompletionsForTesting()
        BackgroundRefreshPolicy.recordRefreshCompleted(at: recentCompletion)

        XCTAssertFalse(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [stale], now: now))
    }

    func testDeviceFallbackTermsIncludeMediaOnlyTermsForMediaPlatformsOnly() {
        let allInfo = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let mediaOnly = WatchTerm(keyword: "Haruka", collection_mode: .mediaOnly)
        let inactive = WatchTerm(keyword: "Miku", collection_mode: .allInfo, is_active: false)

        let eligible = BackgroundRefreshPolicy.termsEligibleForDeviceFallback(
            [allInfo, mediaOnly, inactive],
            subscribedPlatforms: ["youtube", "note"]
        )

        XCTAssertEqual(eligible.map(\.keyword), ["Aiko", "Haruka"])
        XCTAssertEqual(
            BackgroundRefreshPolicy.deviceFallbackPlatforms(
                for: mediaOnly,
                subscribedPlatforms: ["youtube", "note"]
            ),
            ["youtube"]
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.termsEligibleForDeviceFallback(
                [mediaOnly],
                subscribedPlatforms: ["note"]
            ).isEmpty
        )
    }

    func testSourceScopeAllowsMediaOnlyDeviceFallbackForMediaPlatforms() {
        let mediaOnly = WatchTerm(keyword: "Aiko", collection_mode: .mediaOnly)
        let allInfo = WatchTerm(keyword: "Haruka", collection_mode: .allInfo)

        let mediaOnlyScope = BackgroundRefreshPolicy.sourceScope(
            activeTerms: [mediaOnly],
            customUrls: [],
            subscribedPlatforms: ["youtube", "note"]
        )
        XCTAssertTrue(mediaOnlyScope.needsBackend)
        XCTAssertTrue(mediaOnlyScope.needsDevice)
        XCTAssertTrue(mediaOnlyScope.needsLocal)
        XCTAssertEqual(mediaOnlyScope.requiredBackendPlatforms, ["youtube"])
        XCTAssertEqual(mediaOnlyScope.requiredDevicePlatforms, ["youtube"])

        let mediaOnlyArticleScope = BackgroundRefreshPolicy.sourceScope(
            activeTerms: [mediaOnly],
            customUrls: [],
            subscribedPlatforms: ["note"]
        )
        XCTAssertFalse(mediaOnlyArticleScope.needsBackend)
        XCTAssertFalse(mediaOnlyArticleScope.needsDevice)
        XCTAssertFalse(mediaOnlyArticleScope.needsLocal)
        XCTAssertTrue(mediaOnlyArticleScope.requiredBackendPlatforms.isEmpty)

        let allInfoScope = BackgroundRefreshPolicy.sourceScope(
            activeTerms: [allInfo],
            customUrls: [],
            subscribedPlatforms: ["youtube", "note"]
        )
        XCTAssertTrue(allInfoScope.needsBackend)
        XCTAssertTrue(allInfoScope.needsDevice)
        XCTAssertTrue(allInfoScope.needsLocal)
        XCTAssertEqual(allInfoScope.requiredBackendPlatforms, ["youtube", "note"])
        XCTAssertEqual(allInfoScope.requiredDevicePlatforms, ["youtube", "note"])
    }

    func testMediaOnlyArticleOnlySubscriptionsDoNotForceForegroundBackendRefresh() {
        let now = Date(timeIntervalSince1970: 1_800)
        let mediaOnly = WatchTerm(keyword: "Aiko", collection_mode: .mediaOnly)

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [mediaOnly],
                customUrls: [],
                subscribedPlatforms: ["note"],
                items: [],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            ),
            "Media-only keywords should not keep retrying article-only backend sources."
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(
                activeTerms: [mediaOnly],
                customUrls: [],
                subscribedPlatforms: ["note"],
                items: [],
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            ),
            "Dirty feed-scope markers should not force a no-op suspension refresh for media-only/article-only scopes."
        )
    }

    func testDeviceFallbackFreshnessRequiresMediaOnlyMediaPlatforms() {
        let now = Date(timeIntervalSince1970: 1_800)
        let allInfo = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let mediaOnly = WatchTerm(keyword: "Haruka", collection_mode: .mediaOnly)
        let recentFallback = FeedItem(
            id: "youtube:recent",
            platform: "youtube",
            url: "https://example.com/recent",
            title: "Aiko recent",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )
        let recentMediaOnlyFallback = FeedItem(
            id: "youtube:haruka",
            platform: "youtube",
            url: "https://youtube.com/watch?v=haruka",
            title: "Haruka video",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Haruka",
            fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [allInfo, mediaOnly],
                customUrls: [],
                subscribedPlatforms: ["youtube"],
                items: [recentFallback],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: now
            ),
            "Media-only keywords should keep media device fallback stale until their media platform has fresh items."
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [allInfo, mediaOnly],
                customUrls: [],
                subscribedPlatforms: ["youtube"],
                items: [recentFallback, recentMediaOnlyFallback],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: now
            ),
            "Media-only keywords should be fresh once their eligible media fallback platform has a fresh item."
        )
    }

    func testLegacyYouTubeGoogleNewsFallbackDoesNotSatisfyRefreshFreshness() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recent = _ISO8601Cache.withoutFractional.string(from: now.addingTimeInterval(-60))
        let term = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let legacyFallback = FeedItem(
            id: "youtube:https://news.google.com/rss/articles/old-video",
            platform: "youtube",
            url: "https://news.google.com/rss/articles/old-video?oc=5",
            title: "Aiko old video resurfaced",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: recent,
            watch_term_keyword: "Aiko",
            fetched_at: recent,
            source: "google_news"
        )
        let directVideo = FeedItem(
            id: "youtube:direct-fresh",
            platform: "youtube",
            url: "https://youtube.com/watch?v=direct-fresh",
            title: "Aiko direct upload",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: recent,
            watch_term_keyword: "Aiko",
            fetched_at: recent,
            source: "youtube_scrape"
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube"],
                items: [legacyFallback],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            )
        )
        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [legacyFallback], now: now, lastRefreshAt: nil))
        XCTAssertNil(BackgroundRefreshPolicy.incrementalSince(in: [legacyFallback], platformId: "youtube"))
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube"],
                items: [directVideo],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            )
        )
    }

    func testBackendPollTriggerUsesLongQuotaFriendlyThrottle() {
        let now = Date(timeIntervalSince1970: 20_000)

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldTriggerBackendPoll(now: now, lastTriggeredAt: nil)
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldTriggerBackendPoll(
                now: now,
                lastTriggeredAt: now.addingTimeInterval(-60 * 60)
            ),
            "App-triggered backend polls should stay throttled after one hour."
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldTriggerBackendPoll(
                now: now,
                lastTriggeredAt: now.addingTimeInterval(-BackgroundRefreshPolicy.backendPollTriggerInterval)
            )
        )

        BackgroundRefreshPolicy.recordBackendPollTriggered(at: now)
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldTriggerBackendPoll(now: now.addingTimeInterval(60 * 60))
        )
    }

    func testFeedScopeInvalidationPreservesBackendPollTriggerThrottle() {
        let now = Date(timeIntervalSince1970: 30_000)

        BackgroundRefreshPolicy.recordBackendPollTriggered(at: now)
        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldTriggerBackendPoll(now: now.addingTimeInterval(60 * 60)),
            "Feed-scope changes should not allow another backend poll before the quota-friendly interval."
        )
    }

    func testBackendPollEligibilityDoesNotConsumeThrottleBeforeRequestOutcome() {
        let now = Date(timeIntervalSince1970: 40_000)

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldTriggerBackendPoll(now: now.addingTimeInterval(60 * 60)),
            "Poll eligibility must remain available until the backend request succeeds."
        )
        BackgroundRefreshPolicy.recordBackendPollTriggered(at: now)
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldTriggerBackendPoll(now: now.addingTimeInterval(60 * 60))
        )
    }

    func testBackgroundCompletionClearsDirtyForBackendOnlyScope() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recentCompletion = now.addingTimeInterval(-60)
        let term = WatchTerm(keyword: "Aiko", collection_mode: .mediaOnly)
        let recent = FeedItem(
            id: "youtube:recent", platform: "youtube", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["youtube"],
            customRefreshed: false,
            completedDevicePlatforms: ["youtube"],
            activeTerms: [term],
            customUrls: [],
            subscribedPlatforms: ["youtube"],
            at: recentCompletion
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "A backend-only background refresh should satisfy a backend-only feed scope."
        )
    }

    func testStaleFeedScopeCompletionDoesNotClearNewDirtyScope() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recentCompletion = now.addingTimeInterval(-60)
        let term = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let recent = FeedItem(
            id: "youtube:recent", platform: "youtube", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        let staleRevision = BackgroundRefreshPolicy.currentFeedScopeRevision
        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()

        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["youtube"],
            customRefreshed: false,
            completedDevicePlatforms: ["youtube"],
            activeTerms: [term],
            customUrls: [],
            subscribedPlatforms: ["youtube"],
            feedScopeRevision: staleRevision,
            at: recentCompletion
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube"],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "An older in-flight refresh must not clear a newer feed-scope change."
        )

        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["youtube"],
            customRefreshed: false,
            completedDevicePlatforms: ["youtube"],
            activeTerms: [term],
            customUrls: [],
            subscribedPlatforms: ["youtube"],
            feedScopeRevision: BackgroundRefreshPolicy.currentFeedScopeRevision,
            at: recentCompletion
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube"],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "The matching feed-scope revision should still clear once it completes."
        )
    }

    func testNoSourceBackgroundCompletionClearsDirtyFeedScope() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recentCompletion = now.addingTimeInterval(-60)

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(
                activeTerms: [],
                customUrls: [],
                subscribedPlatforms: [],
                items: [],
                now: now
            ),
            "A feed-scope change starts dirty even if the source was removed."
        )

        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: [],
            customRefreshed: false,
            completedDevicePlatforms: [],
            activeTerms: [],
            customUrls: [],
            subscribedPlatforms: [],
            feedScopeRevision: BackgroundRefreshPolicy.currentFeedScopeRevision,
            at: recentCompletion
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(
                activeTerms: [],
                customUrls: [],
                subscribedPlatforms: [],
                items: [],
                now: now
            ),
            "Once there are no refreshable sources left, the dirty marker should not cause no-op refreshes forever."
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [],
                customUrls: [],
                subscribedPlatforms: [],
                items: [],
                pulledNewTerms: false,
                now: now
            )
        )
    }

    func testBackgroundCompletionAllowsDeviceFallbackToCoverBackendMissingPlatforms() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recentCompletion = now.addingTimeInterval(-60)
        let term = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let recentYouTube = FeedItem(
            id: "youtube:recent", platform: "youtube", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )
        let recentNote = FeedItem(
            id: "note:recent", platform: "note", url: "https://example.com/note",
            title: "Recent note", content_text: "Aiko", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z"
        )

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["youtube"],
            customRefreshed: false,
            completedDevicePlatforms: ["youtube", "note"],
            activeTerms: [term],
            customUrls: [],
            subscribedPlatforms: ["youtube", "note"],
            at: recentCompletion
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube", "note"],
                items: [recentYouTube, recentNote],
                pulledNewTerms: false,
                now: now
            ),
            "Device fallback completion should cover backend-missing device-fallback platforms when the server is down."
        )

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: [],
            customRefreshed: false,
            completedDevicePlatforms: ["youtube", "note"],
            activeTerms: [term],
            customUrls: [],
            subscribedPlatforms: ["youtube", "note"],
            at: recentCompletion
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube", "note"],
                items: [recentYouTube, recentNote],
                pulledNewTerms: false,
                now: now
            ),
            "Empty backend completion should still clear once every required device fallback platform completes locally."
        )
    }

    func testBackgroundCompletionIgnoresArticleOnlyPlatformsForMediaOnlyTerms() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recentCompletion = now.addingTimeInterval(-60)
        let term = WatchTerm(keyword: "Aiko", collection_mode: .mediaOnly)
        let recentNote = FeedItem(
            id: "note:recent", platform: "note", url: "https://example.com/note",
            title: "Recent note", content_text: "Aiko", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z"
        )

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: [],
            customRefreshed: false,
            completedDevicePlatforms: [],
            activeTerms: [term],
            customUrls: [],
            subscribedPlatforms: ["note"],
            at: recentCompletion
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["note"],
                items: [recentNote],
                pulledNewTerms: false,
                now: now
            ),
            "Article-only platforms should not keep media-only terms dirty."
        )

        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["note"],
            customRefreshed: false,
            completedDevicePlatforms: [],
            activeTerms: [term],
            customUrls: [],
            subscribedPlatforms: ["note"],
            at: recentCompletion
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["note"],
                items: [recentNote],
                pulledNewTerms: false,
                now: now
            ),
            "Backend completion should clear when local device fallback is intentionally ineligible for this term."
        )
    }

    func testBackgroundCompletionKeepsDirtyForMixedPartialScope() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recentCompletion = now.addingTimeInterval(-60)
        let term = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let customUrl = CustomUrl(
            id: "custom:https%3A%2F%2Fexample.com%2Ffeed",
            url: "https://example.com/feed",
            title: "Feed",
            added_at: "1970-01-01T00:00:00Z"
        )
        let recent = FeedItem(
            id: "youtube:recent", platform: "youtube", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["youtube"],
            customRefreshed: false,
            completedDevicePlatforms: [],
            activeTerms: [term],
            customUrls: [customUrl],
            subscribedPlatforms: ["youtube", "custom"],
            at: recentCompletion
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [customUrl],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "A mixed backend/local scope should stay pending after backend-only completion."
        )

        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: [],
            customRefreshed: true,
            completedDevicePlatforms: [],
            activeTerms: [term],
            customUrls: [customUrl],
            subscribedPlatforms: ["youtube", "custom"],
            at: recentCompletion
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [customUrl],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "A mixed backend/local scope should stay pending after local-only completion."
        )

        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["youtube"],
            customRefreshed: true,
            completedDevicePlatforms: ["youtube"],
            activeTerms: [term],
            customUrls: [customUrl],
            subscribedPlatforms: ["youtube", "custom"],
            at: recentCompletion
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [customUrl],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "A mixed backend/local scope should clear once both source classes complete."
        )
    }

    func testBackgroundCompletionKeepsDirtyWhenDeviceFallbackIsSkippedInMixedScope() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recentCompletion = now.addingTimeInterval(-60)
        let term = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let customUrl = CustomUrl(
            id: "custom:https%3A%2F%2Fexample.com%2Ffeed",
            url: "https://example.com/feed",
            title: "Feed",
            added_at: "1970-01-01T00:00:00Z"
        )
        let recent = FeedItem(
            id: "youtube:recent", platform: "youtube", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["youtube", "yahoonews"],
            customRefreshed: true,
            completedDevicePlatforms: [],
            activeTerms: [term],
            customUrls: [customUrl],
            subscribedPlatforms: ["youtube", "yahoonews", "custom"],
            at: recentCompletion
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [customUrl],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "A mixed backend/custom/device scope should stay pending when the device fallback was throttled or skipped."
        )

        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["youtube", "yahoonews"],
            customRefreshed: true,
            completedDevicePlatforms: ["youtube", "yahoonews"],
            activeTerms: [term],
            customUrls: [customUrl],
            subscribedPlatforms: ["youtube", "yahoonews", "custom"],
            at: recentCompletion
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [customUrl],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "The same mixed scope should clear once backend, custom, and device checks all complete."
        )
    }

    func testBackgroundCompletionKeepsDirtyWhenDeviceFallbackPlatformIsMissing() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recentCompletion = now.addingTimeInterval(-60)
        let term = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let recent = FeedItem(
            id: "yahoonews:recent", platform: "yahoonews", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z"
        )

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["youtube", "yahoonews", "niconico"],
            customRefreshed: false,
            completedDevicePlatforms: ["yahoonews"],
            activeTerms: [term],
            customUrls: [],
            subscribedPlatforms: ["youtube", "yahoonews", "niconico"],
            at: recentCompletion
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube", "yahoonews", "niconico"],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "Completing one device-fallback platform must not clear a dirty scope that also needs another fallback platform."
        )

        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: ["youtube", "yahoonews", "niconico"],
            customRefreshed: false,
            completedDevicePlatforms: ["youtube", "yahoonews", "niconico"],
            activeTerms: [term],
            customUrls: [],
            subscribedPlatforms: ["youtube", "yahoonews", "niconico"],
            at: recentCompletion
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube", "yahoonews", "niconico"],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "The dirty scope should clear once every subscribed device-fallback platform completes."
        )
    }

    func testCompletedDeviceFallbackPlatformsUseScrapeCompletionMetadata() {
        let emptyCompleted = LocalFallbackScrapeResult(
            items: [],
            completedPlatforms: ["yahoonews"]
        )
        XCTAssertEqual(
            BackgroundRefreshPolicy.completedDeviceFallbackPlatforms(
                from: emptyCompleted,
                subscribedPlatforms: ["yahoonews", "niconico"]
            ),
            ["yahoonews"],
            "A fallback platform can complete even when it returns no matching items."
        )

        let itemOnly = FeedItem(
            id: "yahoonews:recent", platform: "yahoonews", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z"
        )
        let partialItems = LocalFallbackScrapeResult(
            items: [itemOnly],
            completedPlatforms: ["yahoonews"]
        )
        XCTAssertEqual(
            BackgroundRefreshPolicy.completedDeviceFallbackPlatforms(
                from: partialItems,
                subscribedPlatforms: ["yahoonews", "niconico"]
            ),
            ["yahoonews"],
            "Returned items should not imply that every subscribed fallback platform completed."
        )
    }

    func testCompletedDeviceFallbackPlatformsRequireEverySearchToCompletePlatform() {
        let firstSearch = LocalFallbackScrapeResult(
            items: [],
            completedPlatforms: ["yahoonews", "niconico"]
        )
        let secondSearch = LocalFallbackScrapeResult(
            items: [],
            completedPlatforms: ["yahoonews"]
        )

        XCTAssertEqual(
            BackgroundRefreshPolicy.completedDeviceFallbackPlatformsForAllSearches(
                from: [firstSearch, secondSearch],
                subscribedPlatforms: ["yahoonews", "niconico"]
            ),
            ["yahoonews"],
            "A fallback platform should complete only after every active keyword or alias has checked it."
        )

        XCTAssertEqual(
            BackgroundRefreshPolicy.completedDeviceFallbackPlatformsForAllSearches(
                from: [firstSearch],
                subscribedPlatforms: ["yahoonews", "niconico"]
            ),
            ["yahoonews", "niconico"],
            "Empty results can still complete all fallback platforms when the scrape reports successful checks."
        )
    }

    func testCompletedDeviceFallbackPlatformsRespectPerSearchEligibility() {
        let allInfoSearch = LocalFallbackScrapeResult(
            items: [],
            completedPlatforms: ["youtube", "note"]
        )
        let mediaOnlySearch = LocalFallbackScrapeResult(
            items: [],
            completedPlatforms: ["youtube"]
        )

        XCTAssertEqual(
            BackgroundRefreshPolicy.completedDeviceFallbackPlatformsForEligibleSearches([
                (allInfoSearch, ["youtube", "note"]),
                (mediaOnlySearch, ["youtube"])
            ]),
            ["youtube", "note"],
            "Article fallback platforms should complete when every search eligible for that platform completed it."
        )
    }

    func testRecentBackendRowsDoNotMaskStaleDeviceFallbackRows() {
        let now = Date(timeIntervalSince1970: 1_800)
        let term = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let recentBackend = FeedItem(
            id: "youtube:recent", platform: "youtube", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )
        let staleDevice = FeedItem(
            id: "yahoonews:stale", platform: "yahoonews", url: "https://example.com/stale",
            title: "Stale", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:20:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:20:00Z"
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube", "yahoonews"],
                items: [recentBackend, staleDevice],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: now.addingTimeInterval(-60)
            ),
            "Recent backend rows must not hide stale device-fallback rows in mixed scopes."
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube", "yahoonews"],
                items: [recentBackend, staleDevice],
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: now.addingTimeInterval(-60)
            ),
            "Suspension refresh should also still run when only the device-fallback source is stale."
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube", "yahoonews"],
                items: [recentBackend, staleDevice],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: now.addingTimeInterval(-60),
                lastBackendRefreshAt: now.addingTimeInterval(-60)
            ),
            "A recent full-source completion should still suppress duplicate foreground refreshes."
        )
    }

    func testRecentRowsMustCoverEachSubscribedBackendPlatform() {
        let now = Date(timeIntervalSince1970: 1_800)
        let term = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let recentYouTube = FeedItem(
            id: "youtube:recent", platform: "youtube", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )
        let recentNote = FeedItem(
            id: "note:recent", platform: "note", url: "https://example.com/note",
            title: "Recent note", content_text: "Aiko", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z"
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube", "note"],
                items: [recentYouTube],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            ),
            "A recent row from one backend platform should not cover another subscribed backend platform."
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["youtube", "note"],
                items: [recentYouTube, recentNote],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            ),
            "Fresh rows for every subscribed backend platform should satisfy backend freshness."
        )
    }

    func testRecentRowsMustCoverEachActiveTermForBackendPlatforms() {
        let now = Date(timeIntervalSince1970: 1_800)
        let aiko = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let haruka = WatchTerm(keyword: "Haruka", collection_mode: .allInfo)
        let recentAiko = FeedItem(
            id: "youtube:aiko", platform: "youtube", url: "https://example.com/aiko",
            title: "Aiko recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )
        let recentHaruka = FeedItem(
            id: "youtube:haruka", platform: "youtube", url: "https://example.com/haruka",
            title: "Haruka recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Haruka", fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [aiko, haruka],
                customUrls: [],
                subscribedPlatforms: ["youtube"],
                items: [recentAiko],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            ),
            "A fresh row for one active term must not cover another active term on the same backend platform."
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [aiko, haruka],
                customUrls: [],
                subscribedPlatforms: ["youtube"],
                items: [recentAiko, recentHaruka],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            )
        )
    }

    func testRecentRowsMustCoverEachSubscribedDeviceFallbackPlatform() {
        let now = Date(timeIntervalSince1970: 1_800)
        let term = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let recentYahoo = FeedItem(
            id: "yahoonews:recent", platform: "yahoonews", url: "https://example.com/yahoo",
            title: "Recent Yahoo", content_text: "Aiko", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z"
        )
        let recentFiveCh = FeedItem(
            id: "5ch:recent", platform: "5ch", url: "https://example.com/5ch",
            title: "Recent 5ch", content_text: "Aiko", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z"
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["yahoonews", "5ch"],
                items: [recentYahoo],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            ),
            "A recent row from one fallback platform should not cover another subscribed fallback platform."
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [term],
                customUrls: [],
                subscribedPlatforms: ["yahoonews", "5ch"],
                items: [recentYahoo, recentFiveCh],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            ),
            "Fresh rows for every subscribed fallback platform should satisfy device freshness."
        )
    }

    func testRecentRowsMustCoverEachActiveTermForDeviceFallbackPlatforms() {
        let now = Date(timeIntervalSince1970: 1_800)
        let aiko = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let haruka = WatchTerm(keyword: "Haruka", collection_mode: .allInfo)
        let recentAiko = FeedItem(
            id: "yahoonews:aiko", platform: "yahoonews", url: "https://example.com/aiko",
            title: "Aiko recent", content_text: "Aiko", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z"
        )
        let recentHaruka = FeedItem(
            id: "yahoonews:haruka", platform: "yahoonews", url: "https://example.com/haruka",
            title: "Haruka recent", content_text: "Haruka", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Haruka", fetched_at: "1970-01-01T00:29:00Z"
        )

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [aiko, haruka],
                customUrls: [],
                subscribedPlatforms: ["yahoonews"],
                items: [recentAiko],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            ),
            "A fresh row for one active term must not cover another active term on the same fallback platform."
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [aiko, haruka],
                customUrls: [],
                subscribedPlatforms: ["yahoonews"],
                items: [recentAiko, recentHaruka],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil,
                lastBackendRefreshAt: nil
            )
        )
    }

    func testForegroundRefreshLaunchesForCustomOnlyScope() {
        let now = Date(timeIntervalSince1970: 1_800)
        let customUrl = CustomUrl(
            id: "custom:https%3A%2F%2Fexample.com%2Ffeed",
            url: "https://example.com/feed",
            title: "Feed",
            added_at: "1970-01-01T00:00:00Z"
        )
        let activeTerm = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let stale = FeedItem(
            id: "custom:old", platform: "custom", url: "https://example.com/old",
            title: "Old", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:20:00Z",
            watch_term_keyword: "", fetched_at: "1970-01-01T00:20:00Z"
        )

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [],
                customUrls: [],
                items: [],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil
            )
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [],
                customUrls: [customUrl],
                items: [],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil
            )
        )
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [],
                customUrls: [customUrl],
                items: [],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: now.addingTimeInterval(-60)
            ),
            "A recent completed custom-only check should not refresh again on every foreground activation."
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [activeTerm],
                customUrls: [],
                items: [],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil
            )
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [],
                customUrls: [],
                items: [stale],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil
            )
        )
    }

    func testFeedScopeInvalidationForcesRefreshWithRecentCachedFeed() {
        let now = Date(timeIntervalSince1970: 1_800)
        let recent = FeedItem(
            id: "youtube:recent", platform: "youtube", url: "https://example.com/recent",
            title: "Recent", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: "1970-01-01T00:29:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:29:00Z",
            source: "youtube_api"
        )
        let activeTerm = WatchTerm(keyword: "Aiko", collection_mode: .allInfo)
        let recentCompletion = now.addingTimeInterval(-60)

        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [activeTerm],
                customUrls: [],
                items: [recent],
                pulledNewTerms: false,
                now: now,
                lastRefreshAt: nil
            )
        )

        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()

        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [activeTerm],
                customUrls: [],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "Changing feed scope should force refresh even when existing cached rows are recent."
        )
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [recent], now: now),
            "Suspension refresh should also see the pending feed-scope refresh."
        )

        BackgroundRefreshPolicy.recordBackendRefreshCompleted(at: recentCompletion)
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [activeTerm],
                customUrls: [],
                items: [recent],
                pulledNewTerms: false,
                now: now
            ),
            "Backend-only completion should not clear pending device/foreground scope refresh."
        )

        BackgroundRefreshPolicy.recordRefreshCompleted(at: recentCompletion)
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: [activeTerm],
                customUrls: [],
                items: [recent],
                pulledNewTerms: false,
                now: now
            )
        )
    }

    func testFeedScopeChangesInvalidateRecordedRefreshCompletions() {
        let now = Date(timeIntervalSince1970: 1_800)
        let stale = FeedItem(
            id: "yahoonews:stale", platform: "yahoonews", url: "https://example.com/stale",
            title: "Stale", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:20:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:20:00Z"
        )
        let recentCompletion = now.addingTimeInterval(-60)

        func recordRecentCompletions() {
            BackgroundRefreshPolicy.recordRefreshCompleted(at: recentCompletion)
            BackgroundRefreshPolicy.recordBackendRefreshCompleted(at: recentCompletion)
            XCTAssertFalse(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [stale], now: now))
            XCTAssertFalse(BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [stale], now: now))
        }

        func assertCompletionsInvalidated(_ message: String) {
            XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [stale], now: now), message)
            XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [stale], now: now), message)
        }

        recordRecentCompletions()
        let term = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        assertCompletionsInvalidated("Adding a watch term changes the feed scope.")

        recordRecentCompletions()
        db.updateTerm(id: term.id, aliases: ["Aiko Chan"])
        assertCompletionsInvalidated("Changing aliases changes strict matching scope.")

        recordRecentCompletions()
        db.updateTerm(id: term.id, notifyOnNew: false)
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [stale], now: now),
            "Notification-only changes should not force a feed refresh."
        )

        recordRecentCompletions()
        db.updateTerm(id: term.id, isActive: false)
        assertCompletionsInvalidated("Changing active state changes the feed scope.")

        recordRecentCompletions()
        db.setSubscribedPlatforms(platforms: ["news", "custom"])
        assertCompletionsInvalidated("Changing subscribed platforms changes the feed scope.")

        recordRecentCompletions()
        db.addCustomUrl(url: "https://feed.example.com/rss", title: "Feed")
        assertCompletionsInvalidated("Adding a custom URL changes the feed scope.")

        let customId = db.customUrls.first!.id
        recordRecentCompletions()
        db.removeCustomUrl(id: customId)
        assertCompletionsInvalidated("Removing a custom URL changes the feed scope.")

        recordRecentCompletions()
        db.deleteTerm(id: term.id)
        assertCompletionsInvalidated("Deleting a watch term changes the feed scope.")
    }

    func testStartupFeedScopeRepairsInvalidateRecordedRefreshCompletions() throws {
        let schemaVersionKey = "localdb_schema_version"
        let originalSchemaVersion = UserDefaults.standard.object(forKey: schemaVersionKey)
        defer {
            if let originalSchemaVersion {
                UserDefaults.standard.set(originalSchemaVersion, forKey: schemaVersionKey)
            } else {
                UserDefaults.standard.removeObject(forKey: schemaVersionKey)
            }
        }

        UserDefaults.standard.set(3, forKey: schemaVersionKey)
        let now = Date(timeIntervalSince1970: 1_800)
        let stale = FeedItem(
            id: "news:stale", platform: "news", url: "https://example.com/stale",
            title: "Stale", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: "1970-01-01T00:20:00Z",
            watch_term_keyword: "Aiko", fetched_at: "1970-01-01T00:20:00Z"
        )
        let startupDir = tempDir.appendingPathComponent("startup-repair")
        try FileManager.default.createDirectory(at: startupDir, withIntermediateDirectories: true)
        try JSONEncoder().encode([stale])
            .write(to: startupDir.appendingPathComponent("feed_items.json"))

        BackgroundRefreshPolicy.recordRefreshCompleted(at: now.addingTimeInterval(-60))
        BackgroundRefreshPolicy.recordBackendRefreshCompleted(at: now.addingTimeInterval(-60))
        XCTAssertFalse(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [stale], now: now))
        XCTAssertFalse(BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [stale], now: now))

        _ = LocalDB(directory: startupDir)

        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [stale], now: now))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(items: [stale], now: now))
    }

    func testBackgroundRefreshNotificationItemsAreNewSurvivingAndDeduplicated() {
        let now = ISO8601DateFormatter().string(from: Date())
        let existing = FeedItem(
            id: "youtube:existing", platform: "youtube", url: "https://example.com/existing",
            title: "Existing", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )
        let fresh = FeedItem(
            id: "youtube:fresh", platform: "youtube", url: "https://example.com/fresh",
            title: "Fresh", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )
        let evicted = FeedItem(
            id: "youtube:evicted", platform: "youtube", url: "https://example.com/evicted",
            title: "Evicted", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )
        let redirectOnly = FeedItem(
            id: "youtube:redirect-only",
            platform: "youtube",
            url: "https://backend.example.com/api/feed/matches/102/redirect",
            title: "Redirect-only",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_api"
        )

        let candidates = BackgroundRefreshPolicy.notificationItems(
            incoming: [existing, fresh, fresh, evicted, redirectOnly],
            existingKeys: [BackgroundRefreshPolicy.itemKey(existing)],
            survivingKeys: [
                BackgroundRefreshPolicy.itemKey(existing),
                BackgroundRefreshPolicy.itemKey(fresh),
                BackgroundRefreshPolicy.itemKey(redirectOnly),
            ]
        )

        XCTAssertEqual(candidates.map(\.id), ["youtube:fresh"])
    }

    func testBatchedNotificationSnapshotsIgnoreEarlierNonSurvivors() {
        let now = ISO8601DateFormatter().string(from: Date())
        let existing = FeedItem(
            id: "youtube:existing", platform: "youtube", url: "https://example.com/existing",
            title: "Existing", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )
        let prunedDuplicate = FeedItem(
            id: "youtube:duplicate", platform: "youtube", url: "https://news.google.com/rss/articles/duplicate",
            title: "Pruned duplicate", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "google_news"
        )
        let survivingDuplicate = FeedItem(
            id: prunedDuplicate.id, platform: "youtube", url: "https://youtube.com/watch?v=duplicate",
            title: "Surviving duplicate", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )

        let duplicateKey = BackgroundRefreshPolicy.itemKey(survivingDuplicate)
        let hiddenKeys = Set<String>()
        let snapshots = BackgroundRefreshPolicy.existingKeySnapshotsForBatchedNotificationAppend(
            itemBatches: [
                [prunedDuplicate].filter {
                    FeedItemPolicy.isMergeEligibleIncomingItem(
                        $0,
                        hiddenItems: hiddenKeys,
                        itemKey: BackgroundRefreshPolicy.itemKey
                    )
                },
                [survivingDuplicate].filter {
                    FeedItemPolicy.isMergeEligibleIncomingItem(
                        $0,
                        hiddenItems: hiddenKeys,
                        itemKey: BackgroundRefreshPolicy.itemKey
                    )
                },
                [survivingDuplicate].filter {
                    FeedItemPolicy.isMergeEligibleIncomingItem(
                        $0,
                        hiddenItems: hiddenKeys,
                        itemKey: BackgroundRefreshPolicy.itemKey
                    )
                },
            ],
            initialKeys: [BackgroundRefreshPolicy.itemKey(existing)],
            survivingKeys: [
                BackgroundRefreshPolicy.itemKey(existing),
                duplicateKey,
            ]
        )

        XCTAssertFalse(snapshots[0].contains(duplicateKey))
        XCTAssertFalse(snapshots[1].contains(duplicateKey))
        XCTAssertTrue(snapshots[2].contains(duplicateKey))
    }

    @MainActor
    func testBackgroundFallbackCanSkipAttachments() async throws {
        let center = MockNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center, initialRegisteredDeviceToken: nil)
        let now = ISO8601DateFormatter().string(from: Date())
        let term = WatchTerm(id: "t1", keyword: "Aiko", notify_on_new: true)
        let item = FeedItem(
            id: "youtube:background", platform: "youtube", url: "https://example.com/item",
            title: "Background item", content_text: nil, author: nil,
            thumbnail_url: "https://example.invalid/never-downloaded.jpg",
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )

        await manager.notifyForNewItems([item], terms: [term], includeAttachments: false)

        XCTAssertEqual(center.requests.count, 1)
        XCTAssertTrue(center.requests[0].content.attachments.isEmpty)
    }

    @MainActor
    func testNotificationNavigationUsesRemotePreviewPayload() throws {
        let manager = NotificationNavigationManager.shared
        manager.selectedItem = nil
        let itemID = "youtube:\(UUID().uuidString)"

        manager.openNotification(userInfo: [
            "watch_term_keyword": "Aiko",
            "new_count": 2,
            "thumbnail_url": "https://img.example.com/thumb.jpg",
            "preview_item": [
                "id": itemID,
                "platform": "youtube",
                "url": "https://youtube.com/watch?v=preview",
                "title": "Preview title",
                "content_text": "Preview description",
                "author": "Aiko Channel",
                "media_type": "video",
                "published_at": "2026-06-17T12:00:00Z",
            ],
        ])

        XCTAssertEqual(manager.selectedItem?.id, itemID)
        XCTAssertEqual(manager.selectedItem?.url, "https://youtube.com/watch?v=preview")
        XCTAssertEqual(manager.selectedItem?.title, "Preview title")
        XCTAssertEqual(manager.selectedItem?.content_text, "Preview description")
        XCTAssertEqual(manager.selectedItem?.author, "Aiko Channel")
        XCTAssertEqual(manager.selectedItem?.thumbnail_url, "https://img.example.com/thumb.jpg")
        XCTAssertEqual(manager.selectedItem?.media_type, "video")
        XCTAssertEqual(manager.selectedItem?.published_at, "2026-06-17T12:00:00Z")
        XCTAssertEqual(manager.selectedItem?.watch_term_keyword, "Aiko")
        manager.selectedItem = nil
    }

    @MainActor
    func testNotificationNavigationInfersFiveChWhenPayloadPlatformWasTrimmed() throws {
        let manager = NotificationNavigationManager.shared
        manager.selectedItem = nil
        let itemID = "5ch:mevius:nogizaka:1782410369"

        manager.openNotification(userInfo: [
            "item_id": itemID,
            "item_url": "https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369",
            "item_title": "5ch push title",
            "watch_term_keyword": "Aiko",
        ])

        XCTAssertEqual(manager.selectedItem?.id, itemID)
        XCTAssertEqual(manager.selectedItem?.platform, "5ch")
        XCTAssertTrue(ReaderView.usesSystemSafari(for: try XCTUnwrap(manager.selectedItem)))
        manager.selectedItem = nil
    }

    @MainActor
    func testNotificationNavigationInfersFiveChMirrorWhenPayloadPlatformWasTrimmed() throws {
        let manager = NotificationNavigationManager.shared
        manager.selectedItem = nil
        let itemID = "2ch.sc:hayabusa3.2ch.sc:mnewsplus:1782467821"

        manager.openNotification(userInfo: [
            "item_id": itemID,
            "item_url": "http://hayabusa3.2ch.sc/test/read.cgi/mnewsplus/1782467821/",
            "item_title": "5ch mirror push title",
            "watch_term_keyword": "Aiko",
        ])

        XCTAssertEqual(manager.selectedItem?.id, itemID)
        XCTAssertEqual(manager.selectedItem?.platform, "5ch")
        XCTAssertTrue(ReaderView.usesSystemSafari(for: try XCTUnwrap(manager.selectedItem)))
        manager.selectedItem = nil
    }

    @MainActor
    func testNotificationNavigationInfersFiveChRootHostWhenPayloadPlatformWasTrimmed() throws {
        let manager = NotificationNavigationManager.shared
        manager.selectedItem = nil
        let itemID = "opaque-google-news-id"

        manager.openNotification(userInfo: [
            "item_id": itemID,
            "item_url": "https://5ch.net/t1",
            "item_title": "5ch root host push title",
            "watch_term_keyword": "Aiko",
        ])

        XCTAssertEqual(manager.selectedItem?.id, itemID)
        XCTAssertEqual(manager.selectedItem?.platform, "5ch")
        XCTAssertTrue(ReaderView.usesSystemSafari(for: try XCTUnwrap(manager.selectedItem)))
        manager.selectedItem = nil
    }

    @MainActor
    func testNotificationNavigationInfersFiveChMirrorRootHostWhenPayloadPlatformWasTrimmed() throws {
        let manager = NotificationNavigationManager.shared
        manager.selectedItem = nil
        let itemID = "opaque-google-news-id"

        manager.openNotification(userInfo: [
            "item_id": itemID,
            "item_url": "https://2ch.sc/test/read.cgi/mnewsplus/1782467821/",
            "item_title": "5ch mirror root push title",
            "watch_term_keyword": "Aiko",
        ])

        XCTAssertEqual(manager.selectedItem?.id, itemID)
        XCTAssertEqual(manager.selectedItem?.platform, "5ch")
        XCTAssertTrue(ReaderView.usesSystemSafari(for: try XCTUnwrap(manager.selectedItem)))
        manager.selectedItem = nil
    }

    @MainActor
    func testNotificationNavigationMergesRemotePreviewPayloadIntoFeed() throws {
        let manager = NotificationNavigationManager.shared
        let itemID = "youtube:\(UUID().uuidString)"
        let keyword = "Merge Test \(UUID().uuidString)"

        let userInfo: [AnyHashable: Any] = [
            "watch_term_keyword": keyword,
            "new_count": 1,
            "thumbnail_url": "https://img.example.com/merged.jpg",
            "preview_item": [
                "id": itemID,
                "platform": "youtube",
                "url": "https://youtube.com/watch?v=merged",
                "title": "Merged notification title",
                "content_text": "Merged notification description",
                "author": "Aiko Channel",
                "media_type": "video",
                "published_at": "2026-06-20T12:00:00Z",
                "source": "youtube_api",
            ],
        ]

        XCTAssertTrue(manager.mergeNotificationItem(userInfo: userInfo))
        let merged = LocalDB.shared.feedItems.first {
            $0.id == itemID && $0.watch_term_keyword == keyword
        }
        let unwrappedMerged = try XCTUnwrap(merged)
        XCTAssertEqual(unwrappedMerged.url, "https://youtube.com/watch?v=merged")
        XCTAssertEqual(unwrappedMerged.title, "Merged notification title")
        XCTAssertEqual(unwrappedMerged.content_text, "Merged notification description")
        XCTAssertEqual(unwrappedMerged.author, "Aiko Channel")
        XCTAssertEqual(unwrappedMerged.thumbnail_url, "https://img.example.com/merged.jpg")
        XCTAssertEqual(unwrappedMerged.media_type, "video")
        XCTAssertEqual(unwrappedMerged.published_at, "2026-06-20T12:00:00Z")
        XCTAssertEqual(unwrappedMerged.source, "youtube_api")
        XCTAssertFalse(FeedItemPolicy.shouldPruneLegacyYouTubeItem(unwrappedMerged))

        LocalDB.shared.deleteFeedItem(id: itemID, watchTermKeyword: keyword)
    }

    @MainActor
    func testNotificationNavigationInfersYouTubeWhenPayloadPlatformWasTrimmed() throws {
        let manager = NotificationNavigationManager.shared
        let itemID = "youtube:\(UUID().uuidString)"
        let keyword = "Trimmed YouTube \(UUID().uuidString)"

        let userInfo: [AnyHashable: Any] = [
            "watch_term_keyword": keyword,
            "item_id": itemID,
            "item_url": "https://backend.example.com/api/feed/matches/123/redirect",
            "item_source": "youtube_api",
            "preview_item": [
                "id": itemID,
                "match_id": "123",
                "url": "https://backend.example.com/api/feed/matches/123/redirect",
                "source": "youtube_api",
            ],
        ]

        XCTAssertTrue(manager.mergeNotificationItem(userInfo: userInfo))
        let merged = try XCTUnwrap(LocalDB.shared.feedItems.first {
            $0.id == itemID && $0.watch_term_keyword == keyword
        })
        XCTAssertEqual(merged.platform, "youtube")
        XCTAssertEqual(merged.source, "youtube_api")
        XCTAssertFalse(FeedItemPolicy.shouldPruneLegacyYouTubeItem(merged))

        LocalDB.shared.deleteFeedItem(id: itemID, watchTermKeyword: keyword)
    }

    func testNotificationNavigationPrefersPushOverStaleCachedItem() throws {
        let cached = FeedItem(
            id: "youtube:shared",
            platform: "youtube",
            url: "https://youtube.com/watch?v=old",
            title: "Old cached title",
            content_text: "Full cached description",
            author: "Cached author",
            thumbnail_url: nil,
            media_type: "video",
            published_at: "2026-06-01T12:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-01T12:05:00Z",
            source: "youtube_api"
        )
        let notification = FeedItem(
            id: "youtube:shared",
            platform: "youtube",
            url: "https://backend.example.com/api/feed/matches/99/redirect",
            title: "Fresh push title",
            content_text: nil,
            author: nil,
            thumbnail_url: "https://img.example.com/fresh.jpg",
            media_type: "video",
            published_at: "2026-06-18T12:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-18T12:01:00Z",
            source: "youtube_api"
        )

        let resolved = NotificationNavigationManager.preferredNotificationItem(
            notification,
            cachedItems: [cached]
        )

        XCTAssertEqual(resolved.url, notification.url)
        XCTAssertEqual(resolved.title, "Fresh push title")
        XCTAssertEqual(resolved.published_at, notification.published_at)
        XCTAssertEqual(resolved.thumbnail_url, notification.thumbnail_url)
        XCTAssertEqual(resolved.content_text, "Full cached description")
    }

    func testNotificationNavigationPreservesCachedMetadataOmittedFromPayload() throws {
        let cached = FeedItem(
            id: "youtube:trimmed",
            platform: "youtube",
            url: "https://youtube.com/watch?v=trimmed",
            title: "Cached title",
            content_text: "Cached description",
            author: "Cached author",
            thumbnail_url: nil,
            media_type: "video",
            published_at: "2026-06-01T12:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-01T12:05:00Z",
            source: "youtube_api"
        )
        let payloadDefaults = FeedItem(
            id: cached.id,
            platform: "web",
            url: "https://backend.example.com/api/feed/matches/101/redirect",
            title: "Push title",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: "2026-06-19T00:00:00Z",
            watch_term_keyword: "",
            fetched_at: "2026-06-19T00:00:00Z"
        )

        let resolved = NotificationNavigationManager.preferredNotificationItem(
            payloadDefaults,
            cachedItems: [cached],
            hasPlatform: false,
            hasMediaType: false,
            hasPublishedAt: false,
            hasWatchTermKeyword: false
        )

        XCTAssertEqual(resolved.platform, cached.platform)
        XCTAssertEqual(resolved.media_type, cached.media_type)
        XCTAssertEqual(resolved.published_at, cached.published_at)
        XCTAssertEqual(resolved.watch_term_keyword, cached.watch_term_keyword)
        XCTAssertEqual(resolved.url, payloadDefaults.url)
        XCTAssertEqual(resolved.title, payloadDefaults.title)
    }

    func testIncrementalSinceIncludesOverlapForClockSkew() throws {
        let latest = "2026-06-18T12:30:00Z"
        let item = FeedItem(
            id: "youtube:cursor",
            platform: "youtube",
            url: "https://example.com/cursor",
            title: "Cursor",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: latest,
            watch_term_keyword: "Aiko",
            fetched_at: latest,
            source: "youtube_api"
        )

        XCTAssertEqual(
            BackgroundRefreshPolicy.incrementalSince(in: [item]),
            "2026-06-18T12:15:00Z"
        )
        XCTAssertNil(
            BackgroundRefreshPolicy.incrementalSince(
                in: [item],
                platformId: "news"
            )
        )
    }

    func testActivityDatePlatformsUseDateWindowRefresh() throws {
        XCTAssertTrue(BackgroundRefreshPolicy.shouldUseDateWindowForPlatformRefresh("5ch"))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldUseDateWindowForPlatformRefresh("girlschannel"))
        XCTAssertTrue(BackgroundRefreshPolicy.shouldUseDateWindowForPlatformRefresh("togetter"))
        XCTAssertFalse(BackgroundRefreshPolicy.shouldUseDateWindowForPlatformRefresh("youtube"))
    }

    func testIncrementalSinceIgnoresLocalOnlyCursorItems() throws {
        let backendItem = FeedItem(
            id: "youtube:cursor",
            platform: "youtube",
            url: "https://example.com/backend",
            title: "Backend",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: "2026-06-18T11:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-18T11:00:00Z",
            source: "youtube_api"
        )
        let localCustomItem = FeedItem(
            id: "custom:local",
            platform: "custom",
            url: "https://example.com/custom",
            title: "Custom",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: "2026-06-18T13:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-18T13:00:00Z"
        )
        let localGoogleNewsFallback = FeedItem(
            id: "news:gnews:local",
            platform: "news",
            url: "https://example.com/gnews",
            title: "Google News fallback",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: "2026-06-18T14:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-18T14:00:00Z"
        )
        let localNHKFallback = FeedItem(
            id: "news:nhk:local",
            platform: "news",
            url: "https://example.com/nhk",
            title: "NHK fallback",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: "2026-06-18T15:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-18T15:00:00Z"
        )

        XCTAssertEqual(
            BackgroundRefreshPolicy.incrementalSince(
                in: [localCustomItem, localGoogleNewsFallback, localNHKFallback, backendItem]
            ),
            "2026-06-18T10:45:00Z"
        )
    }

    func testIncrementalSinceIgnoresBackendMatchRedirectItems() throws {
        let redirectOnly = FeedItem(
            id: "youtube:redirect-cursor",
            platform: "youtube",
            url: "https://backend.example.com/api/feed/matches/103/redirect",
            title: "Redirect cursor",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: "2026-06-18T11:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-18T12:30:00Z",
            source: "youtube_api"
        )
        let stable = FeedItem(
            id: "youtube:stable-cursor",
            platform: "youtube",
            url: "https://youtube.com/watch?v=stable-cursor",
            title: "Stable cursor",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: "2026-06-18T10:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-18T11:15:00Z",
            source: "youtube_api"
        )

        XCTAssertEqual(
            BackgroundRefreshPolicy.incrementalSince(in: [redirectOnly, stable]),
            "2026-06-18T11:00:00Z"
        )
        XCTAssertNil(BackgroundRefreshPolicy.incrementalSince(in: [redirectOnly]))
    }

    @MainActor
    func testNotificationNavigationCanSaveRemotePreviewPayload() throws {
        let manager = NotificationNavigationManager.shared
        let itemID = "note:\(UUID().uuidString)"
        LocalDB.shared.removeSaved(id: itemID)

        let userInfo: [AnyHashable: Any] = [
            "watch_term_keyword": "Aiko",
            "preview_item": [
                "id": itemID,
                "platform": "note",
                "url": "https://note.com/preview",
                "title": "Saved from notification",
                "media_type": "article",
                "published_at": "2026-06-17T12:00:00Z",
            ],
        ]

        XCTAssertTrue(manager.saveNotificationItem(userInfo: userInfo))
        XCTAssertTrue(LocalDB.shared.savedPages.contains(where: { $0.id == itemID }))
        XCTAssertFalse(manager.saveNotificationItem(userInfo: userInfo))

        LocalDB.shared.removeSaved(id: itemID)
    }

    @MainActor
    func testNotificationCategoriesIncludeOpenAndSaveActions() throws {
        let center = MockNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center)

        manager.registerNotificationCategories()

        let category = center.categories.first(where: {
            $0.identifier == NotificationManager.resultPreviewCategoryIdentifier
        })
        XCTAssertNotNil(category)
        let actionIDs = Set(category?.actions.map(\.identifier) ?? [])
        XCTAssertTrue(actionIDs.contains(NotificationManager.openResultActionIdentifier))
        XCTAssertTrue(actionIDs.contains(NotificationManager.saveResultActionIdentifier))
    }

    func testSchemeUsesExpectedBackendConfiguration() throws {
        switch NetworkManager.shared.environmentName {
        case "Local":
            XCTAssertEqual(NetworkManager.shared.apiBase, "http://127.0.0.1:8000")
        case "Development", "Staging", "Production":
            XCTAssertEqual(NetworkManager.shared.apiBase, "https://oshireader.onrender.com")
        default:
            XCTFail("Unexpected backend environment: \(NetworkManager.shared.environmentName)")
        }
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
                title: "Enabled first", content_text: "Expanded message preview",
                author: "Source label that must stay hidden",
                thumbnail_url: "https://img.example.com/preview.jpg",
                media_type: "video", published_at: nowString, watch_term_keyword: enabledTerm.keyword,
                fetched_at: nowString,
                source: "youtube_api"
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

        await manager.notifyForNewItems(
            items,
            terms: [enabledTerm, disabledTerm],
            includeAttachments: false
        )

        XCTAssertEqual(center.requests.count, 1)
        XCTAssertEqual(center.requests.first?.content.title, I18nManager.shared.tFormat("notifNewItemsTitle", "Enabled Oshi"))
        XCTAssertEqual(center.requests.first?.content.body, "Enabled first\n\(I18nManager.shared.tFormat("notifNewItemsMoreFmt", 1))")
        XCTAssertEqual(center.requests.first?.content.subtitle, "")
        XCTAssertEqual(center.requests.first?.content.categoryIdentifier, NotificationManager.resultPreviewCategoryIdentifier)
        XCTAssertEqual(center.requests.first?.content.threadIdentifier, "oshireader-enabled oshi")
        XCTAssertEqual(center.requests.first?.content.targetContentIdentifier, "youtube:enabled-1")
        XCTAssertEqual(center.requests.first?.content.userInfo["item_url"] as? String, "https://youtube.com/1")
        XCTAssertEqual(center.requests.first?.content.userInfo["item_media_type"] as? String, "video")
        XCTAssertEqual(center.requests.first?.content.userInfo["item_published_at"] as? String, nowString)
        let previewItem = center.requests.first?.content.userInfo["preview_item"] as? [String: Any]
        XCTAssertEqual(previewItem?["id"] as? String, "youtube:enabled-1")
        XCTAssertEqual(previewItem?["url"] as? String, "https://youtube.com/1")
        XCTAssertEqual(previewItem?["platform"] as? String, "youtube")
        XCTAssertEqual(previewItem?["title"] as? String, "Enabled first")
        XCTAssertEqual(previewItem?["content_text"] as? String, "Expanded message preview")
        XCTAssertEqual(previewItem?["thumbnail_url"] as? String, "https://img.example.com/preview.jpg")
        XCTAssertEqual(
            center.requests.first?.content.userInfo["thumbnail_url"] as? String,
            "https://img.example.com/preview.jpg"
        )
        XCTAssertEqual(center.requests.first?.content.userInfo["new_count"] as? Int, 2)
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
            fetched_at: nowString,
            source: "youtube_api"
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
            watch_term_keyword: "Oshi", fetched_at: nowString,
            source: "youtube_api"
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
                     published_at: nowString, watch_term_keyword: "Oshi A", fetched_at: nowString, source: "youtube_api"),
            FeedItem(id: "2", platform: "youtube", url: "https://u/2", title: "A2",
                     content_text: nil, author: nil, thumbnail_url: nil, media_type: "video",
                     published_at: nowString, watch_term_keyword: "Oshi A", fetched_at: nowString, source: "youtube_api"),
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
        XCTAssertEqual(bodyA, "A1\n\(I18nManager.shared.tFormat("notifNewItemsMoreFmt", 1))")
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
            fetched_at: nowString,
            source: "youtube_api"
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
                published_at: ts, watch_term_keyword: "Oshi", fetched_at: ts,
                source: "youtube_api"
            )
        }
        let evictedTs = fmt.string(from: base)
        items.append(FeedItem(
            id: "cap:evicted", platform: "youtube", url: "https://u/evicted", title: "Evicted",
            content_text: nil, author: nil, thumbnail_url: nil, media_type: "video",
            published_at: evictedTs, watch_term_keyword: "Oshi", fetched_at: evictedTs,
            source: "youtube_api"
        ))

        _ = db.mergeItems(newItems: items)

        XCTAssertEqual(db.feedItems.count, 600)
        XCTAssertFalse(db.feedItems.contains { $0.id == "cap:evicted" },
                       "Oldest item should be evicted by the 600-item cap and not stored (and therefore not notified)")
    }

    func testPreservedIncomingItemsSurviveFeedCap() {
        db.setSubscribedPlatforms(platforms: ["youtube"])
        let fmt = ISO8601DateFormatter()
        let base = Date(timeIntervalSinceReferenceDate: 0)
        let existingItems: [FeedItem] = (1...600).map { i in
            let ts = fmt.string(from: base.addingTimeInterval(Double(i) * 60))
            return FeedItem(
                id: "cap:existing:\(i)", platform: "youtube", url: "https://u/\(i)",
                title: "Existing \(i)", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "video", published_at: ts, watch_term_keyword: "Oshi", fetched_at: ts,
                source: "youtube_api"
            )
        }
        _ = db.mergeItems(newItems: existingItems)

        let olderTs = fmt.string(from: base.addingTimeInterval(-60))
        let notificationItem = FeedItem(
            id: "cap:notification", platform: "youtube",
            url: "https://youtube.com/watch?v=preserved",
            title: "Preserved notification", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: olderTs, watch_term_keyword: "Oshi", fetched_at: olderTs,
            source: "youtube_api"
        )

        _ = db.mergeItems(
            newItems: [notificationItem],
            notifyOnNew: false,
            preserveIncomingItems: true
        )

        XCTAssertEqual(db.feedItems.count, 600)
        XCTAssertTrue(db.feedItems.contains { $0.id == notificationItem.id })
    }

    func testCappedFeedItemsRetainMoreDiscussionPlatformItems() {
        db.setSubscribedPlatforms(platforms: ["news", "5ch"])
        let fmt = ISO8601DateFormatter()
        let base = Date(timeIntervalSinceReferenceDate: 0)

        let newsItems: [FeedItem] = (1...600).map { i in
            let ts = fmt.string(from: base.addingTimeInterval(Double(i) * 60))
            return FeedItem(
                id: "news:\(i)", platform: "news", url: "https://news.example.com/\(i)",
                title: "Aiko news item \(i)", content_text: "Aiko news content", author: nil,
                thumbnail_url: nil, media_type: "article", published_at: ts,
                watch_term_keyword: "Aiko", fetched_at: ts
            )
        }
        let fiveChItems: [FeedItem] = (1...30).map { i in
            let ts = fmt.string(from: base.addingTimeInterval(Double(i) * 30))
            return FeedItem(
                id: "5ch:\(i)", platform: "5ch", url: "https://example.5ch.net/test/read.cgi/thread/\(i)",
                title: "Aiko thread \(i)", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "article", published_at: ts, watch_term_keyword: "Aiko", fetched_at: ts
            )
        }

        _ = db.mergeItems(newItems: newsItems + fiveChItems)

        XCTAssertEqual(db.feedItems.count, 600)
        XCTAssertEqual(db.feedItems.filter { $0.platform == "5ch" }.count, 25)
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
            watch_term_keyword: "Aiko", fetched_at: formatter.string(from: now),
            source: "youtube_api"
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
        
        // news uses strict keyword matching — this item has keyword "Miku" but no Miku content
        let itemStrictMismatch = FeedItem(
            id: "news:mismatch", platform: "news", url: "https://news.example.com/aiko",
            title: "Aiko show article", content_text: "Only contains Aiko text", author: "News",
            thumbnail_url: nil, media_type: "article", published_at: formatter.string(from: now),
            watch_term_keyword: "Miku", fetched_at: formatter.string(from: now)
        )

        _ = db.mergeItems(newItems: [itemNow, itemYesterday, itemOld, itemStrictMismatch])

        // Verifies platform subscription is active
        db.setSubscribedPlatforms(platforms: ["youtube", "tver", "yahoonews", "news"])
        
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

    func testQueryFeedAlwaysLimitsYouTubeToRecentUploads() throws {
        let formatter = ISO8601DateFormatter()
        let now = Date()
        let recent = Calendar.current.date(byAdding: .day, value: -2, to: now)!
        let old = Calendar.current.date(byAdding: .day, value: -60, to: now)!
        db.setSubscribedPlatforms(platforms: ["youtube", "yahoonews"])
        _ = db.saveTerm(keyword: "Aiko")

        let recentYouTube = FeedItem(
            id: "youtube:recent-upload", platform: "youtube", url: "https://youtube.com/watch?v=recent",
            title: "Aiko recent upload", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: formatter.string(from: recent),
            watch_term_keyword: "Aiko", fetched_at: formatter.string(from: now),
            source: "youtube_api"
        )
        let oldYouTube = FeedItem(
            id: "youtube:old-upload", platform: "youtube", url: "https://youtube.com/watch?v=old",
            title: "Aiko old upload", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: formatter.string(from: old),
            watch_term_keyword: "Aiko", fetched_at: formatter.string(from: now),
            source: "youtube_api"
        )
        let oldArticle = FeedItem(
            id: "yahoonews:old", platform: "yahoonews", url: "https://news.example.com/old",
            title: "Aiko old article", content_text: "Aiko old article", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: formatter.string(from: old),
            watch_term_keyword: "Aiko", fetched_at: formatter.string(from: now)
        )

        _ = db.mergeItems(newItems: [recentYouTube, oldYouTube, oldArticle])

        let ninetyDayIds = db.queryFeed(keyword: "Aiko", days: 90).map(\.id)
        XCTAssertTrue(ninetyDayIds.contains(recentYouTube.id))
        XCTAssertTrue(ninetyDayIds.contains(oldArticle.id))
        XCTAssertFalse(ninetyDayIds.contains(oldYouTube.id))

        let allTimeIds = db.queryFeed(keyword: "Aiko", days: 0).map(\.id)
        XCTAssertTrue(allTimeIds.contains(recentYouTube.id))
        XCTAssertTrue(allTimeIds.contains(oldArticle.id))
        XCTAssertFalse(allTimeIds.contains(oldYouTube.id))
    }

    func testQueryFeedPerformanceWithRepresentativeCache() throws {
        let formatter = ISO8601DateFormatter()
        let publishedAt = formatter.string(from: Date())
        let platforms = ["news", "tver", "yahoonews", "custom"]
        let items = (0..<600).map { index in
            let platform = platforms[index % platforms.count]
            return FeedItem(
                id: "\(platform):performance-\(index)",
                platform: platform,
                url: "https://performance.example/\(index)",
                title: "Aiko performance item \(index)",
                content_text: "Representative cached feed content",
                author: "Test",
                thumbnail_url: nil,
                media_type: platform == "custom" ? "article" : "video",
                published_at: publishedAt,
                watch_term_keyword: platform == "custom" ? "" : "Aiko",
                fetched_at: publishedAt,
                source: nil
            )
        }
        db.feedItems = items

        // Exclude first-use formatter/cache initialization from the steady-state
        // measurement so future comparisons are less noisy.
        let warmupResults = db.queryFeed(keyword: "Aiko", days: 30)
        XCTAssertEqual(warmupResults.count, 600)
        measure {
            for _ in 0..<10 {
                _ = db.queryFeed(keyword: "Aiko", days: 30)
            }
        }
    }

    func testMergeItemsPerformanceWithRepresentativeRefreshBatch() throws {
        let formatter = ISO8601DateFormatter()
        let publishedAt = formatter.string(from: Date())
        let existingItems = (0..<600).map { index in
            FeedItem(
                id: "news:existing-\(index)",
                platform: "news",
                url: "https://performance.example/existing-\(index)",
                title: "Existing refresh item \(index)",
                content_text: "Cached content",
                author: "Test",
                thumbnail_url: nil,
                media_type: "article",
                published_at: publishedAt,
                watch_term_keyword: "Aiko",
                fetched_at: publishedAt,
                source: nil
            )
        }
        let incomingItems = (0..<100).map { index in
            FeedItem(
                id: "news:incoming-\(index)",
                platform: "news",
                url: "https://performance.example/incoming-\(index)",
                title: "Incoming refresh item \(index)",
                content_text: "Fresh content",
                author: "Test",
                thumbnail_url: nil,
                media_type: "article",
                published_at: publishedAt,
                watch_term_keyword: "Aiko",
                fetched_at: publishedAt,
                source: nil
            )
        }

        db.feedItems = existingItems
        _ = db.mergeItems(newItems: incomingItems, notifyOnNew: false)
        XCTAssertEqual(db.feedItems.count, 600)

        db.feedItems = existingItems
        _ = db.mergeItems(newItems: incomingItems, notifyOnNew: false)
        measure {
            for _ in 0..<10 {
                db.feedItems = existingItems
                _ = db.mergeItems(newItems: incomingItems, notifyOnNew: false)
            }
        }
    }

    func testMergeItemsBatchedPerformanceWithRepresentativePlatformRefreshes() throws {
        let formatter = ISO8601DateFormatter()
        let publishedAt = formatter.string(from: Date())
        let existingItems = (0..<600).map { index in
            FeedItem(
                id: "news:batched-existing-\(index)",
                platform: "news",
                url: "https://performance.example/batched-existing-\(index)",
                title: "Existing batched refresh item \(index)",
                content_text: "Cached content",
                author: "Test",
                thumbnail_url: nil,
                media_type: "article",
                published_at: publishedAt,
                watch_term_keyword: "Aiko",
                fetched_at: publishedAt,
                source: nil
            )
        }
        let incomingBatches = (0..<5).map { batchIndex in
            (0..<20).map { itemIndex in
                FeedItem(
                    id: "news:batched-incoming-\(batchIndex)-\(itemIndex)",
                    platform: "news",
                    url: "https://performance.example/batched-incoming-\(batchIndex)-\(itemIndex)",
                    title: "Incoming batched refresh item \(batchIndex)-\(itemIndex)",
                    content_text: "Fresh content",
                    author: "Test",
                    thumbnail_url: nil,
                    media_type: "article",
                    published_at: publishedAt,
                    watch_term_keyword: "Aiko",
                    fetched_at: publishedAt,
                    source: nil
                )
            }
        }

        db.feedItems = existingItems
        let warmupCounts = db.mergeItemsBatched(
            newItemsBatches: incomingBatches,
            notifyOnNew: false
        )
        XCTAssertEqual(warmupCounts, [20, 20, 20, 20, 20])
        XCTAssertEqual(db.feedItems.count, 600)

        measure {
            for _ in 0..<10 {
                db.feedItems = existingItems
                _ = db.mergeItemsBatched(
                    newItemsBatches: incomingBatches,
                    notifyOnNew: false
                )
            }
        }
    }

    func testMergeItemsBatchedReturnsPerBatchAddedCounts() throws {
        let formatter = ISO8601DateFormatter()
        let publishedAt = formatter.string(from: Date())
        let makeItem = { (id: String) in
            FeedItem(
                id: "news:\(id)",
                platform: "news",
                url: "https://performance.example/\(id)",
                title: "Aiko \(id)",
                content_text: "Content",
                author: "Test",
                thumbnail_url: nil,
                media_type: "article",
                published_at: publishedAt,
                watch_term_keyword: "Aiko",
                fetched_at: publishedAt,
                source: nil
            )
        }

        let first = makeItem("batch-one")
        let second = makeItem("batch-two")
        let counts = db.mergeItemsBatched(
            newItemsBatches: [[first], [second, first]],
            notifyOnNew: false
        )

        XCTAssertEqual(counts, [1, 1])
        XCTAssertEqual(db.feedItems.map(\.id).sorted(), [first.id, second.id].sorted())
    }

    func testMergeItemsBatchedSkipsAllEmptyBatches() throws {
        let formatter = ISO8601DateFormatter()
        let publishedAt = formatter.string(from: Date())
        let existing = FeedItem(
            id: "news:existing-empty-batch",
            platform: "news",
            url: "https://performance.example/existing-empty-batch",
            title: "Aiko existing empty batch",
            content_text: "Content",
            author: "Test",
            thumbnail_url: nil,
            media_type: "article",
            published_at: publishedAt,
            watch_term_keyword: "Aiko",
            fetched_at: publishedAt,
            source: nil
        )
        db.feedItems = [existing]

        let counts = db.mergeItemsBatched(
            newItemsBatches: [[], [], []],
            notifyOnNew: false
        )

        XCTAssertEqual(counts, [0, 0, 0])
        XCTAssertEqual(db.feedItems, [existing])
    }

    func testMergeItemsBatchedEmptyBatchesStillPruneLegacyItems() throws {
        let formatter = ISO8601DateFormatter()
        let publishedAt = formatter.string(from: Date())
        let legacy = FeedItem(
            id: "youtube:gnews-empty-batch",
            platform: "youtube",
            url: "https://news.google.com/rss/articles/empty-batch",
            title: "Aiko legacy empty batch",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: publishedAt,
            watch_term_keyword: "Aiko",
            fetched_at: publishedAt,
            source: "google_news"
        )
        let current = FeedItem(
            id: "youtube:current-empty-batch",
            platform: "youtube",
            url: "https://youtube.com/watch?v=current-empty-batch",
            title: "Aiko current empty batch",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: publishedAt,
            watch_term_keyword: "Aiko",
            fetched_at: publishedAt,
            source: "youtube"
        )
        db.feedItems = [legacy, current]

        let counts = db.mergeItemsBatched(
            newItemsBatches: [[], []],
            notifyOnNew: false
        )

        XCTAssertEqual(counts, [0, 0])
        XCTAssertEqual(db.feedItems, [current])
    }

    func testMergePrunesLegacyYouTubeGoogleNewsFallbackItems() throws {
        let formatter = ISO8601DateFormatter()
        let now = formatter.string(from: Date())
        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.saveTerm(keyword: "Aiko")

        let legacyBySource = FeedItem(
            id: "youtube:gnews-source",
            platform: "youtube",
            url: "https://youtube.com/watch?v=old-source",
            title: "Aiko old source fallback",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "google_news"
        )
        let legacyByURL = FeedItem(
            id: "youtube:https://news.google.com/rss/articles/old-url",
            platform: "youtube",
            url: "https://news.google.com/rss/articles/old-url?oc=5",
            title: "Aiko old url fallback",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now
        )
        let direct = FeedItem(
            id: "youtube:direct",
            platform: "youtube",
            url: "https://youtube.com/watch?v=direct",
            title: "Aiko direct upload",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_scrape"
        )

        _ = db.mergeItems(newItems: [legacyBySource, legacyByURL, direct])

        XCTAssertEqual(db.feedItems.map(\.id), [direct.id])
        XCTAssertEqual(db.queryFeed(keyword: "Aiko", days: 0).map(\.id), [direct.id])
    }

    func testMergePrunesLegacyUnmarkedYouTubeItems() throws {
        let formatter = ISO8601DateFormatter()
        let now = formatter.string(from: Date())
        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.saveTerm(keyword: "Aiko")

        let legacyUnmarked = FeedItem(
            id: "youtube:legacy-unmarked",
            platform: "youtube",
            url: "https://youtube.com/watch?v=legacy-unmarked",
            title: "Aiko legacy unmarked",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now
        )
        let direct = FeedItem(
            id: "youtube:marked",
            platform: "youtube",
            url: "https://youtube.com/watch?v=marked",
            title: "Aiko marked upload",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_api"
        )

        _ = db.mergeItems(newItems: [legacyUnmarked, direct])

        XCTAssertEqual(db.feedItems.map(\.id), [direct.id])
        XCTAssertTrue(FeedItemPolicy.isLegacyUnmarkedYouTubeEstimate(legacyUnmarked))
    }

    func testStartupPrunesLegacyYouTubeGoogleNewsFallbackItems() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let legacy = FeedItem(
            id: "youtube:https://news.google.com/rss/articles/old-startup",
            platform: "youtube",
            url: "https://news.google.com/rss/articles/old-startup?oc=5",
            title: "Aiko old startup fallback",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "google_news"
        )
        let direct = FeedItem(
            id: "youtube:startup-direct",
            platform: "youtube",
            url: "https://youtube.com/watch?v=startup-direct",
            title: "Aiko direct startup upload",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_scrape"
        )
        try JSONEncoder().encode([legacy, direct]).write(
            to: tempDir.appendingPathComponent("feed_items.json")
        )
        try JSONEncoder().encode(["youtube"]).write(
            to: tempDir.appendingPathComponent("subscribed_platforms.json")
        )
        UserDefaults.standard.set(4, forKey: "localdb_schema_version")
        defer { UserDefaults.standard.removeObject(forKey: "localdb_schema_version") }

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertEqual(freshDB.feedItems.map(\.id), [direct.id])
        XCTAssertFalse(freshDB.feedItems.contains { FeedItemPolicy.isLegacyYouTubeGoogleNewsFallback($0) })
    }

    func testStartupPrunesLegacyUnmarkedYouTubeItems() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let legacy = FeedItem(
            id: "youtube:legacy-startup",
            platform: "youtube",
            url: "https://youtube.com/watch?v=legacy-startup",
            title: "Aiko legacy startup",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now
        )
        let direct = FeedItem(
            id: "youtube:marked-startup",
            platform: "youtube",
            url: "https://youtube.com/watch?v=marked-startup",
            title: "Aiko marked startup upload",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_api"
        )
        try JSONEncoder().encode([legacy, direct]).write(
            to: tempDir.appendingPathComponent("feed_items.json")
        )
        try JSONEncoder().encode(["youtube"]).write(
            to: tempDir.appendingPathComponent("subscribed_platforms.json")
        )
        UserDefaults.standard.set(4, forKey: "localdb_schema_version")
        defer { UserDefaults.standard.removeObject(forKey: "localdb_schema_version") }

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertEqual(freshDB.feedItems.map(\.id), [direct.id])
        XCTAssertFalse(freshDB.feedItems.contains { FeedItemPolicy.shouldPruneLegacyYouTubeItem($0) })
    }

    func testStrictArticleSourceIgnoresSummaryOnlyKeywordMatch() throws {
        db.setSubscribedPlatforms(platforms: ["livedoor", "realsound", "cinemacafe"])
        _ = db.saveTerm(keyword: "吉沢亮")
        let now = ISO8601DateFormatter().string(from: Date())
        let livedoorItem = FeedItem(
            id: "livedoor:summary-only",
            platform: "livedoor",
            url: "https://news.livedoor.com/example",
            title: "杉野遥亮、『世にも奇妙な物語』で初主演",
            content_text: "吉沢亮の関連記事も紹介",
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: "吉沢亮",
            fetched_at: now
        )
        let realSoundItem = FeedItem(
            id: "realsound:summary-only",
            platform: "realsound",
            url: "https://realsound.jp/example",
            title: "杉野遥亮、『世にも奇妙な物語』で初主演",
            content_text: "吉沢亮の関連記事も紹介",
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: "吉沢亮",
            fetched_at: now
        )
        let cinemaCafeItem = FeedItem(
            id: "cinemacafe:summary-only",
            platform: "cinemacafe",
            url: "https://cinemacafe.net/example",
            title: "杉野遥亮、『世にも奇妙な物語』で初主演",
            content_text: "吉沢亮の関連記事も紹介",
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: "吉沢亮",
            fetched_at: now
        )
        _ = db.mergeItems(newItems: [livedoorItem, realSoundItem, cinemaCafeItem])

        XCTAssertTrue(db.queryFeed(keyword: "吉沢亮", days: 30).isEmpty)
    }

    func testLocalGoogleNewsFallbacksCoverStrictArticleSources() throws {
        let rssFallback = Set(["news"])
        let strictArticleFallbacks = Set(
            Platform.all
                .filter { $0.usesStrictKeywordMatching && !$0.isMediaPlatform }
                .map(\.id)
        )
        .subtracting(rssFallback)
        let sourceSpecificFallbacks = Set(["note"])
        let supplementalFallbacks = Set(["tver", "twitter"])
        let expected = strictArticleFallbacks.union(supplementalFallbacks)
            .subtracting(sourceSpecificFallbacks)
        let actual = Set(NetworkManager.googleNewsFallbackSites.map(\.platform))

        XCTAssertEqual(actual, expected)
    }

    func testLocalGoogleNewsFallbackPlatformsExistInRegistry() throws {
        let fallbackPlatforms = Set(NetworkManager.googleNewsFallbackSites.map(\.platform))
        let missing = fallbackPlatforms.filter { Platform.find($0) == nil }.sorted()

        XCTAssertTrue(missing.isEmpty, "Missing platform definitions: \(missing)")
    }

    func testKpopGoogleNewsFallbacksUseEnglishLocale() throws {
        let sitesByPlatform = Dictionary(
            uniqueKeysWithValues: Platform.googleNewsFallbackSites().map { ($0.platform, $0) }
        )

        for platform in ["soompi", "allkpop", "kpopofficial"] {
            XCTAssertEqual(sitesByPlatform[platform]?.locale, .englishUS)
        }
        XCTAssertEqual(sitesByPlatform["natalie"]?.locale, .japan)
        XCTAssertEqual(sitesByPlatform["billboardjapan"]?.locale, .japan)
    }

    func testPlatformFetchCapabilitiesDriveRefreshDecisions() throws {
        XCTAssertTrue(Platform.shouldFetchFromBackend("youtube"))
        XCTAssertTrue(Platform.shouldFetchFromBackend("5ch"))
        XCTAssertFalse(Platform.shouldFetchFromBackend("custom"))
        XCTAssertFalse(Platform.shouldFetchFromBackend("unknown-platform"))

        XCTAssertTrue(Platform.shouldRunDeviceFallback("5ch"))
        XCTAssertTrue(Platform.shouldRunDeviceFallback("news"))
        XCTAssertTrue(Platform.shouldRunDeviceFallback("girlschannel"))
        XCTAssertTrue(Platform.shouldRunDeviceFallback("youtube"))
        XCTAssertTrue(Platform.shouldRunDeviceFallback("note"))
        XCTAssertFalse(Platform.shouldRunDeviceFallback("custom"))

        XCTAssertTrue(Platform.shouldUseDateWindowRefresh("5ch"))
        XCTAssertTrue(Platform.shouldUseDateWindowRefresh("girlschannel"))
        XCTAssertTrue(Platform.shouldUseDateWindowRefresh("togetter"))
        XCTAssertFalse(Platform.shouldUseDateWindowRefresh("news"))
    }

    func testScopedGoogleNewsFallbackSitesOnlyIncludeRequestedFallbackPlatforms() throws {
        let sites = Platform.googleNewsFallbackSites(for: Set(["5ch", "girlschannel", "youtube", "custom"]))
        XCTAssertEqual(sites.map(\.platform), ["girlschannel", "5ch"])
    }
    
    // MARK: - skipDateCutoff flag: forum platforms always pass date filter
    func testSkipDateCutoffAllowsOldForumItems() throws {
        db.setSubscribedPlatforms(platforms: ["5ch", "togetter", "news"])
        let fmt = ISO8601DateFormatter()
        let old = fmt.string(from: Calendar.current.date(byAdding: .day, value: -60, to: Date())!)
        let recent = fmt.string(from: Calendar.current.date(byAdding: .day, value: -1, to: Date())!)

        // 60-day-old 5ch thread — should bypass 30-day cutoff
        let forumItem = FeedItem(
            id: "5ch:old-thread", platform: "5ch", url: "https://5ch.net/t/old",
            title: "Aiko old thread", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: old, watch_term_keyword: "Aiko", fetched_at: old
        )
        // 60-day-old news article — should be filtered out
        let newsItem = FeedItem(
            id: "news:old-article", platform: "news", url: "https://news/old",
            title: "Aiko old news", content_text: "Aiko content", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: old, watch_term_keyword: "Aiko", fetched_at: old
        )
        // Recent news article — passes
        let recentNewsItem = FeedItem(
            id: "news:recent", platform: "news", url: "https://news/recent",
            title: "Aiko recent", content_text: "Aiko content", author: nil, thumbnail_url: nil,
            media_type: "article", published_at: recent, watch_term_keyword: "Aiko", fetched_at: recent
        )
        _ = db.mergeItems(newItems: [forumItem, newsItem, recentNewsItem])
        _ = db.saveTerm(keyword: "Aiko") // needed for strict keyword matching on news

        let results = db.queryFeed(keyword: "Aiko", days: 30)
        XCTAssertTrue(results.contains { $0.id == "5ch:old-thread" }, "5ch item should bypass date cutoff")
        XCTAssertFalse(results.contains { $0.id == "news:old-article" }, "Old news should be filtered by date")
        XCTAssertTrue(results.contains { $0.id == "news:recent" }, "Recent news should pass")
    }

    // MARK: - Cross-path dedup (backend copy + device Google News copy of same article)
    func testQueryFeedDedupesSameArticleFromTwoSources() throws {
        db.setSubscribedPlatforms(platforms: ["mdpr"])
        let now = ISO8601DateFormatter().string(from: Date())
        // Backend copy: direct URL, plain id.
        let backendCopy = FeedItem(
            id: "mdpr:12345", platform: "mdpr", url: "https://mdpr.jp/news/12345",
            title: "Aiko 新曲を発表！", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: now, watch_term_keyword: "Aiko", fetched_at: now
        )
        // Device-scraped copy of the SAME article: Google News URL, gnews id.
        let deviceCopy = FeedItem(
            id: "mdpr:gnews:99887766", platform: "mdpr", url: "https://news.google.com/rss/articles/ABC",
            title: "Aiko 新曲を発表！", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: now, watch_term_keyword: "Aiko", fetched_at: now
        )
        _ = db.mergeItems(newItems: [backendCopy, deviceCopy])
        _ = db.saveTerm(keyword: "Aiko")

        let results = db.queryFeed(keyword: "Aiko", days: 30)
        let mdprResults = results.filter { $0.platform == "mdpr" }
        XCTAssertEqual(mdprResults.count, 1, "Same article from backend + device should appear once")
    }

    func testQueryFeedKeepsConsecutivePlatformClustersNewestFirst() throws {
        db.setSubscribedPlatforms(platforms: ["youtube", "tver"])
        let fmt = ISO8601DateFormatter()
        let now = Date()

        let makeItem = { (id: String, platform: String, title: String, hoursAgo: Int) -> FeedItem in
            let published = fmt.string(from: Calendar.current.date(byAdding: .hour, value: -hoursAgo, to: now)!)
            return FeedItem(
                id: id, platform: platform, url: "https://example.com/\(id)",
                title: title, content_text: nil, author: nil, thumbnail_url: nil,
                media_type: platform == "tver" ? "video" : "article",
                published_at: published, watch_term_keyword: "Aiko", fetched_at: published,
                source: platform == "youtube" ? "youtube_api" : nil
            )
        }

        _ = db.mergeItems(newItems: [
            makeItem("youtube:1", "youtube", "Aiko upload 1", 1),
            makeItem("youtube:2", "youtube", "Aiko upload 2", 2),
            makeItem("youtube:3", "youtube", "Aiko upload 3", 3),
            makeItem("tver:1", "tver", "Aiko TV episode", 4),
        ])

        let results = db.queryFeed(keyword: nil, days: 30)

        XCTAssertEqual(results.map(\.id), ["youtube:1", "youtube:2", "youtube:3", "tver:1"])
    }

    func testQueryFeedKeepsNearDuplicateTitleClustersNewestFirst() throws {
        db.setSubscribedPlatforms(platforms: ["youtube", "niconico", "tver"])
        let fmt = ISO8601DateFormatter()
        let now = Date()

        let makeItem = { (id: String, platform: String, title: String, hoursAgo: Int) -> FeedItem in
            let published = fmt.string(from: Calendar.current.date(byAdding: .hour, value: -hoursAgo, to: now)!)
            return FeedItem(
                id: id, platform: platform, url: "https://example.com/\(id)",
                title: title, content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "video",
                published_at: published, watch_term_keyword: "Aiko", fetched_at: published,
                source: platform == "youtube" ? "youtube_api" : nil
            )
        }

        _ = db.mergeItems(newItems: [
            makeItem("youtube:tour", "youtube", "Aiko announces first arena tour and new single", 1),
            makeItem("niconico:tour", "niconico", "Aiko announces first arena tour and new single - fan reaction", 2),
            makeItem("tver:episode", "tver", "Aiko guest appearance on music talk", 3),
        ])

        let results = db.queryFeed(keyword: nil, days: 30)

        XCTAssertEqual(results.map(\.id), ["youtube:tour", "niconico:tour", "tver:episode"])
    }

    // MARK: - Custom platform keyword-filter bypass
    func testCustomPlatformItemsPassKeywordFilter() throws {
        // custom items have a different watch_term_keyword (e.g. "") but should appear
        // in any keyword-filtered query because the "custom" bypass skips the kw check.
        let now = ISO8601DateFormatter().string(from: Date())
        let customItem = FeedItem(
            id: "custom:rss1", platform: "custom", url: "https://myfeed.com/1",
            title: "Custom feed entry", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: now,
            watch_term_keyword: "", fetched_at: now
        )
        let regularItem = FeedItem(
            id: "youtube:v1", platform: "youtube", url: "https://yt/v1",
            title: "Aiko video", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now,
            watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
        )
        db.setSubscribedPlatforms(platforms: ["youtube", "custom"])
        _ = db.mergeItems(newItems: [customItem, regularItem])

        // Keyword filter: only "Aiko" items normally pass, but custom also passes
        let results = db.queryFeed(keyword: "Aiko", days: 0)
        XCTAssertTrue(results.contains { $0.id == "youtube:v1" }, "Regular item should pass keyword filter")
        XCTAssertTrue(results.contains { $0.id == "custom:rss1" }, "Custom item should bypass keyword filter")
    }

    func testCustomPlatformHiddenWhenUnsubscribed() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let customItem = FeedItem(
            id: "custom:rss2", platform: "custom", url: "https://myfeed.com/2",
            title: "Custom entry", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: now,
            watch_term_keyword: "", fetched_at: now
        )
        db.setSubscribedPlatforms(platforms: ["youtube"]) // custom not subscribed
        _ = db.mergeItems(newItems: [customItem])

        let results = db.queryFeed(keyword: nil, days: 0)
        XCTAssertFalse(results.contains { $0.id == "custom:rss2" }, "Custom item should be hidden when platform is unsubscribed")
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

    func testRemoveCustomUrlPrunesCachedCustomFeedItem() throws {
        db.setSubscribedPlatforms(platforms: ["custom"])
        db.addCustomUrl(url: "https://myoshi-blog.com/feed", title: "Oshi Blog")
        let custom = db.customUrls.first!
        let now = ISO8601DateFormatter().string(from: Date())
        let item = FeedItem(
            id: custom.id,
            platform: "custom",
            url: custom.url,
            title: "Oshi Blog",
            content_text: nil,
            author: "myoshi-blog.com",
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: "",
            fetched_at: now
        )
        _ = db.mergeItems(newItems: [item])
        XCTAssertEqual(db.queryFeed(keyword: nil, days: 0).map(\.id), [custom.id])

        db.removeCustomUrl(id: custom.id)

        XCTAssertTrue(db.customUrls.isEmpty)
        XCTAssertTrue(db.feedItems.isEmpty)
        XCTAssertTrue(db.queryFeed(keyword: nil, days: 0).isEmpty)
    }

    func testCurrentCustomFeedItemsFiltersRemovedCustomSourceResults() throws {
        db.addCustomUrl(url: "https://myoshi-blog.com/feed", title: "Oshi Blog")
        let custom = db.customUrls.first!
        db.removeCustomUrl(id: custom.id)
        let now = ISO8601DateFormatter().string(from: Date())
        let staleCustom = FeedItem(
            id: custom.id,
            platform: "custom",
            url: custom.url,
            title: "Oshi Blog",
            content_text: nil,
            author: "myoshi-blog.com",
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: "",
            fetched_at: now
        )
        let regular = FeedItem(
            id: "youtube:v1",
            platform: "youtube",
            url: "https://youtube.com/watch?v=v1",
            title: "Video",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube_api"
        )

        XCTAssertEqual(db.currentCustomFeedItems([staleCustom, regular]).map(\.id), ["youtube:v1"])
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
    func testMergeItemsCoalescesFeedPersistenceUntilFlush() throws {
        db = LocalDB(directory: tempDir, feedItemsSaveCoalescingDelay: .seconds(60))
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)

        _ = db.mergeItems(newItems: [
            FeedItem(
                id: "youtube:coalesce-1",
                platform: "youtube",
                url: "https://youtube.com/watch?v=coalesce-1",
                title: "First",
                content_text: nil,
                author: nil,
                thumbnail_url: nil,
                media_type: "video",
                published_at: now,
                watch_term_keyword: "Aiko",
                fetched_at: now,
                source: "youtube"
            ),
        ])
        _ = db.mergeItems(newItems: [
            FeedItem(
                id: "youtube:coalesce-2",
                platform: "youtube",
                url: "https://youtube.com/watch?v=coalesce-2",
                title: "Second",
                content_text: nil,
                author: nil,
                thumbnail_url: nil,
                media_type: "video",
                published_at: now,
                watch_term_keyword: "Aiko",
                fetched_at: now,
                source: "youtube"
            ),
        ])

        XCTAssertFalse(
            FileManager.default.fileExists(atPath: tempDir.appendingPathComponent("feed_items.json").path),
            "Merge persistence should be delayed so refresh bursts can coalesce into one feed write"
        )

        db.flushPendingFeedItemsSave()
        let freshDB = LocalDB(directory: tempDir)

        XCTAssertEqual(
            Set(freshDB.feedItems.map(\.id)),
            ["youtube:coalesce-1", "youtube:coalesce-2"]
        )
    }

    func testFlushAfterDelayedFeedSaveDoesNotRewriteFile() throws {
        db = LocalDB(directory: tempDir, feedItemsSaveCoalescingDelay: .milliseconds(10))
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        _ = db.mergeItems(newItems: [
            FeedItem(
                id: "youtube:coalesce-finished",
                platform: "youtube",
                url: "https://youtube.com/watch?v=coalesce-finished",
                title: "Finished",
                content_text: nil,
                author: nil,
                thumbnail_url: nil,
                media_type: "video",
                published_at: now,
                watch_term_keyword: "Aiko",
                fetched_at: now,
                source: "youtube"
            ),
        ])

        let expectation = XCTestExpectation(description: "delayed feed write")
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.2) { expectation.fulfill() }
        wait(for: [expectation], timeout: 1.0)

        let feedURL = tempDir.appendingPathComponent("feed_items.json")
        let writtenDate = try XCTUnwrap(
            feedURL.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate
        )

        db.flushPendingFeedItemsSave()

        let dateAfterFlush = try XCTUnwrap(
            feedURL.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate
        )
        XCTAssertEqual(dateAfterFlush, writtenDate)
    }

    func testDeleteFeedItemCancelsPendingMergePersistence() throws {
        db = LocalDB(directory: tempDir, feedItemsSaveCoalescingDelay: .seconds(60))
        let now = ISO8601DateFormatter().string(from: Date())
        let item = FeedItem(
            id: "youtube:pending-delete",
            platform: "youtube",
            url: "https://youtube.com/watch?v=pending-delete",
            title: "Pending delete",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube"
        )

        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        _ = db.mergeItems(newItems: [item])
        db.deleteFeedItem(id: item.id, watchTermKeyword: item.watch_term_keyword)
        db.flushPendingFeedItemsSave()

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertTrue(freshDB.feedItems.isEmpty)
        XCTAssertTrue(freshDB.hiddenItems.contains("youtube:pending-delete::Aiko"))
    }

    func testFlushWaitsForImmediateFeedSaveAfterDelete() throws {
        db = LocalDB(directory: tempDir, feedItemsSaveCoalescingDelay: .milliseconds(1))
        let now = ISO8601DateFormatter().string(from: Date())
        let item = FeedItem(
            id: "youtube:immediate-delete",
            platform: "youtube",
            url: "https://youtube.com/watch?v=immediate-delete",
            title: "Immediate delete",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: now,
            watch_term_keyword: "Aiko",
            fetched_at: now,
            source: "youtube"
        )

        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        _ = db.mergeItems(newItems: [item])
        db.flushPendingFeedItemsSave()

        var freshDB = LocalDB(directory: tempDir)
        XCTAssertEqual(freshDB.feedItems.map(\.id), ["youtube:immediate-delete"])

        db.deleteFeedItem(id: item.id, watchTermKeyword: item.watch_term_keyword)
        db.flushPendingFeedItemsSave()

        freshDB = LocalDB(directory: tempDir)
        XCTAssertTrue(freshDB.feedItems.isEmpty)
    }

    func testPreviewMergePersistsAfterExplicitFlush() throws {
        db = LocalDB(directory: tempDir, feedItemsSaveCoalescingDelay: .seconds(60))
        let itemID = "youtube:notification-flush"
        let item = FeedItem(
            id: itemID,
            platform: "youtube",
            url: "https://youtube.com/watch?v=notification-flush",
            title: "Notification flush",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "video",
            published_at: "2026-06-20T12:00:00Z",
            watch_term_keyword: "Notification Flush",
            fetched_at: "2026-06-20T12:00:00Z",
            source: "youtube_api"
        )

        XCTAssertEqual(db.mergeItems(newItems: [item], notifyOnNew: false, preserveIncomingItems: true), 1)
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: tempDir.appendingPathComponent("feed_items.json").path),
            "Preview-style merges use delayed feed persistence until an explicit lifecycle/background flush"
        )

        db.flushPendingFeedItemsSave()

        let freshDB = LocalDB(directory: tempDir)
        XCTAssertEqual(freshDB.feedItems.map(\.id), [itemID])
    }

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
        let platforms = Platform.all.filter(\.subscribedByDefault).map(\.id).filter { $0 != "oricon" }
        let data = try JSONEncoder().encode(platforms)
        let url = tempDir.appendingPathComponent("subscribed_platforms.json")
        try data.write(to: url)

        let freshDB = LocalDB(directory: tempDir)
        XCTAssertTrue(freshDB.subscribedPlatforms.contains("oricon"),
                      "Migration should have added missing 'oricon' platform")

        // Clean up the version key so subsequent tests see a clean state
        UserDefaults.standard.removeObject(forKey: "localdb_schema_version")
    }

    func testSchemaV3AddsTwitterToExistingSubscriptions() throws {
        let platforms = Platform.all
            .filter(\.subscribedByDefault)
            .map(\.id)
            .filter { $0 != "twitter" && $0 != "oricon" }
        let data = try JSONEncoder().encode(platforms)
        try data.write(to: tempDir.appendingPathComponent("subscribed_platforms.json"))
        UserDefaults.standard.set(2, forKey: "localdb_schema_version")

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertTrue(freshDB.subscribedPlatforms.contains("twitter"))
        XCTAssertFalse(
            freshDB.subscribedPlatforms.contains("oricon"),
            "Migration must preserve sources the user previously disabled"
        )
        UserDefaults.standard.removeObject(forKey: "localdb_schema_version")
    }

    func testSchemaV4AddsTheTVToExistingSubscriptions() throws {
        let platforms = Platform.all
            .filter(\.subscribedByDefault)
            .map(\.id)
            .filter { $0 != "thetv" && $0 != "oricon" }
        let data = try JSONEncoder().encode(platforms)
        try data.write(to: tempDir.appendingPathComponent("subscribed_platforms.json"))
        UserDefaults.standard.set(3, forKey: "localdb_schema_version")

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertTrue(freshDB.subscribedPlatforms.contains("thetv"))
        XCTAssertFalse(
            freshDB.subscribedPlatforms.contains("oricon"),
            "Migration must preserve sources the user previously disabled"
        )
        UserDefaults.standard.removeObject(forKey: "localdb_schema_version")
    }

    func testSchemaV5AddsArtistTrackingSourcesToExistingSubscriptions() throws {
        let newSources = Set([
            "allkpop",
            "billboardjapan",
            "kpopofficial",
            "natalie",
            "soompi",
        ])
        let platforms = Platform.all
            .filter(\.subscribedByDefault)
            .map(\.id)
            .filter { !newSources.contains($0) && $0 != "oricon" }
        let data = try JSONEncoder().encode(platforms)
        try data.write(to: tempDir.appendingPathComponent("subscribed_platforms.json"))
        UserDefaults.standard.set(4, forKey: "localdb_schema_version")

        let freshDB = LocalDB(directory: tempDir)

        for source in newSources {
            XCTAssertTrue(freshDB.subscribedPlatforms.contains(source))
        }
        XCTAssertFalse(
            freshDB.subscribedPlatforms.contains("oricon"),
            "Migration must preserve sources the user previously disabled"
        )
        UserDefaults.standard.removeObject(forKey: "localdb_schema_version")
    }

    func testSchemaMigrationPrunesSummaryOnlyArticleMatch() throws {
        let term = WatchTerm(keyword: "吉沢亮")
        let now = ISO8601DateFormatter().string(from: Date())
        let item = FeedItem(
            id: "realsound:cached-noise",
            platform: "realsound",
            url: "https://realsound.jp/example",
            title: "杉野遥亮、『世にも奇妙な物語』で初主演",
            content_text: "吉沢亮の関連記事も紹介",
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: "吉沢亮",
            fetched_at: now
        )
        try JSONEncoder().encode([term]).write(
            to: tempDir.appendingPathComponent("terms.json")
        )
        try JSONEncoder().encode([item]).write(
            to: tempDir.appendingPathComponent("feed_items.json")
        )
        try JSONEncoder().encode(["news", "youtube", "tver", "custom", "twitter"]).write(
            to: tempDir.appendingPathComponent("subscribed_platforms.json")
        )
        UserDefaults.standard.set(1, forKey: "localdb_schema_version")
        let refreshCheckTime = Date(timeIntervalSince1970: 1_800)
        BackgroundRefreshPolicy.recordRefreshCompleted(at: refreshCheckTime.addingTimeInterval(-60))
        XCTAssertFalse(
            BackgroundRefreshPolicy.shouldRefreshOnForeground(items: [], now: refreshCheckTime)
        )

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertTrue(freshDB.feedItems.isEmpty)
        XCTAssertTrue(
            BackgroundRefreshPolicy.shouldRefreshOnForeground(items: freshDB.feedItems, now: refreshCheckTime)
        )
        UserDefaults.standard.removeObject(forKey: "localdb_schema_version")
    }

    func testSchemaMigrationPrunesOrphanStrictCachedItemBeforeRepair() throws {
        let existingTerm = WatchTerm(keyword: "Aiko")
        let now = ISO8601DateFormatter().string(from: Date())
        let item = FeedItem(
            id: "note:orphan-cached-item",
            platform: "note",
            url: "https://note.com/example/n/orphan",
            title: "Unrelated cached note",
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: "Miku",
            fetched_at: now
        )
        try JSONEncoder().encode([existingTerm]).write(
            to: tempDir.appendingPathComponent("terms.json")
        )
        try JSONEncoder().encode([item]).write(
            to: tempDir.appendingPathComponent("feed_items.json")
        )
        UserDefaults.standard.set(1, forKey: "localdb_schema_version")
        defer { UserDefaults.standard.removeObject(forKey: "localdb_schema_version") }

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertFalse(freshDB.terms.contains(where: { $0.keyword == "Miku" }))
        XCTAssertFalse(freshDB.feedItems.contains(where: { $0.id == item.id }))
    }

    // MARK: - Feature 5b: Hidden items (deleteFeedItem)
    func testDeleteFeedItemHidesAndExcludesFromQuery() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let item = FeedItem(
            id: "youtube:hidden-test", platform: "youtube",
            url: "https://youtube.com/watch?v=hidden-test",
            title: "Hidden item", content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "video", published_at: now,
            watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube_api"
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
                watch_term_keyword: kw, fetched_at: now,
                source: "youtube_api"
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

    func testQueryFeedFiltersYahooNewsBareUrlItems() throws {
        // Yahoo News sometimes returns items whose title or content is a raw URL
        // (fallback when the scraper can't extract article text). These should be
        // excluded from queryFeed results because they offer no readable content.
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["yahoonews"])

        let bareUrlTitle = FeedItem(
            id: "yahoonews:bare-title", platform: "yahoonews",
            url: "https://news.yahoo.co.jp/articles/abc",
            title: "https://news.yahoo.co.jp/articles/abc",
            content_text: nil, author: nil, thumbnail_url: nil,
            media_type: "article", published_at: now,
            watch_term_keyword: "Aiko", fetched_at: now
        )
        let bareUrlContent = FeedItem(
            id: "yahoonews:bare-content", platform: "yahoonews",
            url: "https://news.yahoo.co.jp/articles/def",
            title: "Yahoo News Article",
            content_text: "https://news.yahoo.co.jp/articles/def",
            author: nil, thumbnail_url: nil,
            media_type: "article", published_at: now,
            watch_term_keyword: "Aiko", fetched_at: now
        )
        let normalArticle = FeedItem(
            id: "yahoonews:normal", platform: "yahoonews",
            url: "https://news.yahoo.co.jp/articles/ghi",
            title: "Aiko releases new single",
            content_text: "Aiko announced a new song.",
            author: nil, thumbnail_url: nil,
            media_type: "article", published_at: now,
            watch_term_keyword: "Aiko", fetched_at: now
        )
        let googleNewsSummary = FeedItem(
            id: "yahoonews:google-summary", platform: "yahoonews",
            url: "https://news.google.com/rss/articles/ABC?oc=5",
            title: "Aiko announces a new tour - Yahoo! News",
            content_text: """
            <a href="https://news.google.com/rss/articles/ABC?oc=5" target="_blank">\
            Aiko announces a new tour</a>&nbsp;&nbsp;<font color="#6f6f6f">Yahoo! News</font>
            """,
            author: nil, thumbnail_url: nil,
            media_type: "article", published_at: now,
            watch_term_keyword: "Aiko", fetched_at: now
        )

        _ = db.mergeItems(newItems: [bareUrlTitle, bareUrlContent, normalArticle, googleNewsSummary])
        let results = db.queryFeed(keyword: nil, days: 30)

        XCTAssertEqual(results.count, 2)
        XCTAssertTrue(results.contains { $0.id == "yahoonews:normal" })
        XCTAssertTrue(results.contains { $0.id == "yahoonews:google-summary" })
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
                watch_term_keyword: kw, fetched_at: now,
                source: "youtube_api"
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

    func testQueryFeedDeduplicatesExactUrlAcrossDifferentPlatforms() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["news", "yahoonews"])
        let sharedURL = "https://news.google.com/rss/articles/shared-story?oc=5"
        _ = db.mergeItems(newItems: [
            FeedItem(
                id: "news:gnews:shared", platform: "news", url: sharedURL,
                title: "Aiko announces a new film - Example News",
                content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "article", published_at: now,
                watch_term_keyword: "Aiko", fetched_at: now
            ),
            FeedItem(
                id: "yahoonews:gnews:shared", platform: "yahoonews", url: sharedURL,
                title: "Aiko announces a new film（Example News） - Yahoo!ニュース",
                content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "article", published_at: now,
                watch_term_keyword: "Aiko", fetched_at: now
            ),
        ])

        XCTAssertEqual(db.feedItems.count, 2, "Source-specific copies remain available for source filters")
        XCTAssertEqual(db.queryFeed(keyword: nil, days: 30).count, 1)
    }

    func testQueryFeedDeduplicatesNormalizedUrlVariants() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["news"])
        _ = db.mergeItems(newItems: [
            FeedItem(
                id: "news:direct-clean", platform: "news",
                url: "http://example.com/articles/aiko-story",
                title: "Aiko announces a new film", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "article", published_at: now, watch_term_keyword: "Aiko", fetched_at: now
            ),
            FeedItem(
                id: "news:direct-tracked", platform: "news",
                url: "https://www.example.com/articles/aiko-story/?utm_source=gnews&fbclid=abc#comments",
                title: "Aiko announces a new film", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "article", published_at: now, watch_term_keyword: "Aiko", fetched_at: now
            ),
        ])

        XCTAssertEqual(db.feedItems.count, 2)
        XCTAssertEqual(db.queryFeed(keyword: nil, days: 30).count, 1)
    }

    func testQueryFeedDeduplicatesDirectAndPublisherSuffixedArticleTitles() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["news", "yahoonews"])
        _ = db.mergeItems(newItems: [
            FeedItem(
                id: "news:direct-title", platform: "news",
                url: "https://example.com/articles/aiko-film",
                title: "Aiko announces a new film",
                content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "article", published_at: now, watch_term_keyword: "Aiko", fetched_at: now
            ),
            FeedItem(
                id: "yahoonews:suffixed-title", platform: "yahoonews",
                url: "https://news.google.com/rss/articles/aiko-film?oc=5",
                title: "Aiko announces a new film - Example News",
                content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "article", published_at: now, watch_term_keyword: "Aiko", fetched_at: now
            ),
        ])

        XCTAssertEqual(db.feedItems.count, 2)
        XCTAssertEqual(db.queryFeed(keyword: nil, days: 30).count, 1)
    }

    func testQueryFeedDeduplicatesYoutubeUrlAliases() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["youtube"])
        _ = db.mergeItems(newItems: [
            FeedItem(
                id: "youtube:abc123", platform: "youtube",
                url: "https://www.youtube.com/watch?v=abc123&utm_source=share",
                title: "Aiko live clip", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
                source: "youtube_api"
            ),
            FeedItem(
                id: "youtube:alias:abc123", platform: "youtube",
                url: "https://youtu.be/abc123?t=30",
                title: "Aiko live clip", content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
                source: "youtube_api"
            ),
        ])

        XCTAssertEqual(db.feedItems.count, 2)
        XCTAssertEqual(db.queryFeed(keyword: nil, days: 30).count, 1)
    }

    func testQueryFeedDeduplicatesCrossSourcePublisherTitleVariants() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["news", "yahoonews"])
        _ = db.mergeItems(newItems: [
            FeedItem(
                id: "news:publisher-copy", platform: "news",
                url: "https://news.google.com/rss/articles/source-a?oc=5",
                title: "Aiko announces a new film - Example News",
                content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "article", published_at: now, watch_term_keyword: "Aiko", fetched_at: now
            ),
            FeedItem(
                id: "yahoonews:publisher-copy", platform: "yahoonews",
                url: "https://news.google.com/rss/articles/source-b?oc=5",
                title: "Aiko announces a new film（Example News） - Yahoo!ニュース",
                content_text: nil, author: nil, thumbnail_url: nil,
                media_type: "article", published_at: now, watch_term_keyword: "Aiko", fetched_at: now
            ),
        ])

        XCTAssertEqual(db.feedItems.count, 2)
        XCTAssertEqual(db.queryFeed(keyword: nil, days: 30).count, 1)
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

    func testKeywordMatchingIsCaseInsensitive() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["news"])
        _ = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)

        // Title uses all-caps — strict matching must still pass
        let item = FeedItem(
            id: "news:case-test", platform: "news",
            url: "https://news.example.com/case",
            title: "AIKO releases new single", content_text: nil,
            author: nil, thumbnail_url: nil, media_type: "article",
            published_at: now, watch_term_keyword: "Aiko", fetched_at: now
        )
        _ = db.mergeItems(newItems: [item])
        let results = db.queryFeed(keyword: "Aiko", days: 30)
        XCTAssertEqual(results.count, 1, "Strict keyword match must be case-insensitive")
    }

    func testMultiWordKeywordRequiresAllPartsPresent() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        db.setSubscribedPlatforms(platforms: ["news"])
        _ = db.saveTerm(keyword: "Aiko Chan", collectionMode: .allInfo)

        // Title contains both words — should pass
        let bothWords = FeedItem(
            id: "news:both", platform: "news",
            url: "https://news.example.com/both",
            title: "Aiko Chan announces tour", content_text: nil,
            author: nil, thumbnail_url: nil, media_type: "article",
            published_at: now, watch_term_keyword: "Aiko Chan", fetched_at: now
        )
        // Title contains only one word — should be filtered out
        let oneWord = FeedItem(
            id: "news:one", platform: "news",
            url: "https://news.example.com/one",
            title: "Aiko releases album", content_text: nil,
            author: nil, thumbnail_url: nil, media_type: "article",
            published_at: now, watch_term_keyword: "Aiko Chan", fetched_at: now
        )
        _ = db.mergeItems(newItems: [bothWords, oneWord])
        let results = db.queryFeed(keyword: "Aiko Chan", days: 30)
        XCTAssertEqual(results.count, 1)
        XCTAssertEqual(results.first?.id, "news:both")
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
            media_type: "video", published_at: now, watch_term_keyword: "Aiko", fetched_at: now,
            source: "youtube"
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

        db.flushPendingFeedItemsSave()
        let freshDB = LocalDB(directory: tempDir)
        XCTAssertTrue(freshDB.terms.isEmpty)
        XCTAssertTrue(freshDB.feedItems.isEmpty)
        XCTAssertTrue(freshDB.savedPages.isEmpty)
        XCTAssertTrue(freshDB.customUrls.isEmpty)
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
            watch_term_keyword: "Aiko", fetched_at: inside,
            source: "youtube_api"
        )
        let itemOutside = FeedItem(
            id: "youtube:outside", platform: "youtube", url: "https://yt/outside",
            title: "Outside", content_text: "Outside", author: nil, thumbnail_url: nil,
            media_type: "video", published_at: outside,
            watch_term_keyword: "Aiko", fetched_at: outside,
            source: "youtube_api"
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
        let newerDate = Date().addingTimeInterval(-3600)
        let utcFormatter = ISO8601DateFormatter()
        utcFormatter.formatOptions = [.withInternetDateTime]
        let newerUtc = utcFormatter.string(from: newerDate)
        // older: 2 hours ago expressed with +09:00; lexicographic ordering must not win.
        let olderDate = Date().addingTimeInterval(-7200)
        let offsetFormatter = ISO8601DateFormatter()
        offsetFormatter.formatOptions = [.withInternetDateTime]
        offsetFormatter.timeZone = TimeZone(secondsFromGMT: 9 * 60 * 60)
        let olderWithOffset = offsetFormatter.string(from: olderDate)

        let newerItem = FeedItem(
            id: "youtube:newer", platform: "youtube", url: "https://yt/newer",
            title: "Newer", content_text: "Newer", author: nil, thumbnail_url: nil,
            media_type: "video", published_at: newerUtc,
            watch_term_keyword: "Aiko", fetched_at: newerUtc,
            source: "youtube_api"
        )
        let olderItem = FeedItem(
            id: "youtube:older", platform: "youtube", url: "https://yt/older",
            title: "Older", content_text: "Older", author: nil, thumbnail_url: nil,
            media_type: "video", published_at: olderWithOffset,
            watch_term_keyword: "Aiko", fetched_at: olderWithOffset,
            source: "youtube_api"
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

    func testParseISO8601DateHandlesRepeatedConcurrentCalls() throws {
        let values = [
            "2024-06-15T10:30:00.123456Z",
            "2024-06-15T10:30:00Z",
            "2024-06-15T19:30:00+09:00",
            "2024-06-15T10:30:00",
            "not a date",
            "",
        ]
        let lock = NSLock()
        var mismatches: [String] = []

        DispatchQueue.concurrentPerform(iterations: 500) { index in
            let value = values[index % values.count]
            let date = parseISO8601Date(value)
            let expectedNil = value.isEmpty || value == "not a date"
            if expectedNil != (date == nil) {
                lock.lock()
                mismatches.append(value)
                lock.unlock()
            }
        }

        XCTAssertTrue(mismatches.isEmpty, "Unexpected parse results for \(mismatches)")
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
        XCTAssertEqual(result, "Aiko")

        i18n.setLanguage("ja")
        let jaResult = i18n.tFormat("notifNewItemsTitle", "愛子")
        XCTAssertEqual(jaResult, "愛子")

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

    // MARK: - Reader displayability
    func testReaderDisplayabilityAllowsStructuredLowTextPages() throws {
        let metrics: [String: Any] = [
            "textLength": 24,
            "titleLength": 18,
            "hasReaderContainer": false,
            "linkCount": NSNumber(value: 4),
            "imageCount": 1,
            "visibleTextNodes": 1,
            "height": 360,
            "blockedText": false,
            "urlLooksBlank": false
        ]

        XCTAssertFalse(ReaderContentDisplayability.shouldShowBlockedBanner(metrics: metrics))
    }

    func testReaderDisplayabilityAllowsArticleContainersWithShortVisibleText() throws {
        let metrics: [String: Any] = [
            "textLength": 32,
            "titleLength": 0,
            "hasReaderContainer": true,
            "linkCount": 0,
            "imageCount": 0,
            "visibleTextNodes": 0,
            "height": 500,
            "blockedText": false,
            "urlLooksBlank": false
        ]

        XCTAssertFalse(ReaderContentDisplayability.shouldShowBlockedBanner(metrics: metrics))
    }

    func testReaderDisplayabilityShowsBannerForTrulyBlankPages() throws {
        let metrics: [String: Any] = [
            "textLength": 0,
            "titleLength": 0,
            "hasReaderContainer": false,
            "linkCount": 0,
            "imageCount": 0,
            "visibleTextNodes": 0,
            "height": 0,
            "blockedText": false,
            "urlLooksBlank": false
        ]

        XCTAssertTrue(ReaderContentDisplayability.shouldShowBlockedBanner(metrics: metrics))
    }

    func testReaderDisplayabilityShowsBannerForBlockedShells() throws {
        let metrics: [String: Any] = [
            "textLength": 86,
            "titleLength": 0,
            "hasReaderContainer": false,
            "linkCount": 0,
            "imageCount": 0,
            "visibleTextNodes": 1,
            "height": 320,
            "blockedText": true,
            "urlLooksBlank": false
        ]

        XCTAssertTrue(ReaderContentDisplayability.shouldShowBlockedBanner(metrics: metrics))
    }

    func testReaderDisplayabilityShowsBannerForBlockedShellsWithTitles() throws {
        let metrics: [String: Any] = [
            "textLength": 72,
            "titleLength": 18,
            "hasReaderContainer": false,
            "linkCount": 3,
            "imageCount": 1,
            "visibleTextNodes": 1,
            "height": 480,
            "blockedText": true,
            "urlLooksBlank": false
        ]

        XCTAssertTrue(ReaderContentDisplayability.shouldShowBlockedBanner(metrics: metrics))
    }

    // MARK: - Search catalog
    func testAllBuiltInSearchPagesGenerateValidWebModeReaders() throws {
        let query = "UITest Oshi"
        let expectedGroups = Set([
            "News", "Entertainment", "Magazines", "Video", "Writing",
            "Social", "Community", "Web", "Shopping"
        ])
        let groups = Set(staticSearchLinks.map(\.group))
        XCTAssertEqual(groups, expectedGroups)
        XCTAssertEqual(Set(staticSearchLinks.map(\.id)).count, staticSearchLinks.count)

        for link in staticSearchLinks {
            let urlString = link.makeUrl(query)
            let components = URLComponents(string: urlString)
            XCTAssertEqual(components?.scheme, "https", "\(link.id) should use https")
            XCTAssertFalse(components?.host?.isEmpty ?? true, "\(link.id) should have a host")
            XCTAssertFalse(urlString.contains(" "), "\(link.id) URL should be escaped")
            XCTAssertTrue(urlString.localizedCaseInsensitiveContains("UITest") || link.id == "nhk",
                          "\(link.id) should include the query")

            let item = FeedItem(
                id: "search:\(link.id):UITest%20Oshi",
                platform: link.platform,
                url: urlString,
                title: "\(link.label): \(query)",
                content_text: nil,
                author: link.domain,
                thumbnail_url: nil,
                media_type: "article",
                published_at: "2026-06-15T00:00:00Z",
                watch_term_keyword: query,
                fetched_at: "2026-06-15T00:00:00Z"
            )
            XCTAssertFalse(ReaderView.initialReaderMode(for: item),
                           "\(link.id) should open in web mode to avoid blank search pages")
        }

        let customSearchItem = FeedItem(
            id: "search:custom-url:",
            platform: "custom",
            url: "https://example.com",
            title: "Custom URL",
            content_text: nil,
            author: "example.com",
            thumbnail_url: nil,
            media_type: "article",
            published_at: "2026-06-15T00:00:00Z",
            watch_term_keyword: "",
            fetched_at: "2026-06-15T00:00:00Z"
        )
        XCTAssertFalse(ReaderView.initialReaderMode(for: customSearchItem),
                       "Custom URLs opened from Search should also use web mode")
    }

    func testDedicatedSearchLinksUseDedicatedPlatformIds() throws {
        let linksById = Dictionary(uniqueKeysWithValues: staticSearchLinks.map { ($0.id, $0) })

        XCTAssertEqual(linksById["modelpress"]?.platform, "mdpr")
        XCTAssertEqual(linksById["natalie"]?.platform, "natalie")
        XCTAssertEqual(linksById["tver"]?.platform, "tver")
        XCTAssertEqual(linksById["youtube"]?.platform, "youtube")
        XCTAssertEqual(linksById["niconico"]?.platform, "niconico")
        XCTAssertEqual(linksById["x"]?.platform, "twitter")
        XCTAssertEqual(linksById["girlschannel"]?.platform, "girlschannel")
        XCTAssertEqual(linksById["togetter"]?.platform, "togetter")
    }

    func testFiveChFeedItemsOpenInWebMode() throws {
        let item = FeedItem(
            id: "5ch:mevius:nogizaka:1782410369",
            platform: "5ch",
            url: "https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369",
            title: "5ch thread",
            content_text: "12 posts",
            author: "5ch",
            thumbnail_url: nil,
            media_type: "text",
            published_at: "2026-06-15T00:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-15T00:00:00Z"
        )

        XCTAssertTrue(ReaderView.usesSystemSafari(for: item),
                      "5ch should use the in-app Safari controller instead of WKWebView")
        XCTAssertFalse(ReaderView.initialReaderMode(for: item),
                       "5ch should keep the site reader intact instead of applying article-reader cleanup")
    }

    func testFiveChIgnoresAutoTranslateReaderURLWrapping() throws {
        let previous = UserDefaults.standard.bool(forKey: "auto_translate_reader")
        UserDefaults.standard.set(true, forKey: "auto_translate_reader")
        defer { UserDefaults.standard.set(previous, forKey: "auto_translate_reader") }

        let rawURL = "https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369"
        let item = FeedItem(
            id: "5ch:mevius:nogizaka:1782410369",
            platform: "5ch",
            url: rawURL,
            title: "5ch thread",
            content_text: "12 posts",
            author: "5ch",
            thumbnail_url: nil,
            media_type: "text",
            published_at: "2026-06-15T00:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-15T00:00:00Z"
        )

        let view = ReaderView(feedItem: item)
        let target = view.targetUrl

        XCTAssertEqual(target?.host, "itest.5ch.io")
        XCTAssertFalse(target?.host == "translate.google.com")
        XCTAssertFalse(target?.absoluteString.contains("translate.google.com") ?? true)
    }

    func testTranslatedReaderKeepsOriginalPageUrlForImageReferer() throws {
        let previous = UserDefaults.standard.bool(forKey: "auto_translate_reader")
        UserDefaults.standard.set(true, forKey: "auto_translate_reader")
        defer { UserDefaults.standard.set(previous, forKey: "auto_translate_reader") }

        let rawURL = "https://thetv.jp/news/detail/123456/?utm_source=share"
        let item = FeedItem(
            id: "thetv:gnews:123456",
            platform: "thetv",
            url: rawURL,
            title: "TheTV article",
            content_text: nil,
            author: "TheTV",
            thumbnail_url: nil,
            media_type: "article",
            published_at: "2026-06-15T00:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-15T00:00:00Z"
        )

        let view = ReaderView(feedItem: item)

        XCTAssertEqual(view.targetUrl?.host, "translate.google.com")
        XCTAssertEqual(view.originalPageUrl?.absoluteString, "https://thetv.jp/news/detail/123456/")
    }

    func testFiveChMirrorURLsOpenDirectlyWithoutGuessingItestServer() throws {
        let item = FeedItem(
            id: "2ch.sc:hayabusa3.2ch.sc:mnewsplus:1782467821",
            platform: "5ch",
            url: "http://hayabusa3.2ch.sc/test/read.cgi/mnewsplus/1782467821/",
            title: "5ch mirror thread",
            content_text: "42 posts",
            author: "5ch",
            thumbnail_url: nil,
            media_type: "text",
            published_at: "2026-06-15T00:00:00Z",
            watch_term_keyword: "Aiko",
            fetched_at: "2026-06-15T00:00:00Z"
        )

        XCTAssertEqual(
            ReaderView(feedItem: item).targetUrl?.absoluteString,
            "http://hayabusa3.2ch.sc/test/read.cgi/mnewsplus/1782467821/"
        )
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

    func testMissingTermsAreRepairedFromCachedFeedItems() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let cached = [
            FeedItem(
                id: "note:cached-1",
                platform: "note",
                url: "https://note.com/example/n/1",
                title: "Cached Aiko note",
                content_text: nil,
                author: nil,
                thumbnail_url: nil,
                media_type: "article",
                published_at: now,
                watch_term_keyword: "Aiko",
                fetched_at: now
            ),
            FeedItem(
                id: "youtube:cached-2",
                platform: "youtube",
                url: "https://youtube.com/watch?v=2",
                title: "Cached Miku video",
                content_text: nil,
                author: nil,
                thumbnail_url: nil,
                media_type: "video",
                published_at: now,
                watch_term_keyword: "Miku",
                fetched_at: now,
                source: "youtube_api"
            ),
        ]
        let data = try JSONEncoder().encode(cached)
        try data.write(to: tempDir.appendingPathComponent("feed_items.json"))
        try "[]".write(to: tempDir.appendingPathComponent("terms.json"), atomically: true, encoding: .utf8)
        UserDefaults.standard.set(3, forKey: "localdb_schema_version")
        defer { UserDefaults.standard.removeObject(forKey: "localdb_schema_version") }

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertEqual(freshDB.terms.map(\.keyword).sorted(), ["Aiko", "Miku"])
        XCTAssertTrue(freshDB.terms.allSatisfy(\.is_active))
        XCTAssertTrue(freshDB.terms.allSatisfy(\.repaired_from_cache))
    }

    func testPartiallyMissingTermsAreRepairedFromCachedFeedItems() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let existingTerm = WatchTerm(id: "server-aiko", keyword: "Aiko", collection_mode: .mediaOnly, repaired_from_cache: false)
        let cached = [
            FeedItem(
                id: "note:cached-1",
                platform: "note",
                url: "https://note.com/example/n/1",
                title: "Cached Aiko note",
                content_text: nil,
                author: nil,
                thumbnail_url: nil,
                media_type: "article",
                published_at: now,
                watch_term_keyword: "Aiko",
                fetched_at: now
            ),
            FeedItem(
                id: "youtube:cached-2",
                platform: "youtube",
                url: "https://youtube.com/watch?v=2",
                title: "Cached Miku video",
                content_text: nil,
                author: nil,
                thumbnail_url: nil,
                media_type: "video",
                published_at: now,
                watch_term_keyword: "Miku",
                fetched_at: now,
                source: "youtube_api"
            ),
        ]
        try JSONEncoder().encode([existingTerm])
            .write(to: tempDir.appendingPathComponent("terms.json"))
        try JSONEncoder().encode(cached)
            .write(to: tempDir.appendingPathComponent("feed_items.json"))
        UserDefaults.standard.set(3, forKey: "localdb_schema_version")
        defer { UserDefaults.standard.removeObject(forKey: "localdb_schema_version") }

        let freshDB = LocalDB(directory: tempDir)

        XCTAssertEqual(freshDB.terms.map(\.keyword).sorted(), ["Aiko", "Miku"])
        XCTAssertEqual(freshDB.term(matchingKeyword: "Aiko")?.id, "server-aiko")
        XCTAssertFalse(freshDB.term(matchingKeyword: "Aiko")?.repaired_from_cache ?? true)
        XCTAssertTrue(freshDB.term(matchingKeyword: "Miku")?.repaired_from_cache ?? false)
    }

    // MARK: - setSourcesOrder / setWallpaper / setOshiAvatar
    func testSetSourcesOrderUpdatesAndClearsOnClearAll() throws {
        db.setSourcesOrder(order: ["youtube", "news", "tver"])
        XCTAssertEqual(db.sourcesOrder, ["youtube", "news", "tver"])
        db.clearAllData()
        XCTAssertNil(db.sourcesOrder)
    }

    func testSetWallpaperUpdatesAndNilClearsOnClearAll() throws {
        db.setWallpaper(url: "https://img.example.com/bg.jpg")
        XCTAssertEqual(db.wallpaper, "https://img.example.com/bg.jpg")
        db.setWallpaper(url: nil)
        XCTAssertNil(db.wallpaper)
    }

    func testSetOshiAvatarStoresImageUrl() throws {
        db.setOshiAvatar(keyword: "Aiko", imageUrl: "https://img.example.com/aiko.png")
        XCTAssertEqual(db.oshiAvatars["Aiko"], "https://img.example.com/aiko.png")
    }

    // MARK: - addTermFromBackend / replaceTerm / removeSaved
    func testAddTermFromBackendInsertsAtFront() throws {
        let first = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        let server = WatchTerm(id: "server-99", keyword: "Miku", collection_mode: .mediaOnly)
        db.addTermFromBackend(server)
        XCTAssertEqual(db.terms.count, 2)
        XCTAssertEqual(db.terms.first?.id, "server-99", "addTermFromBackend must insert at position 0")
        _ = first
    }

    func testReplaceTermSwapsById() throws {
        let local = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        let updated = WatchTerm(id: "server-42", keyword: "Aiko Updated", collection_mode: .mediaOnly, is_active: false)
        db.replaceTerm(localId: local.id, with: updated)
        XCTAssertEqual(db.terms.count, 1)
        XCTAssertEqual(db.terms.first?.id, "server-42")
        XCTAssertEqual(db.terms.first?.keyword, "Aiko Updated")
        XCTAssertEqual(db.terms.first?.collection_mode, .mediaOnly)
    }

    func testReplaceTermNoOpForUnknownId() throws {
        _ = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        let ghost = WatchTerm(id: "ghost-id", keyword: "Ghost")
        db.replaceTerm(localId: "nonexistent", with: ghost)
        XCTAssertEqual(db.terms.count, 1)
        XCTAssertEqual(db.terms.first?.keyword, "Aiko")
    }

    func testGuardedReplaceTermSkipsWhenLocalTermChanged() throws {
        let local = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        db.updateTerm(id: local.id, collectionMode: .mediaOnly)
        let server = WatchTerm(id: "server-42", keyword: "Aiko", collection_mode: .allInfo)

        XCTAssertFalse(
            db.replaceTerm(localId: local.id, with: server, ifUnchangedFrom: local),
            "A delayed backend create response must not overwrite a newer local edit."
        )
        XCTAssertEqual(db.terms.first?.id, local.id)
        XCTAssertEqual(db.terms.first?.collection_mode, .mediaOnly)
    }

    func testGuardedReplaceTermSwapsWhenLocalTermUnchanged() throws {
        let local = db.saveTerm(keyword: "Aiko", collectionMode: .allInfo)
        let server = WatchTerm(id: "server-42", keyword: "Aiko", collection_mode: .allInfo)

        XCTAssertTrue(db.replaceTerm(localId: local.id, with: server, ifUnchangedFrom: local))
        XCTAssertEqual(db.terms.first?.id, "server-42")
    }

    func testRemoveSavedDeletesBookmark() throws {
        db.setSubscribedPlatforms(platforms: ["youtube"])
        let now = ISO8601DateFormatter().string(from: Date())
        let item = FeedItem(id: "youtube:x1", platform: "youtube", url: "https://youtu.be/x1",
                            title: nil, content_text: nil, author: nil, thumbnail_url: nil,
                            media_type: "video", published_at: now,
                            watch_term_keyword: "Aiko", fetched_at: now, source: "youtube_api")
        _ = db.toggleSaved(item: item)
        XCTAssertEqual(db.getSaved().count, 1)
        db.removeSaved(id: "youtube:x1")
        XCTAssertEqual(db.getSaved().count, 0)
    }

    func testRemoveSavedNoOpForUnknownId() throws {
        XCTAssertNoThrow(db.removeSaved(id: "does-not-exist"))
        XCTAssertEqual(db.getSaved().count, 0)
    }

    // MARK: - WatchTerm custom JSON decoding
    func testWatchTermDecodesIntIdAsString() throws {
        let json = #"{"id":42,"keyword":"Aiko","collection_mode":"all_info","is_active":true,"notify_on_new":false,"aliases":[],"created_at":"2024-01-01T00:00:00Z"}"#
        let term = try JSONDecoder().decode(WatchTerm.self, from: Data(json.utf8))
        XCTAssertEqual(term.id, "42")
    }

    func testWatchTermFallsBackToAllInfoForUnknownCollectionMode() throws {
        let json = #"{"id":"x","keyword":"Aiko","collection_mode":"unknown_future_mode","is_active":true,"notify_on_new":false,"aliases":[],"created_at":"2024-01-01T00:00:00Z"}"#
        let term = try JSONDecoder().decode(WatchTerm.self, from: Data(json.utf8))
        XCTAssertEqual(term.collection_mode, .allInfo)
    }

    func testWatchTermFallsBackToAllInfoWhenCollectionModeMissing() throws {
        let json = #"{"id":"x","keyword":"Aiko","is_active":true,"notify_on_new":false,"aliases":[],"created_at":"2024-01-01T00:00:00Z"}"#
        let term = try JSONDecoder().decode(WatchTerm.self, from: Data(json.utf8))
        XCTAssertEqual(term.collection_mode, .allInfo)
    }

    func testWatchTermDefaultsEmptyAliasesWhenMissing() throws {
        let json = #"{"id":"x","keyword":"Aiko","created_at":"2024-01-01T00:00:00Z"}"#
        let term = try JSONDecoder().decode(WatchTerm.self, from: Data(json.utf8))
        XCTAssertEqual(term.aliases, [])
        XCTAssertTrue(term.is_active)
        // notify_on_new now defaults to true so new terms alert on new feed items.
        XCTAssertTrue(term.notify_on_new)
    }

    // MARK: - Content Cache
    func testContentCacheRoundTrip() throws {
        db.saveContentCache(id: "article-123", html: "<h1>Hello</h1>")
        let result = db.getContentCache(id: "article-123")
        XCTAssertEqual(result, "<h1>Hello</h1>")
    }

    func testContentCacheRemove() throws {
        db.saveContentCache(id: "to-remove", html: "<p>content</p>")
        XCTAssertEqual(db.getContentCache(id: "to-remove"), "<p>content</p>")
        db.removeContentCache(id: "to-remove")
        XCTAssertNil(db.getContentCache(id: "to-remove"))
    }

    func testContentCacheMissingReturnsNil() throws {
        XCTAssertNil(db.getContentCache(id: "nonexistent-key"))
    }

    // MARK: - Stats
    func testGetStatsCountsByPlatform() throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let items = [
            FeedItem(id: "youtube:s1", platform: "youtube", url: "https://yt/s1", title: "A",
                     content_text: nil, author: nil, thumbnail_url: nil, media_type: "video",
                     published_at: now, watch_term_keyword: "Aiko", fetched_at: now, source: "youtube_api"),
            FeedItem(id: "youtube:s2", platform: "youtube", url: "https://yt/s2", title: "B",
                     content_text: nil, author: nil, thumbnail_url: nil, media_type: "video",
                     published_at: now, watch_term_keyword: "Aiko", fetched_at: now, source: "youtube_api"),
            FeedItem(id: "news:s3", platform: "news", url: "https://news/s3", title: "C",
                     content_text: nil, author: nil, thumbnail_url: nil, media_type: "article",
                     published_at: now, watch_term_keyword: "Aiko", fetched_at: now),
        ]
        _ = db.mergeItems(newItems: items)
        let stats = db.getStats()
        XCTAssertEqual(stats.total, 3)
        XCTAssertEqual(stats.byPlatform["youtube"], 2)
        XCTAssertEqual(stats.byPlatform["news"], 1)
    }

    func testGetStatsEmptyDB() throws {
        let stats = db.getStats()
        XCTAssertEqual(stats.total, 0)
        XCTAssertTrue(stats.byPlatform.isEmpty)
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

    func testRandomKeywordsAllSources() throws {
        // Generate random keywords
        let randomKeywords = (0..<3).map { _ in UUID().uuidString }
        
        let sources = Platform.all.map { $0.id }
        db.setSubscribedPlatforms(platforms: sources)
        
        for kw in randomKeywords {
            _ = db.saveTerm(keyword: kw, collectionMode: .allInfo)
        }
        
        let nowString = ISO8601DateFormatter().string(from: Date())
        var newItems = [FeedItem]()
        
        for kw in randomKeywords {
            for source in sources {
                let item = FeedItem(
                    id: "\(source):mock:\(kw)",
                    platform: source,
                    url: "https://mock.com/\(source)/\(kw)",
                    title: "\(kw) random update from \(source)",
                    content_text: "Summary of \(kw) on \(source)",
                    author: "Mock User",
                    thumbnail_url: nil,
                    media_type: "article",
                    published_at: nowString,
                    watch_term_keyword: kw,
                    fetched_at: nowString,
                    source: source == "youtube" ? "youtube_api" : nil
                )
                newItems.append(item)
            }
        }
        
        let addedCount = db.mergeItems(newItems: newItems)
        XCTAssertEqual(addedCount, randomKeywords.count * sources.count)
        
        for kw in randomKeywords {
            let queryResult = db.queryFeed(keyword: kw, days: 30)
            XCTAssertGreaterThanOrEqual(queryResult.count, sources.count, "Should fetch results from all sources for \(kw)")
            
            let platformsFound = Set(queryResult.map { $0.platform })
            let expectedPlatforms = Set(sources)
            XCTAssertEqual(platformsFound, expectedPlatforms, "All sources should be present for \(kw)")
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
        KeychainHelper.delete(key: "apns_device_token")
        KeychainHelper.delete(key: "apns_device_environment")
        KeychainHelper.delete(key: "apns_device_secret")
        try super.tearDownWithError()
    }

    private static let mockURL = URL(string: "https://mock.test")!

    private static func response(status: Int) -> HTTPURLResponse {
        HTTPURLResponse(url: mockURL, statusCode: status, httpVersion: nil, headerFields: nil)!
    }

    func testAPIClientHTTPStatusLocalizedDescriptionIncludesDetail() {
        XCTAssertEqual(
            APIClientError.httpStatus(409, detail: "A watch term with this keyword already exists").localizedDescription,
            "HTTP 409: A watch term with this keyword already exists"
        )
        XCTAssertEqual(
            APIClientError.httpStatus(500, detail: "  ").localizedDescription,
            "HTTP 500"
        )
        XCTAssertTrue(
            APIClientError.httpStatus(
                409,
                detail: #"{"code":"notification_device_required","message":"device required"}"#
            ).requiresVerifiedNotificationDevice
        )
        XCTAssertFalse(
            APIClientError.httpStatus(
                409,
                detail: "Notification-enabled watch terms require a verified APNs device"
            ).requiresVerifiedNotificationDevice
        )
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

    // 4xx response -> preserves HTTP status
    func testFetchWatchTerms404Throws() async throws {
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 404)) }

        do {
            _ = try await NetworkManager.shared.fetchWatchTerms()
            XCTFail("Expected error not thrown")
        } catch APIClientError.httpStatus(let status, _) {
            XCTAssertEqual(status, 404)
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

    // 5xx response -> preserves HTTP status
    func testFetchFeed500Throws() async throws {
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 500)) }

        do {
            _ = try await NetworkManager.shared.fetchFeed(limit: 10, days: 7)
            XCTFail("Expected error not thrown")
        } catch APIClientError.httpStatus(let status, _) {
            XCTAssertEqual(status, 500)
        }
    }

    func testFetchFeedRefreshesRejectedDeviceCredentialAndRetries() async throws {
        let token = String(repeating: "a", count: 64)
        KeychainHelper.write(key: "apns_device_token", value: token)
        KeychainHelper.write(key: "apns_device_environment", value: NetworkManager.shared.apnsEnvironment)
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")

        var feedRequests = 0
        var registrationRequests = 0
        MockURLProtocol.handler = { request in
            switch request.url?.path {
            case "/api/feed":
                feedRequests += 1
                XCTAssertEqual(request.value(forHTTPHeaderField: "X-Device-Token"), token)
                XCTAssertEqual(request.value(forHTTPHeaderField: "X-Device-Secret"), "device-secret")
                if feedRequests == 1 {
                    return (Data(), Self.response(status: 401))
                }
                return (Data("[]".utf8), Self.response(status: 200))
            case "/api/devices/apns-token":
                registrationRequests += 1
                return (Data(), Self.response(status: 201))
            default:
                XCTFail("Unexpected request path: \(request.url?.path ?? "nil")")
                return (Data(), Self.response(status: 500))
            }
        }

        let items = try await NetworkManager.shared.fetchFeed(limit: 10, days: 7)

        XCTAssertTrue(items.isEmpty)
        XCTAssertEqual(feedRequests, 2)
        XCTAssertEqual(registrationRequests, 1)
    }

    // 200 OK with empty array → returns empty, no throw
    func testFetchFeedEmptyArray() async throws {
        MockURLProtocol.handler = { _ in (Data("[]".utf8), Self.response(status: 200)) }

        let items = try await NetworkManager.shared.fetchFeed(limit: 10, days: 7)
        XCTAssertTrue(items.isEmpty)
    }

    func testFetchFeedSendsDeviceSecretWithoutAPNSToken() async throws {
        KeychainHelper.delete(key: "apns_device_token")
        KeychainHelper.delete(key: "apns_device_environment")
        KeychainHelper.write(key: "apns_device_secret", value: "standalone-device-secret")
        MockURLProtocol.handler = { request in
            XCTAssertNil(request.value(forHTTPHeaderField: "X-Device-Token"))
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "X-Device-Secret"),
                "standalone-device-secret"
            )
            return (Data("[]".utf8), Self.response(status: 200))
        }

        let items = try await NetworkManager.shared.fetchFeed(limit: 10, days: 7)
        XCTAssertTrue(items.isEmpty)
    }

    // 204 No Content within accept range → apiVoid succeeds
    func testDeleteWatchTermAccepts204() async throws {
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 204)) }

        // deleteWatchTerm uses apiVoid with acceptRange 200...299
        do { try await NetworkManager.shared.deleteWatchTerm(id: "99") }
        catch { XCTFail("Unexpected error: \(error)") }
    }

    func testDeleteWatchTermAccepts404AsAlreadyDeleted() async throws {
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 404)) }

        do { try await NetworkManager.shared.deleteWatchTerm(id: "99") }
        catch { XCTFail("Unexpected error: \(error)") }
    }

    func testDeleteWatchTermIfSyncedSkipsLocalUUID() async throws {
        var requestCount = 0
        MockURLProtocol.handler = { _ in
            requestCount += 1
            return (Data(), Self.response(status: 204))
        }
        let localOnlyTerm = WatchTerm(id: UUID().uuidString, keyword: "Aiko", collection_mode: .allInfo)

        try await NetworkManager.shared.deleteWatchTermIfSynced(localOnlyTerm)

        XCTAssertEqual(requestCount, 0)
    }

    func testDeleteWatchTermIfSyncedResolvesRepairedUUIDByKeyword() async throws {
        let backendTerm = WatchTerm(id: "42", keyword: "Aiko", collection_mode: .allInfo)
        let backendData = try JSONEncoder().encode([backendTerm])
        let repairedTerm = WatchTerm(
            id: UUID().uuidString,
            keyword: "Aiko",
            collection_mode: .allInfo,
            repaired_from_cache: true
        )
        var requestedPaths: [String] = []
        MockURLProtocol.handler = { request in
            requestedPaths.append(request.url?.path ?? "")
            if request.url?.path == "/api/watch-terms" {
                return (backendData, Self.response(status: 200))
            }
            if request.url?.path == "/api/watch-terms/42" {
                XCTAssertEqual(request.httpMethod, "DELETE")
                return (Data(), Self.response(status: 204))
            }
            XCTFail("Unexpected request path: \(request.url?.path ?? "nil")")
            return (Data(), Self.response(status: 500))
        }

        try await NetworkManager.shared.deleteWatchTermIfSynced(repairedTerm)

        XCTAssertEqual(requestedPaths, ["/api/watch-terms", "/api/watch-terms/42"])
    }

    @MainActor
    func testSyncWatchTermsPreservesRepairedNotificationPreference() async throws {
        let keyword = "Sync Repaired \(UUID().uuidString)"
        let repairedTerm = WatchTerm(
            id: UUID().uuidString,
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: true,
            aliases: [],
            repaired_from_cache: true
        )
        let backendTerm = WatchTerm(
            id: "42",
            keyword: keyword,
            collection_mode: .mediaOnly,
            is_active: false,
            notify_on_new: false,
            aliases: ["Aiko Alias"]
        )
        let updatedTerm = WatchTerm(
            id: "42",
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: true,
            aliases: []
        )
        _ = LocalDB.shared.deleteTerm(keyword: keyword)
        LocalDB.shared.addTermFromBackend(repairedTerm)
        defer { _ = LocalDB.shared.deleteTerm(keyword: keyword) }
        let backendData = try JSONEncoder().encode([backendTerm])
        var requestedMethodsAndPaths: [String] = []
        MockURLProtocol.handler = { request in
            requestedMethodsAndPaths.append("\(request.httpMethod ?? "") \(request.url?.path ?? "")")
            if request.url?.path == "/api/watch-terms" {
                return (backendData, Self.response(status: 200))
            }
            if request.url?.path == "/api/watch-terms/42" {
                XCTAssertEqual(request.httpMethod, "PATCH")
                return (try! JSONEncoder().encode(updatedTerm), Self.response(status: 200))
            }
            XCTFail("Unexpected request path: \(request.url?.path ?? "nil")")
            return (Data(), Self.response(status: 500))
        }

        let succeeded = await NetworkManager.shared.syncWatchTermsToBackend(localTerms: [repairedTerm])

        XCTAssertTrue(succeeded)
        XCTAssertEqual(requestedMethodsAndPaths, ["GET /api/watch-terms", "PATCH /api/watch-terms/42"])
        let syncedTerm = LocalDB.shared.term(matchingKeyword: keyword)
        XCTAssertEqual(syncedTerm?.id, "42")
        XCTAssertEqual(syncedTerm?.collection_mode, .allInfo)
        XCTAssertEqual(syncedTerm?.notify_on_new, true)
        XCTAssertEqual(syncedTerm?.aliases, [])
    }

    @MainActor
    func testSyncWatchTermsPreservesRepairedMutedPreference() async throws {
        let keyword = "Sync Repaired Muted \(UUID().uuidString)"
        let repairedTerm = WatchTerm(
            id: UUID().uuidString,
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: false,
            aliases: [],
            repaired_from_cache: true
        )
        let backendTerm = WatchTerm(
            id: "43",
            keyword: keyword,
            collection_mode: .mediaOnly,
            is_active: false,
            notify_on_new: true,
            aliases: ["Alias"]
        )
        let updatedTerm = WatchTerm(
            id: "43",
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: false,
            aliases: []
        )
        _ = LocalDB.shared.deleteTerm(keyword: keyword)
        LocalDB.shared.addTermFromBackend(repairedTerm)
        defer { _ = LocalDB.shared.deleteTerm(keyword: keyword) }
        let backendData = try JSONEncoder().encode([backendTerm])
        var capturedNotifyOnNew: Bool?
        MockURLProtocol.handler = { request in
            if request.url?.path == "/api/watch-terms" {
                return (backendData, Self.response(status: 200))
            }
            if request.url?.path == "/api/watch-terms/43" {
                XCTAssertEqual(request.httpMethod, "PATCH")
                if let body = request.httpBody,
                   let payload = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                   let notifyOnNew = payload["notify_on_new"] as? Bool {
                    capturedNotifyOnNew = notifyOnNew
                } else {
                    XCTFail("Expected notify_on_new in PATCH body")
                }
                return (try! JSONEncoder().encode(updatedTerm), Self.response(status: 200))
            }
            XCTFail("Unexpected request path: \(request.url?.path ?? "nil")")
            return (Data(), Self.response(status: 500))
        }

        let succeeded = await NetworkManager.shared.syncWatchTermsToBackend(localTerms: [repairedTerm])

        XCTAssertTrue(succeeded)
        XCTAssertEqual(capturedNotifyOnNew, false)
        let syncedTerm = LocalDB.shared.term(matchingKeyword: keyword)
        XCTAssertEqual(syncedTerm?.notify_on_new, false)
    }

    @MainActor
    func testSyncWatchTermsCreatesRepairedTermWhenBackendLacksKeyword() async throws {
        let keyword = "Sync Create \(UUID().uuidString)"
        let repairedTerm = WatchTerm(
            id: UUID().uuidString,
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: true,
            aliases: [],
            repaired_from_cache: true
        )
        let createdTerm = WatchTerm(id: "42", keyword: keyword, collection_mode: .allInfo)
        _ = LocalDB.shared.deleteTerm(keyword: keyword)
        LocalDB.shared.addTermFromBackend(repairedTerm)
        defer { _ = LocalDB.shared.deleteTerm(keyword: keyword) }
        let emptyBackendData = try JSONEncoder().encode([WatchTerm]())
        let createdData = try JSONEncoder().encode(createdTerm)
        var requestedMethodsAndPaths: [String] = []
        var capturedBody: [String: Any] = [:]
        MockURLProtocol.handler = { request in
            requestedMethodsAndPaths.append("\(request.httpMethod ?? "") \(request.url?.path ?? "")")
            if request.url?.path == "/api/watch-terms", request.httpMethod == "GET" {
                return (emptyBackendData, Self.response(status: 200))
            }
            if request.url?.path == "/api/watch-terms", request.httpMethod == "POST" {
                if let body = request.httpBody,
                   let parsed = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                    capturedBody = parsed
                } else {
                    XCTFail("Expected JSON request body")
                }
                return (createdData, Self.response(status: 200))
            }
            XCTFail("Unexpected request: \(request.httpMethod ?? "") \(request.url?.path ?? "nil")")
            return (Data(), Self.response(status: 500))
        }

        let succeeded = await NetworkManager.shared.syncWatchTermsToBackend(localTerms: [repairedTerm])

        XCTAssertTrue(succeeded)
        XCTAssertEqual(requestedMethodsAndPaths, ["GET /api/watch-terms", "POST /api/watch-terms"])
        XCTAssertEqual(capturedBody["keyword"] as? String, keyword)
        XCTAssertEqual(capturedBody["collection_mode"] as? String, "all_info")
        XCTAssertEqual(capturedBody["notify_on_new"] as? Bool, true)
        XCTAssertEqual(capturedBody["is_active"] as? Bool, true)
        let syncedTerm = LocalDB.shared.term(matchingKeyword: keyword)
        XCTAssertEqual(syncedTerm?.id, "42")
        XCTAssertFalse(syncedTerm?.repaired_from_cache ?? true)
    }

    @MainActor
    func testSyncWatchTermsPreservesNotificationPreferenceWhenDeviceIsUnverified() async throws {
        let keyword = "Sync Create Muted \(UUID().uuidString)"
        let repairedTerm = WatchTerm(
            id: UUID().uuidString,
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: true,
            aliases: ["Alias"],
            repaired_from_cache: true
        )
        let createdTerm = WatchTerm(
            id: "42",
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: false,
            aliases: ["Alias"]
        )
        _ = LocalDB.shared.deleteTerm(keyword: keyword)
        LocalDB.shared.addTermFromBackend(repairedTerm)
        defer { _ = LocalDB.shared.deleteTerm(keyword: keyword) }
        let emptyBackendData = try JSONEncoder().encode([WatchTerm]())
        let createdData = try JSONEncoder().encode(createdTerm)
        var requestedMethodsAndPaths: [String] = []
        var capturedNotifyValues: [Bool] = []
        MockURLProtocol.handler = { request in
            requestedMethodsAndPaths.append("\(request.httpMethod ?? "") \(request.url?.path ?? "")")
            if request.url?.path == "/api/watch-terms", request.httpMethod == "GET" {
                return (emptyBackendData, Self.response(status: 200))
            }
            if request.url?.path == "/api/watch-terms", request.httpMethod == "POST" {
                if let body = request.httpBody,
                   let parsed = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                   let notifyOnNew = parsed["notify_on_new"] as? Bool {
                    capturedNotifyValues.append(notifyOnNew)
                } else {
                    XCTFail("Expected JSON request body")
                }
                if capturedNotifyValues == [true] {
                    return (Data("{\"detail\":{\"code\":\"notification_device_required\",\"message\":\"device required\"}}".utf8), Self.response(status: 409))
                }
                return (createdData, Self.response(status: 200))
            }
            XCTFail("Unexpected request: \(request.httpMethod ?? "") \(request.url?.path ?? "nil")")
            return (Data(), Self.response(status: 500))
        }

        let succeeded = await NetworkManager.shared.syncWatchTermsToBackend(localTerms: [repairedTerm])

        XCTAssertFalse(succeeded)
        XCTAssertEqual(requestedMethodsAndPaths, ["GET /api/watch-terms", "POST /api/watch-terms"])
        XCTAssertEqual(capturedNotifyValues, [true])
        let syncedTerm = LocalDB.shared.term(matchingKeyword: keyword)
        XCTAssertEqual(syncedTerm?.id, repairedTerm.id)
        XCTAssertEqual(syncedTerm?.notify_on_new, true)
        XCTAssertTrue(syncedTerm?.repaired_from_cache ?? false)
    }

    @MainActor
    func testSyncWatchTermsDoesNotRetryMutedAfterDuplicateConflict() async throws {
        let keyword = "Sync Create Duplicate \(UUID().uuidString)"
        let repairedTerm = WatchTerm(
            id: UUID().uuidString,
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: true,
            aliases: ["Alias"],
            repaired_from_cache: true
        )
        _ = LocalDB.shared.deleteTerm(keyword: keyword)
        LocalDB.shared.addTermFromBackend(repairedTerm)
        defer { _ = LocalDB.shared.deleteTerm(keyword: keyword) }
        let emptyBackendData = try JSONEncoder().encode([WatchTerm]())
        var requestedMethodsAndPaths: [String] = []
        var capturedNotifyValues: [Bool] = []
        MockURLProtocol.handler = { request in
            requestedMethodsAndPaths.append("\(request.httpMethod ?? "") \(request.url?.path ?? "")")
            if request.url?.path == "/api/watch-terms", request.httpMethod == "GET" {
                return (emptyBackendData, Self.response(status: 200))
            }
            if request.url?.path == "/api/watch-terms", request.httpMethod == "POST" {
                if let body = request.httpBody,
                   let parsed = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                   let notifyOnNew = parsed["notify_on_new"] as? Bool {
                    capturedNotifyValues.append(notifyOnNew)
                } else {
                    XCTFail("Expected JSON request body")
                }
                return (Data("{\"detail\":\"A watch term with this keyword already exists\"}".utf8), Self.response(status: 409))
            }
            XCTFail("Unexpected request: \(request.httpMethod ?? "") \(request.url?.path ?? "nil")")
            return (Data(), Self.response(status: 500))
        }

        let succeeded = await NetworkManager.shared.syncWatchTermsToBackend(localTerms: [repairedTerm])

        XCTAssertFalse(succeeded)
        XCTAssertEqual(requestedMethodsAndPaths, ["GET /api/watch-terms", "POST /api/watch-terms"])
        XCTAssertEqual(capturedNotifyValues, [true])
        let syncedTerm = LocalDB.shared.term(matchingKeyword: keyword)
        XCTAssertEqual(syncedTerm?.notify_on_new, true)
        XCTAssertTrue(syncedTerm?.repaired_from_cache ?? false)
    }

    @MainActor
    func testSyncWatchTermsDoesNotRetryMutedAfterServerError() async throws {
        let keyword = "Sync Create Server Error \(UUID().uuidString)"
        let repairedTerm = WatchTerm(
            id: UUID().uuidString,
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: true,
            aliases: ["Alias"],
            repaired_from_cache: true
        )
        _ = LocalDB.shared.deleteTerm(keyword: keyword)
        LocalDB.shared.addTermFromBackend(repairedTerm)
        defer { _ = LocalDB.shared.deleteTerm(keyword: keyword) }
        let emptyBackendData = try JSONEncoder().encode([WatchTerm]())
        var requestedMethodsAndPaths: [String] = []
        var capturedNotifyValues: [Bool] = []
        MockURLProtocol.handler = { request in
            requestedMethodsAndPaths.append("\(request.httpMethod ?? "") \(request.url?.path ?? "")")
            if request.url?.path == "/api/watch-terms", request.httpMethod == "GET" {
                return (emptyBackendData, Self.response(status: 200))
            }
            if request.url?.path == "/api/watch-terms", request.httpMethod == "POST" {
                if let body = request.httpBody,
                   let parsed = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                   let notifyOnNew = parsed["notify_on_new"] as? Bool {
                    capturedNotifyValues.append(notifyOnNew)
                } else {
                    XCTFail("Expected JSON request body")
                }
                return (Data("{\"detail\":\"temporary backend failure\"}".utf8), Self.response(status: 500))
            }
            XCTFail("Unexpected request: \(request.httpMethod ?? "") \(request.url?.path ?? "nil")")
            return (Data(), Self.response(status: 500))
        }

        let succeeded = await NetworkManager.shared.syncWatchTermsToBackend(localTerms: [repairedTerm])

        XCTAssertFalse(succeeded)
        XCTAssertEqual(requestedMethodsAndPaths, ["GET /api/watch-terms", "POST /api/watch-terms"])
        XCTAssertEqual(capturedNotifyValues, [true])
        let syncedTerm = LocalDB.shared.term(matchingKeyword: keyword)
        XCTAssertEqual(syncedTerm?.notify_on_new, true)
        XCTAssertTrue(syncedTerm?.repaired_from_cache ?? false)
    }

    @MainActor
    func testSyncWatchTermsPatchesNormalLocalTermWhenBackendDiffers() async throws {
        let keyword = "Sync Patch \(UUID().uuidString)"
        let localTerm = WatchTerm(
            id: UUID().uuidString,
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: true,
            aliases: ["Aiko Alias"]
        )
        let backendTerm = WatchTerm(
            id: "42",
            keyword: keyword,
            collection_mode: .mediaOnly,
            is_active: false,
            notify_on_new: false,
            aliases: []
        )
        let updatedTerm = WatchTerm(
            id: "42",
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: true,
            aliases: ["Aiko Alias"]
        )
        _ = LocalDB.shared.deleteTerm(keyword: keyword)
        LocalDB.shared.addTermFromBackend(localTerm)
        defer { _ = LocalDB.shared.deleteTerm(keyword: keyword) }
        let backendData = try JSONEncoder().encode([backendTerm])
        let updatedData = try JSONEncoder().encode(updatedTerm)
        var requestedMethodsAndPaths: [String] = []
        var capturedBody: [String: Any] = [:]
        MockURLProtocol.handler = { request in
            requestedMethodsAndPaths.append("\(request.httpMethod ?? "") \(request.url?.path ?? "")")
            if request.url?.path == "/api/watch-terms" {
                return (backendData, Self.response(status: 200))
            }
            if request.url?.path == "/api/watch-terms/42" {
                XCTAssertEqual(request.httpMethod, "PATCH")
                if let body = request.httpBody,
                   let parsed = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                    capturedBody = parsed
                } else {
                    XCTFail("Expected JSON request body")
                }
                return (updatedData, Self.response(status: 200))
            }
            XCTFail("Unexpected request: \(request.httpMethod ?? "") \(request.url?.path ?? "nil")")
            return (Data(), Self.response(status: 500))
        }

        let succeeded = await NetworkManager.shared.syncWatchTermsToBackend(localTerms: [localTerm])

        XCTAssertTrue(succeeded)
        XCTAssertEqual(requestedMethodsAndPaths, ["GET /api/watch-terms", "PATCH /api/watch-terms/42"])
        XCTAssertEqual(capturedBody["collection_mode"] as? String, "all_info")
        XCTAssertEqual(capturedBody["notify_on_new"] as? Bool, true)
        XCTAssertEqual(capturedBody["is_active"] as? Bool, true)
        XCTAssertEqual(capturedBody["aliases"] as? [String], ["Aiko Alias"])
        let syncedTerm = LocalDB.shared.term(matchingKeyword: keyword)
        XCTAssertEqual(syncedTerm?.id, "42")
        XCTAssertEqual(syncedTerm?.aliases, ["Aiko Alias"])
    }

    @MainActor
    func testSyncWatchTermsDoesNotOverwriteNewerLocalEditAfterPatchReturns() async throws {
        let keyword = "Sync Patch Race \(UUID().uuidString)"
        let localTerm = WatchTerm(
            id: UUID().uuidString,
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: true,
            aliases: []
        )
        let backendTerm = WatchTerm(
            id: "42",
            keyword: keyword,
            collection_mode: .mediaOnly,
            is_active: false,
            notify_on_new: false,
            aliases: []
        )
        let updatedTerm = WatchTerm(
            id: "42",
            keyword: keyword,
            collection_mode: .allInfo,
            is_active: true,
            notify_on_new: true,
            aliases: []
        )
        _ = LocalDB.shared.deleteTerm(keyword: keyword)
        LocalDB.shared.addTermFromBackend(localTerm)
        defer { _ = LocalDB.shared.deleteTerm(keyword: keyword) }
        let backendData = try JSONEncoder().encode([backendTerm])
        let updatedData = try JSONEncoder().encode(updatedTerm)

        MockURLProtocol.handler = { request in
            if request.url?.path == "/api/watch-terms" {
                return (backendData, Self.response(status: 200))
            }
            if request.url?.path == "/api/watch-terms/42" {
                let edited = DispatchSemaphore(value: 0)
                Task { @MainActor in
                    LocalDB.shared.updateTerm(id: localTerm.id, collectionMode: .mediaOnly)
                    edited.signal()
                }
                edited.wait()
                return (updatedData, Self.response(status: 200))
            }
            XCTFail("Unexpected request: \(request.httpMethod ?? "") \(request.url?.path ?? "nil")")
            return (Data(), Self.response(status: 500))
        }

        let succeeded = await NetworkManager.shared.syncWatchTermsToBackend(localTerms: [localTerm])

        XCTAssertFalse(succeeded)
        let currentTerm = LocalDB.shared.term(matchingKeyword: keyword)
        XCTAssertEqual(currentTerm?.id, localTerm.id)
        XCTAssertEqual(currentTerm?.collection_mode, .mediaOnly)
    }

    func testMuteFeedItemPostsSourceAndWatchTermIDs() async throws {
        var capturedBody: [String: Any] = [:]
        MockURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/feed/muted-items")
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
            if let body = request.httpBody,
               let parsed = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                capturedBody = parsed
            } else {
                XCTFail("Expected JSON request body")
            }
            return (Data(), Self.response(status: 204))
        }

        try await NetworkManager.shared.muteFeedItem(
            sourceItemID: "youtube:test123",
            watchTermID: 42
        )

        XCTAssertEqual(capturedBody["source_item_id"] as? String, "youtube:test123")
        XCTAssertEqual(capturedBody["watch_term_id"] as? Int, 42)
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

    func testFetchWatchTermsRetriesConnectionLostOnce() async throws {
        var attemptCount = 0
        let term = WatchTerm(id: "42", keyword: "Aiko", collection_mode: .allInfo)
        let data = try JSONEncoder().encode([term])
        MockURLProtocol.errorHandler = { _ in
            attemptCount += 1
            return attemptCount == 1 ? URLError(.networkConnectionLost) : nil
        }
        MockURLProtocol.handler = { _ in (data, Self.response(status: 200)) }

        let terms = try await NetworkManager.shared.fetchWatchTerms()

        XCTAssertEqual(attemptCount, 2)
        XCTAssertEqual(terms.first?.keyword, "Aiko")
    }

    func testAPIVoidRetriesConnectionLostOnce() async throws {
        var attemptCount = 0
        MockURLProtocol.errorHandler = { _ in
            attemptCount += 1
            return attemptCount == 1 ? URLError(.networkConnectionLost) : nil
        }
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 204)) }

        try await NetworkManager.shared.deleteWatchTerm(id: "99")

        XCTAssertEqual(attemptCount, 2)
    }

    // createWatchTerm sends POST and decodes the returned term
    func testCreateWatchTermSendsPostAndDecodesTerm() async throws {
        var capturedMethod: String?
        var capturedDeviceToken: String?
        var capturedDeviceSecret: String?
        let deviceToken = String(repeating: "a", count: 64)
        KeychainHelper.write(key: "apns_device_token", value: deviceToken)
        KeychainHelper.write(key: "apns_device_environment", value: NetworkManager.shared.apnsEnvironment)
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")
        var capturedBody: [String: Any]?
        let expected = WatchTerm(id: "7", keyword: "Haruka", collection_mode: .allInfo)
        let responseData = try JSONEncoder().encode(expected)
        MockURLProtocol.handler = { req in
            capturedMethod = req.httpMethod
            capturedDeviceToken = req.value(forHTTPHeaderField: "X-Device-Token")
            capturedDeviceSecret = req.value(forHTTPHeaderField: "X-Device-Secret")
            if let body = req.httpBody {
                capturedBody = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
            }
            return (responseData, Self.response(status: 200))
        }

        let created = try await NetworkManager.shared.createWatchTerm(
            keyword: "Haruka",
            collectionMode: .allInfo,
            notifyOnNew: false,
            isActive: false
        )
        XCTAssertEqual(capturedMethod, "POST")
        XCTAssertEqual(capturedDeviceToken, deviceToken)
        XCTAssertEqual(capturedDeviceSecret, "device-secret")
        XCTAssertEqual(capturedBody?["keyword"] as? String, "Haruka")
        XCTAssertEqual(capturedBody?["collection_mode"] as? String, "all_info")
        XCTAssertEqual(capturedBody?["notify_on_new"] as? Bool, false)
        XCTAssertEqual(capturedBody?["is_active"] as? Bool, false)
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

    // When `since` is provided, the URL uses since= and omits days=
    func testFetchFeedUsedSinceWhenProvided() async throws {
        var capturedURL: URL?
        MockURLProtocol.handler = { req in
            capturedURL = req.url
            return (Data("[]".utf8), Self.response(status: 200))
        }

        let since = "2026-06-01T00:00:00+00:00"
        _ = try await NetworkManager.shared.fetchFeed(days: 30, since: since)

        let query = capturedURL?.query ?? ""
        XCTAssertTrue(query.contains("since"), "URL must contain 'since' parameter")
        XCTAssertFalse(query.contains("days"), "URL must NOT contain 'days' when 'since' is set")
    }

    // When `since` is nil, the URL uses days= and omits since=
    func testFetchFeedUsesDaysWhenSinceIsNil() async throws {
        var capturedURL: URL?
        MockURLProtocol.handler = { req in
            capturedURL = req.url
            return (Data("[]".utf8), Self.response(status: 200))
        }

        _ = try await NetworkManager.shared.fetchFeed(days: 14, since: nil)

        let query = capturedURL?.query ?? ""
        XCTAssertTrue(query.contains("days=14"), "URL must contain 'days=14'")
        XCTAssertFalse(query.contains("since"), "URL must NOT contain 'since' when it is nil")
    }

    func testFetchFeedIncludesTermIdWhenProvided() async throws {
        var capturedURL: URL?
        MockURLProtocol.handler = { req in
            capturedURL = req.url
            return (Data("[]".utf8), Self.response(status: 200))
        }

        _ = try await NetworkManager.shared.fetchFeed(termId: 42, days: 30)

        let query = capturedURL?.query ?? ""
        XCTAssertTrue(query.contains("term_id=42"), "URL must include term_id=42")
    }

    func testFetchFeedIncludesPlatformWhenProvided() async throws {
        var capturedURL: URL?
        MockURLProtocol.handler = { req in
            capturedURL = req.url
            return (Data("[]".utf8), Self.response(status: 200))
        }

        _ = try await NetworkManager.shared.fetchFeed(platform: "youtube", days: 30)

        let query = capturedURL?.query ?? ""
        XCTAssertTrue(query.contains("platform=youtube"), "URL must include platform=youtube")
    }

    func testFetchFeedOmitsTermIdAndPlatformWhenNil() async throws {
        var capturedURL: URL?
        MockURLProtocol.handler = { req in
            capturedURL = req.url
            return (Data("[]".utf8), Self.response(status: 200))
        }

        _ = try await NetworkManager.shared.fetchFeed(days: 30)

        let query = capturedURL?.query ?? ""
        XCTAssertFalse(query.contains("term_id"), "term_id must be absent when not provided")
        XCTAssertFalse(query.contains("platform"), "platform must be absent when not provided")
    }

    func testBackendFetchesUseProvidedTimeout() async throws {
        var capturedFeedTimeout: TimeInterval?
        MockURLProtocol.handler = { req in
            capturedFeedTimeout = req.timeoutInterval
            return (Data("[]".utf8), Self.response(status: 200))
        }

        _ = try await NetworkManager.shared.fetchFeed(
            days: 30,
            timeout: BackgroundRefreshPolicy.foregroundBackendTimeout
        )

        XCTAssertEqual(capturedFeedTimeout, BackgroundRefreshPolicy.foregroundBackendTimeout)

        let term = WatchTerm(id: "42", keyword: "Aiko", collection_mode: .allInfo)
        let data = try JSONEncoder().encode([term])
        var capturedTermsTimeout: TimeInterval?
        MockURLProtocol.handler = { req in
            capturedTermsTimeout = req.timeoutInterval
            return (data, Self.response(status: 200))
        }

        _ = try await NetworkManager.shared.fetchWatchTerms(
            timeout: BackgroundRefreshPolicy.foregroundBackendTimeout
        )

        XCTAssertEqual(capturedTermsTimeout, BackgroundRefreshPolicy.foregroundBackendTimeout)

        let termData = try JSONEncoder().encode(term)
        var capturedCreateTimeout: TimeInterval?
        MockURLProtocol.handler = { req in
            capturedCreateTimeout = req.timeoutInterval
            return (termData, Self.response(status: 200))
        }

        _ = try await NetworkManager.shared.createWatchTerm(
            keyword: "Aiko",
            collectionMode: .allInfo,
            notifyOnNew: false,
            timeout: BackgroundRefreshPolicy.foregroundBackendTimeout
        )

        XCTAssertEqual(capturedCreateTimeout, BackgroundRefreshPolicy.foregroundBackendTimeout)

        var capturedUpdateTimeout: TimeInterval?
        MockURLProtocol.handler = { req in
            capturedUpdateTimeout = req.timeoutInterval
            return (termData, Self.response(status: 200))
        }

        _ = try await NetworkManager.shared.updateWatchTerm(
            id: "42",
            collectionMode: .allInfo,
            timeout: BackgroundRefreshPolicy.foregroundBackendTimeout
        )

        XCTAssertEqual(capturedUpdateTimeout, BackgroundRefreshPolicy.foregroundBackendTimeout)
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
        XCTAssertEqual(items.first?.url, "https://youtu.be/test123")
        XCTAssertEqual(items.first?.watch_term_keyword, "Aiko")
        XCTAssertEqual(items.first?.watch_term_id, 2)
        XCTAssertEqual(items.first?.media_type, "video")
    }

    // BackendFeedItem with nil media_type falls back to "article"
    func testFetchFeedNilMediaTypeFallsBackToArticle() async throws {
        let now = ISO8601DateFormatter().string(from: Date())
        let sourceItem = SourceItem(
            id: "news:abc", platform: "news",
            url: "https://news.example.com/abc", published_at: now,
            author: nil, title: "Article Title", content_text: nil,
            media_type: nil, thumbnail_url: nil
        )
        let backendItem = BackendFeedItem(
            match_id: 9, watch_term_id: 1, watch_term_keyword: "Aiko",
            item: sourceItem, matched_at: now
        )
        let data = try JSONEncoder().encode([backendItem])
        MockURLProtocol.handler = { _ in (data, Self.response(status: 200)) }

        let items = try await NetworkManager.shared.fetchFeed()
        XCTAssertEqual(items.first?.media_type, "article", "nil media_type should fall back to 'article'")
    }

    // fetchCredentials decodes credential list
    func testFetchCredentialsDecodesList() async throws {
        let creds = [
            Credential(platform: "youtube", has_bearer_token: true, has_api_key: false, has_api_secret: false, updated_at: nil),
            Credential(platform: "twitter", has_bearer_token: false, has_api_key: false, has_api_secret: false, updated_at: nil),
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
        var capturedBody: [String: Any]?
        MockURLProtocol.handler = { req in
            capturedMethod = req.httpMethod
            if let body = req.httpBody {
                capturedBody = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
            }
            return (Data(), Self.response(status: 201))
        }

        do { try await NetworkManager.shared.registerAPNSDeviceToken(String(repeating: "a", count: 64)) }
        catch { XCTFail("Unexpected error: \(error)") }
        XCTAssertEqual(capturedMethod, "POST")
        XCTAssertEqual(capturedBody?["token"] as? String, String(repeating: "a", count: 64))
        XCTAssertEqual(capturedBody?["environment"] as? String, NetworkManager.shared.apnsEnvironment)
        XCTAssertNotNil(capturedBody?["device_secret"] as? String)
        XCTAssertEqual(NetworkManager.shared.registeredAPNSDeviceToken, String(repeating: "a", count: 64))
        XCTAssertEqual(NetworkManager.shared.registeredAPNSDeviceEnvironment, NetworkManager.shared.apnsEnvironment)
    }

    @MainActor
    func testNotificationManagerUploadsRegisteredDeviceToken() async throws {
        let seededTerm = LocalDB.shared.saveTerm(
            keyword: "CI APNS Sync \(UUID().uuidString)",
            collectionMode: .allInfo
        )
        defer { LocalDB.shared.deleteTerm(id: seededTerm.id) }
        let backendTermsData = try JSONEncoder().encode([seededTerm])
        var capturedPaths: [String] = []
        var capturedBody: [String: Any]?
        MockURLProtocol.handler = { req in
            let path = req.url?.path ?? ""
            capturedPaths.append(path)
            if path == "/api/devices/apns-token", let body = req.httpBody {
                capturedBody = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
            }
            if path == "/api/watch-terms" {
                return (backendTermsData, Self.response(status: 200))
            }
            return (Data(), Self.response(status: 201))
        }

        let manager = NotificationManager(center: MockNotificationCenter(status: .authorized))
        await manager.handleRegisteredDeviceToken(Data(repeating: 0xab, count: 32))

        XCTAssertTrue(capturedPaths.contains("/api/devices/apns-token"))
        XCTAssertTrue(capturedPaths.contains("/api/watch-terms"))
        XCTAssertEqual(capturedBody?["token"] as? String, String(repeating: "ab", count: 32))
        XCTAssertNotNil(capturedBody?["environment"] as? String)
        XCTAssertNotNil(capturedBody?["device_id"] as? String)
    }

    func testSendRemoteTestPushUsesDeviceScopedEndpointWhenTokenStored() async throws {
        KeychainHelper.write(key: "apns_device_token", value: String(repeating: "a", count: 64))
        KeychainHelper.write(key: "apns_device_environment", value: NetworkManager.shared.apnsEnvironment)
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")
        var capturedMethod: String?
        var capturedPath: String?
        var capturedAuthHeader: String?
        var capturedBody: [String: Any]?
        let body = Data(#"{"configured":true,"results":[{"token":"abcd1234","environment":"production","host":"https://api.push.apple.com","status":200}],"pruned_tokens":0}"#.utf8)
        MockURLProtocol.handler = { req in
            capturedMethod = req.httpMethod
            capturedAuthHeader = req.value(forHTTPHeaderField: "Authorization")
            capturedPath = req.url?.path
            if let body = req.httpBody {
                capturedBody = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
            }
            return (body, Self.response(status: 200))
        }

        let report = try await NetworkManager.shared.sendRemoteTestPush()

        XCTAssertEqual(capturedMethod, "POST")
        XCTAssertNil(capturedAuthHeader)
        XCTAssertEqual(capturedPath, "/api/devices/apns-test-push")
        XCTAssertEqual(capturedBody?["token"] as? String, String(repeating: "a", count: 64))
        XCTAssertEqual(capturedBody?["device_secret"] as? String, "device-secret")
        XCTAssertTrue(report.configured)
        XCTAssertEqual(report.results.first?.status, 200)
    }

    func testSendRemoteTestPushDecodesQueuedAcceptance() async throws {
        KeychainHelper.write(key: "apns_device_token", value: String(repeating: "a", count: 64))
        KeychainHelper.write(key: "apns_device_environment", value: NetworkManager.shared.apnsEnvironment)
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")
        let body = Data(#"{"configured":true,"results":[{"token":"abcd1234","environment":"sandbox","status":202,"reason":"queued"}],"pruned_tokens":0,"note":"queued"}"#.utf8)
        MockURLProtocol.handler = { _ in
            (body, Self.response(status: 200))
        }

        let report = try await NetworkManager.shared.sendRemoteTestPush()

        XCTAssertTrue(report.configured)
        XCTAssertEqual(report.results.first?.status, 202)
        XCTAssertEqual(report.results.first?.reason, "queued")
        XCTAssertEqual(report.note, "queued")
    }

    func testSendRemoteTestPushUsesDeviceScopedFallbackWhenEnvironmentChanged() async throws {
        KeychainHelper.write(key: "apns_device_token", value: String(repeating: "a", count: 64))
        KeychainHelper.write(
            key: "apns_device_environment",
            value: NetworkManager.shared.apnsEnvironment == "sandbox" ? "production" : "sandbox"
        )
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")

        var capturedPath: String?
        var capturedAuthHeader: String?
        var capturedBody: [String: Any]?
        let body = Data(#"{"configured":true,"results":[],"pruned_tokens":0}"#.utf8)
        MockURLProtocol.handler = { req in
            capturedPath = req.url?.path
            capturedAuthHeader = req.value(forHTTPHeaderField: "Authorization")
            if let body = req.httpBody {
                capturedBody = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
            }
            return (body, Self.response(status: 200))
        }

        _ = try await NetworkManager.shared.sendRemoteTestPush()

        XCTAssertEqual(capturedPath, "/api/devices/apns-test-push")
        XCTAssertNil(capturedAuthHeader)
        XCTAssertNil(capturedBody?["token"])
        XCTAssertNotNil(capturedBody?["device_id"] as? String)
        XCTAssertEqual(capturedBody?["environment"] as? String, NetworkManager.shared.apnsEnvironment)
        XCTAssertEqual(capturedBody?["device_secret"] as? String, "device-secret")
    }

    func testUnregisterAPNSDeviceTokenUsesDeviceSecret() async throws {
        let token = String(repeating: "a", count: 64)
        KeychainHelper.write(key: "apns_device_token", value: token)
        KeychainHelper.write(key: "apns_device_environment", value: NetworkManager.shared.apnsEnvironment)
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")
        var capturedMethod: String?
        var capturedPath: String?
        var capturedSecret: String?
        MockURLProtocol.handler = { request in
            capturedMethod = request.httpMethod
            capturedPath = request.url?.path
            capturedSecret = request.value(forHTTPHeaderField: "X-Device-Secret")
            return (Data(), Self.response(status: 204))
        }

        try await NetworkManager.shared.unregisterAPNSDeviceToken()

        XCTAssertEqual(capturedMethod, "DELETE")
        XCTAssertEqual(capturedPath, "/api/devices/apns-token/\(token)")
        XCTAssertEqual(capturedSecret, "device-secret")
        XCTAssertNil(NetworkManager.shared.registeredAPNSDeviceToken)
        XCTAssertNil(NetworkManager.shared.registeredAPNSDeviceEnvironment)
    }

    func testSendRemoteTestPushClearsStoredTokenOnDeviceScopedNotFound() async throws {
        KeychainHelper.write(key: "apns_device_token", value: String(repeating: "a", count: 64))
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")
        MockURLProtocol.handler = { _ in
            (Data(#"{"detail":"APNs device token not registered"}"#.utf8), Self.response(status: 404))
        }

        do {
            _ = try await NetworkManager.shared.sendRemoteTestPush()
            XCTFail("Expected APIClientError not thrown")
        } catch APIClientError.httpStatus(let status, _) {
            XCTAssertEqual(status, 404)
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertNil(NetworkManager.shared.registeredAPNSDeviceToken)
    }

    func testSendRemoteTestPushPreservesBackendErrorDetail() async throws {
        KeychainHelper.write(key: "apns_device_token", value: String(repeating: "a", count: 64))
        KeychainHelper.write(key: "apns_device_environment", value: NetworkManager.shared.apnsEnvironment)
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")
        MockURLProtocol.handler = { _ in
            (
                Data(#"{"detail":"APNs provider quota exceeded"}"#.utf8),
                Self.response(status: 500)
            )
        }

        do {
            _ = try await NetworkManager.shared.sendRemoteTestPush()
            XCTFail("Expected APIClientError not thrown")
        } catch APIClientError.httpStatus(let status, let detail) {
            XCTAssertEqual(status, 500)
            XCTAssertEqual(detail, "APNs provider quota exceeded")
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    func testSendRemoteTestPushFallsBackToDeviceScopedEndpointWithoutStoredToken() async throws {
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")
        var capturedMethod: String?
        var capturedAuthHeader: String?
        var capturedPath: String?
        var capturedBody: [String: Any]?
        let body = Data(#"{"configured":true,"results":[{"token":"abcd1234","environment":"production","host":"https://api.push.apple.com","status":200}],"pruned_tokens":0}"#.utf8)
        MockURLProtocol.handler = { req in
            capturedMethod = req.httpMethod
            capturedAuthHeader = req.value(forHTTPHeaderField: "Authorization")
            capturedPath = req.url?.path
            if let body = req.httpBody {
                capturedBody = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
            }
            return (body, Self.response(status: 200))
        }

        let report = try await NetworkManager.shared.sendRemoteTestPush()

        XCTAssertEqual(capturedMethod, "POST")
        XCTAssertNil(capturedAuthHeader)
        XCTAssertEqual(capturedPath, "/api/devices/apns-test-push")
        XCTAssertNil(capturedBody?["token"])
        XCTAssertNotNil(capturedBody?["device_id"] as? String)
        XCTAssertEqual(capturedBody?["environment"] as? String, NetworkManager.shared.apnsEnvironment)
        XCTAssertEqual(capturedBody?["device_secret"] as? String, "device-secret")
        XCTAssertTrue(report.configured)
        XCTAssertEqual(report.results.first?.status, 200)
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
        let cred = Credential(platform: "youtube", has_bearer_token: true, has_api_key: false, has_api_secret: false, updated_at: nil)
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
        var capturedTimeout: TimeInterval?
        MockURLProtocol.handler = { req in
            capturedMethod = req.httpMethod
            capturedAuthHeader = req.value(forHTTPHeaderField: "Authorization")
            capturedTimeout = req.timeoutInterval
            return (Data(), Self.response(status: 200))
        }
        NetworkManager.shared.setAdminApiToken("admin-secret")
        defer { NetworkManager.shared.setAdminApiToken(nil) }

        do { try await NetworkManager.shared.triggerPoll() }
        catch { XCTFail("Unexpected error: \(error)") }
        XCTAssertEqual(capturedMethod, "POST")
        XCTAssertEqual(capturedAuthHeader, "Bearer admin-secret")
        XCTAssertEqual(capturedTimeout, 90)
    }

    // triggerPoll preserves server HTTP status
    func testTriggerPoll500Throws() async throws {
        MockURLProtocol.handler = { _ in (Data(), Self.response(status: 500)) }

        do {
            try await NetworkManager.shared.triggerPoll()
            XCTFail("Expected APIClientError not thrown")
        } catch APIClientError.httpStatus(let status, _) {
            XCTAssertEqual(status, 500)
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    func testTriggerBackgroundPollUsesDeviceScopedEndpointWhenTokenRegistered() async throws {
        let token = String(repeating: "a", count: 64)
        KeychainHelper.write(key: "apns_device_token", value: token)
        KeychainHelper.write(key: "apns_device_environment", value: NetworkManager.shared.apnsEnvironment)
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")

        var capturedPath: String?
        var capturedAuthHeader: String?
        var capturedTimeout: TimeInterval?
        var capturedBody: [String: Any]?
        MockURLProtocol.handler = { req in
            capturedPath = req.url?.path
            capturedAuthHeader = req.value(forHTTPHeaderField: "Authorization")
            capturedTimeout = req.timeoutInterval
            if let body = req.httpBody {
                capturedBody = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
            }
            return (Data(), Self.response(status: 200))
        }

        try await NetworkManager.shared.triggerBackgroundPoll(timeout: 12)

        XCTAssertEqual(capturedPath, "/api/devices/background-refresh")
        XCTAssertNil(capturedAuthHeader)
        XCTAssertEqual(capturedTimeout, 12)
        XCTAssertEqual(capturedBody?["token"] as? String, token)
        XCTAssertEqual(capturedBody?["device_secret"] as? String, "device-secret")
    }

    func testTriggerBackgroundPollRefreshesRejectedDeviceCredentialAndRetries() async throws {
        let token = String(repeating: "a", count: 64)
        KeychainHelper.write(key: "apns_device_token", value: token)
        KeychainHelper.write(key: "apns_device_environment", value: NetworkManager.shared.apnsEnvironment)
        KeychainHelper.write(key: "apns_device_secret", value: "device-secret")

        var paths: [String] = []
        var backgroundRefreshRequests = 0
        MockURLProtocol.handler = { request in
            let path = request.url?.path ?? ""
            paths.append(path)
            if path == "/api/devices/background-refresh" {
                backgroundRefreshRequests += 1
                return (
                    Data(),
                    Self.response(status: backgroundRefreshRequests == 1 ? 404 : 200)
                )
            }
            if path == "/api/devices/apns-token" {
                return (Data(), Self.response(status: 201))
            }
            XCTFail("Unexpected request path: \(path)")
            return (Data(), Self.response(status: 500))
        }

        try await NetworkManager.shared.triggerBackgroundPoll(timeout: 12)

        XCTAssertEqual(
            paths,
            [
                "/api/devices/background-refresh",
                "/api/devices/apns-token",
                "/api/devices/background-refresh",
            ]
        )
    }

    func testTriggerBackgroundPollDoesNotUseAdminFallbackWhenDeviceEndpointFails() async {
        NetworkManager.shared.setAdminApiToken("admin-secret")
        defer { NetworkManager.shared.setAdminApiToken(nil) }

        var paths: [String] = []
        var authHeaders: [String?] = []
        MockURLProtocol.handler = { req in
            let path = req.url?.path ?? ""
            paths.append(path)
            authHeaders.append(req.value(forHTTPHeaderField: "Authorization"))
            if path == "/api/devices/background-refresh" {
                return (Data(), Self.response(status: 404))
            }
            return (Data(), Self.response(status: 200))
        }

        do {
            try await NetworkManager.shared.triggerBackgroundPoll(timeout: 12)
            XCTFail("Expected background refresh failure")
        } catch {
            XCTAssertEqual(paths, ["/api/devices/background-refresh"])
            XCTAssertNil(authHeaders.first ?? nil)
        }
    }

    func testTriggerBackgroundPollDoesNotAttemptAdminFallbackWithoutCredential() async {
        NetworkManager.shared.setAdminApiToken(nil)
        var paths: [String] = []
        MockURLProtocol.handler = { req in
            paths.append(req.url?.path ?? "")
            return (Data(), Self.response(status: 500))
        }

        do {
            try await NetworkManager.shared.triggerBackgroundPoll(timeout: 8)
            XCTFail("Expected background refresh failure")
        } catch {
            XCTAssertEqual(paths, ["/api/devices/background-refresh"])
        }
    }

    func testTriggerBackgroundPollPreservesBackendErrorDetail() async {
        NetworkManager.shared.setAdminApiToken(nil)
        MockURLProtocol.handler = { _ in
            (
                Data("{\"detail\":\"database quota exceeded while starting poll\"}".utf8),
                Self.response(status: 500)
            )
        }

        do {
            try await NetworkManager.shared.triggerBackgroundPoll(timeout: 8)
            XCTFail("Expected background refresh failure")
        } catch APIClientError.httpStatus(let status, let detail) {
            XCTAssertEqual(status, 500)
            XCTAssertEqual(detail, "database quota exceeded while starting poll")
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    // Japanese text is returned unchanged without a network call.
    func testTranslateToJapaneseSkipsForJapanese() async throws {
        // Make no handler — any network call will fail with .unknown
        let result = await NetworkManager.shared.translateToJapanese("こんにちは")
        XCTAssertEqual(result, "こんにちは")
    }

    // Non-Japanese text makes a network call; valid response is parsed correctly.
    func testTranslateToJapaneseReturnsParsedResult() async throws {
        // Google Translate response: [[["こんにちは","hello",null,null,10]], ...]
        let gtResponse = Data(#"[[["こんにちは","hello",null,null,10]],null,"en"]"#.utf8)
        MockURLProtocol.handler = { _ in (gtResponse, Self.response(status: 200)) }

        let result = await NetworkManager.shared.translateToJapanese("hello")
        XCTAssertEqual(result, "こんにちは")
    }

    // Multi-chunk response is concatenated correctly.
    func testTranslateToJapaneseJoinsContinuationChunks() async throws {
        let gtResponse = Data(#"[[["ありがとう","thank",null,null,10],["ございます"," you",null,null,10]],null,"en"]"#.utf8)
        MockURLProtocol.handler = { _ in (gtResponse, Self.response(status: 200)) }

        let result = await NetworkManager.shared.translateToJapanese("thank you")
        XCTAssertEqual(result, "ありがとうございます")
    }

    // Network failure falls back to the original text without throwing.
    func testTranslateToJapaneseFallsBackOnNetworkError() async throws {
        MockURLProtocol.errorHandler = { _ in URLError(.notConnectedToInternet) }

        let result = await NetworkManager.shared.translateToJapanese("hello")
        XCTAssertEqual(result, "hello")
    }

    // Malformed JSON falls back to the original text without throwing.
    func testTranslateToJapaneseFallsBackOnBadJSON() async throws {
        MockURLProtocol.handler = { _ in (Data("not json".utf8), Self.response(status: 200)) }

        let result = await NetworkManager.shared.translateToJapanese("hello")
        XCTAssertEqual(result, "hello")
    }

    // MARK: - scrapeRSSFallback
    func testScrapeRSSFallbackNHKFiltersByKeyword() async {
        // Return an RSS with one matching item and one non-matching item from the NHK URL,
        // and empty RSS from GNews, so we can isolate NHK filtering.
        let nhkXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Aiko releases new album</title>
              <link>https://nhk.or.jp/aiko-album</link>
              <description>Singer Aiko announced a new release.</description>
            </item>
            <item>
              <title>Unrelated sports result</title>
              <link>https://nhk.or.jp/sports</link>
              <description>The match ended 2-1.</description>
            </item>
          </channel>
        </rss>
        """
        let emptyXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel></channel></rss>
        """
        MockURLProtocol.handler = { request in
            let xml = request.url?.host?.contains("nhk") == true ? nhkXml : emptyXml
            return (Data(xml.utf8), Self.response(status: 200))
        }

        let items = await NetworkManager.shared.scrapeRSSFallback(keyword: "Aiko")
        let titles = items.map { $0.title ?? "" }
        XCTAssertTrue(titles.contains(where: { $0.contains("Aiko") }),
                      "NHK items matching the keyword should appear in results")
        XCTAssertFalse(titles.contains(where: { $0.contains("Unrelated") }),
                       "NHK items not matching the keyword should be filtered out")
    }

    func testScrapeRSSFallbackNHKUsesStableIds() async {
        // IDs must be deterministic (derived from link URL) so the same article
        // doesn't accumulate as a new entry on each refresh.
        let xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Aiko live event recap</title>
              <link>https://nhk.or.jp/aiko-live-recap</link>
              <description>Aiko performed all her hits.</description>
            </item>
          </channel>
        </rss>
        """
        MockURLProtocol.handler = { _ in (Data(xml.utf8), Self.response(status: 200)) }

        let first = await NetworkManager.shared.scrapeRSSFallback(keyword: "Aiko")
        let second = await NetworkManager.shared.scrapeRSSFallback(keyword: "Aiko")

        XCTAssertFalse(first.isEmpty)
        XCTAssertEqual(first.first?.id, second.first?.id,
                       "NHK scraper IDs must be stable across refreshes to prevent duplicates")
        XCTAssertFalse(first.first?.id.contains("-") == true, "ID should not contain UUID hyphens")
    }

    func testScrapeRSSFallbackGNewsUsesStableIds() async {
        let emptyXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel></channel></rss>
        """
        let gnewsXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Aiko new single review</title>
              <link>https://news.google.com/rss/articles/gnews-stable-link-abc123</link>
              <description>Review of Aiko's latest single.</description>
            </item>
          </channel>
        </rss>
        """
        MockURLProtocol.handler = { request in
            let isGNews = request.url?.host?.contains("google.com") == true
            return (Data((isGNews ? gnewsXml : emptyXml).utf8), Self.response(status: 200))
        }

        let first = await NetworkManager.shared.scrapeRSSFallback(keyword: "Aiko")
        let second = await NetworkManager.shared.scrapeRSSFallback(keyword: "Aiko")

        XCTAssertFalse(first.isEmpty)
        XCTAssertEqual(first.first?.id, second.first?.id,
                       "GNews scraper IDs must be stable across refreshes to prevent duplicates")
        XCTAssertFalse(first.first?.id.contains("-") == true, "ID should not contain UUID hyphens")
    }

    func testScrapeRSSFallbackGNewsRejectsSummaryOnlyMatch() async {
        let emptyXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel></channel></rss>
        """
        let gnewsXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>杉野遥亮、『世にも奇妙な物語』で初主演</title>
              <link>https://news.google.com/rss/articles/summary-only</link>
              <description>吉沢亮の関連記事も紹介</description>
            </item>
          </channel>
        </rss>
        """
        MockURLProtocol.handler = { request in
            let isGNews = request.url?.host?.contains("google.com") == true
            return (Data((isGNews ? gnewsXml : emptyXml).utf8), Self.response(status: 200))
        }

        let items = await NetworkManager.shared.scrapeRSSFallback(keyword: "吉沢亮")
        XCTAssertTrue(items.isEmpty)
    }

    func testScrapeGoogleNewsSiteRejectsSummaryOnlyMatch() async {
        let xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>杉野遥亮、『世にも奇妙な物語』で初主演 - Real Sound</title>
              <link>https://news.google.com/rss/articles/realsound-summary-only</link>
              <description>吉沢亮の関連記事も紹介</description>
            </item>
          </channel>
        </rss>
        """
        MockURLProtocol.handler = { _ in (Data(xml.utf8), Self.response(status: 200)) }

        let items = await NetworkManager.shared.scrapeGoogleNewsSite(
            keyword: "吉沢亮",
            site: "realsound.jp",
            platform: "realsound"
        )
        XCTAssertTrue(items.isEmpty)
    }

    func testScrapeGoogleNewsSiteCompletionRequiresHistoricalQueryWhenInitialHasNoMatch() async {
        let xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>杉野遥亮、『世にも奇妙な物語』で初主演 - Real Sound</title>
              <link>https://news.google.com/rss/articles/realsound-summary-only</link>
              <description>吉沢亮の関連記事も紹介</description>
            </item>
          </channel>
        </rss>
        """
        MockURLProtocol.errorHandler = { request in
            let decodedURL = request.url?.absoluteString.removingPercentEncoding ?? ""
            return decodedURL.contains("when:10y") ? URLError(.notConnectedToInternet) : nil
        }
        MockURLProtocol.handler = { _ in (Data(xml.utf8), Self.response(status: 200)) }

        let result = await NetworkManager.shared.scrapeGoogleNewsSiteWithCompletion(
            keyword: "吉沢亮",
            site: "realsound.jp",
            platform: "realsound"
        )

        XCTAssertTrue(result.items.isEmpty)
        XCTAssertTrue(
            result.completedPlatforms.isEmpty,
            "A failed historical query must not mark the site fallback as complete."
        )
    }

    func testScrapeGoogleNewsSiteCompletionMarksPlatformWhenHistoricalQuerySucceeds() async {
        let initialXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>杉野遥亮、『世にも奇妙な物語』で初主演 - Real Sound</title>
              <link>https://news.google.com/rss/articles/realsound-summary-only</link>
              <description>吉沢亮の関連記事も紹介</description>
            </item>
          </channel>
        </rss>
        """
        let historicalXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel></channel></rss>
        """
        MockURLProtocol.handler = { request in
            let decodedURL = request.url?.absoluteString.removingPercentEncoding ?? ""
            let xml = decodedURL.contains("when:10y") ? historicalXml : initialXml
            return (Data(xml.utf8), Self.response(status: 200))
        }

        let result = await NetworkManager.shared.scrapeGoogleNewsSiteWithCompletion(
            keyword: "吉沢亮",
            site: "realsound.jp",
            platform: "realsound"
        )

        XCTAssertTrue(result.items.isEmpty)
        XCTAssertEqual(result.completedPlatforms, ["realsound"])
    }

    func testScrapeLocalFallbackUsesSourceSpecificYouTubeSearch() async {
        let body = """
        {
          "contents": {
            "sectionListRenderer": {
              "contents": [
                {
                  "itemSectionRenderer": {
                    "contents": [
                      {
                        "videoRenderer": {
                          "videoId": "abc123def45",
                          "title": { "runs": [{ "text": "Aiko live clip" }] },
                          "ownerText": { "runs": [{ "text": "Aiko Channel" }] },
                          "publishedTimeText": { "simpleText": "2 days ago" },
                          "thumbnail": { "thumbnails": [{ "url": "https://img.example/youtube.jpg" }] }
                        }
                      }
                    ]
                  }
                }
              ]
            }
          }
        }
        """
        MockURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.host, "www.youtube.com")
            XCTAssertEqual(request.httpMethod, "POST")
            return (Data(body.utf8), Self.response(status: 200))
        }

        let result = await NetworkManager.shared.scrapeLocalFallbacksWithCompletion(
            keyword: "Aiko",
            platformIds: ["youtube"]
        )

        XCTAssertEqual(result.completedPlatforms, ["youtube"])
        XCTAssertEqual(result.items.first?.platform, "youtube")
        XCTAssertEqual(result.items.first?.media_type, "video")
        XCTAssertEqual(result.items.first?.url, "https://www.youtube.com/watch?v=abc123def45")
    }

    func testYouTubeFallbackTriesSearchPageWhenInnertubeIsEmpty() async {
        let requestQueue = DispatchQueue(label: "test.youtube.requests")
        var requestedMethods = [String]()
        let emptyInnertube = #"{"contents":{}}"#
        let html = """
        <html><script>
        ytInitialData = {
          "contents": {
            "sectionListRenderer": {
              "contents": [
                {
                  "itemSectionRenderer": {
                    "contents": [
                      {
                        "videoRenderer": {
                          "videoId": "xyz987uvw65",
                          "title": { "runs": [{ "text": "Aiko live clip" }] },
                          "publishedTimeText": { "simpleText": "1 day ago" },
                          "ownerText": { "runs": [{ "text": "Aiko Official" }] }
                        }
                      }
                    ]
                  }
                }
              ]
            }
          }
        };
        </script></html>
        """
        MockURLProtocol.handler = { request in
            requestQueue.sync {
                requestedMethods.append("\(request.httpMethod ?? "") \(request.url?.path ?? "")")
            }
            if request.httpMethod == "POST" {
                return (Data(emptyInnertube.utf8), Self.response(status: 200))
            }
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/results")
            return (Data(html.utf8), Self.response(status: 200))
        }

        let result = await NetworkManager.shared.scrapeLocalFallbacksWithCompletion(
            keyword: "Aiko",
            platformIds: ["youtube"]
        )

        let methods = requestQueue.sync { requestedMethods }
        XCTAssertEqual(methods, ["POST /youtubei/v1/search", "GET /results"])
        XCTAssertEqual(result.completedPlatforms, ["youtube"])
        XCTAssertEqual(result.items.map(\.id), ["youtube:xyz987uvw65"])
        XCTAssertEqual(result.items.first?.url, "https://www.youtube.com/watch?v=xyz987uvw65")
        XCTAssertEqual(result.items.first?.title, "Aiko live clip")
    }

    func testYouTubeSearchPageIgnoresUnstructuredVideoIds() async {
        let emptyInnertube = #"{"contents":{}}"#
        let html = #"<html><script>var data={"videoId":"xyz987uvw65"};</script></html>"#
        MockURLProtocol.handler = { request in
            if request.httpMethod == "POST" {
                return (Data(emptyInnertube.utf8), Self.response(status: 200))
            }
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/results")
            return (Data(html.utf8), Self.response(status: 200))
        }

        let result = await NetworkManager.shared.scrapeLocalFallbacksWithCompletion(
            keyword: "Aiko",
            platformIds: ["youtube"]
        )

        XCTAssertEqual(result.completedPlatforms, ["youtube"])
        XCTAssertTrue(result.items.isEmpty)
    }

    func testYouTubeSearchPageKeepsAmpersandInsideKeywordQueryValue() async {
        let requestQueue = DispatchQueue(label: "test.youtube.query")
        var capturedSearchQuery: String?
        MockURLProtocol.handler = { request in
            if request.httpMethod == "POST" {
                return (Data(#"{"contents":{}}"#.utf8), Self.response(status: 200))
            }
            requestQueue.sync {
                capturedSearchQuery = URLComponents(
                    url: request.url!,
                    resolvingAgainstBaseURL: false
                )?.queryItems?.first(where: { $0.name == "search_query" })?.value
            }
            return (Data("<html></html>".utf8), Self.response(status: 200))
        }

        _ = await NetworkManager.shared.scrapeLocalFallbacksWithCompletion(
            keyword: "A&B",
            platformIds: ["youtube"]
        )

        XCTAssertEqual(requestQueue.sync { capturedSearchQuery }, "A&B")
    }

    func testScrapeLocalFallbackUsesNoteHashtagRSS() async {
        let xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Aiko essay</title>
              <link>https://note.com/aiko/n/note123</link>
              <description>Aiko wrote a note.</description>
              <pubDate>Wed, 22 Jul 2026 01:00:00 +0000</pubDate>
            </item>
          </channel>
        </rss>
        """
        MockURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.host, "note.com")
            XCTAssertTrue(request.url?.path.contains("/hashtag/Aiko/rss") == true)
            return (Data(xml.utf8), Self.response(status: 200))
        }

        let result = await NetworkManager.shared.scrapeLocalFallbacksWithCompletion(
            keyword: "Aiko",
            tagKeyword: "Aiko",
            platformIds: ["note"]
        )

        XCTAssertEqual(result.completedPlatforms, ["note"])
        XCTAssertEqual(result.items.first?.id, "note:note123")
        XCTAssertEqual(result.items.first?.platform, "note")
        XCTAssertEqual(result.items.first?.media_type, "article")
        XCTAssertEqual(result.items.first?.watch_term_keyword, "Aiko")
    }

    func testNoteHashtagRSSPercentEncodesSlashInKeyword() async {
        let xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel></channel></rss>
        """
        MockURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.host, "note.com")
            XCTAssertTrue(request.url?.absoluteString.contains("/hashtag/A%2FB/rss") == true)
            return (Data(xml.utf8), Self.response(status: 200))
        }

        let result = await NetworkManager.shared.scrapeLocalFallbacksWithCompletion(
            keyword: "A/B",
            tagKeyword: "A/B",
            platformIds: ["note"]
        )

        XCTAssertEqual(result.completedPlatforms, ["note"])
        XCTAssertTrue(result.items.isEmpty)
    }

    func testScrapeLocalFallbackDefaultSkipsGenericGoogleNewsForSourceSpecificPlatforms() async {
        let requestQueue = DispatchQueue(label: "test.requestedURLs")
        var requestedURLs = [String]()
        let emptyRSS = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel></channel></rss>
        """
        MockURLProtocol.handler = { request in
            requestQueue.sync {
                requestedURLs.append(request.url?.absoluteString.removingPercentEncoding ?? "")
            }
            if request.httpMethod == "POST" {
                return (Data(#"{"contents":{}}"#.utf8), Self.response(status: 200))
            }
            return (Data(emptyRSS.utf8), Self.response(status: 200))
        }

        _ = await NetworkManager.shared.scrapeLocalFallbacksWithCompletion(keyword: "Aiko")

        let urls = requestQueue.sync { requestedURLs }
        XCTAssertTrue(urls.contains { $0.contains("www.youtube.com/youtubei/v1/search") })
        XCTAssertTrue(urls.contains { $0.contains("note.com/hashtag/Aiko/rss") })
        XCTAssertFalse(urls.contains { $0.contains("site:youtube.com") })
        XCTAssertFalse(urls.contains { $0.contains("site:note.com") })
    }

    func testGoogleNewsFallbackKeepsAmpersandInsideKeywordQueryValue() async {
        let requestQueue = DispatchQueue(label: "test.gnews.query")
        var capturedQuery: String?
        let emptyRSS = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel></channel></rss>
        """
        MockURLProtocol.handler = { request in
            requestQueue.sync {
                capturedQuery = URLComponents(
                    url: request.url!,
                    resolvingAgainstBaseURL: false
                )?.queryItems?.first(where: { $0.name == "q" })?.value
            }
            return (Data(emptyRSS.utf8), Self.response(status: 200))
        }

        _ = await NetworkManager.shared.scrapeLocalFallbacksWithCompletion(
            keyword: "A&B",
            platformIds: ["girlschannel"]
        )

        XCTAssertEqual(requestQueue.sync { capturedQuery }, "A&B site:girlschannel.net when:10y")
    }

    func testScrapeRSSFallbackReturnsEmptyOnNetworkError() async {
        MockURLProtocol.errorHandler = { _ in URLError(.notConnectedToInternet) }

        let items = await NetworkManager.shared.scrapeRSSFallback(keyword: "Aiko")
        XCTAssertTrue(items.isEmpty, "Network error should produce an empty result, not a crash")
    }

    func testScrapeRSSFallbackSetsCorrectPlatform() async {
        let xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Aiko concert announcement</title>
              <link>https://nhk.or.jp/concert</link>
              <description>Aiko will hold a concert in Osaka.</description>
            </item>
          </channel>
        </rss>
        """
        MockURLProtocol.handler = { _ in (Data(xml.utf8), Self.response(status: 200)) }

        let items = await NetworkManager.shared.scrapeRSSFallback(keyword: "Aiko")
        if !items.isEmpty {
            XCTAssertEqual(items.first?.platform, "news")
            XCTAssertEqual(items.first?.media_type, "article")
            XCTAssertEqual(items.first?.watch_term_keyword, "Aiko")
        }
    }

    func testScrapeRSSFallbackCompletionRequiresNHKAndGoogleNews() async {
        let nhkXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Aiko concert announcement</title>
              <link>https://nhk.or.jp/concert</link>
              <description>Aiko will hold a concert in Osaka.</description>
            </item>
          </channel>
        </rss>
        """
        MockURLProtocol.errorHandler = { request in
            request.url?.host?.contains("google.com") == true
                ? URLError(.notConnectedToInternet)
                : nil
        }
        MockURLProtocol.handler = { _ in (Data(nhkXml.utf8), Self.response(status: 200)) }

        let result = await NetworkManager.shared.scrapeRSSFallbackWithCompletion(keyword: "Aiko")

        XCTAssertFalse(result.items.isEmpty)
        XCTAssertTrue(
            result.completedPlatforms.isEmpty,
            "A partial news fallback scrape must not clear the news refresh marker."
        )
    }

    func testScrapeRSSFallbackCompletionMarksNewsWhenBothFeedsSucceed() async {
        let nhkXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel></channel></rss>
        """
        let gnewsXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Aiko new single review</title>
              <link>https://news.google.com/rss/articles/aiko-review</link>
              <description>Review of Aiko's latest single.</description>
            </item>
          </channel>
        </rss>
        """
        MockURLProtocol.handler = { request in
            let isGoogleNews = request.url?.host?.contains("google.com") == true
            return (Data((isGoogleNews ? gnewsXml : nhkXml).utf8), Self.response(status: 200))
        }

        let result = await NetworkManager.shared.scrapeRSSFallbackWithCompletion(keyword: "Aiko")

        XCTAssertEqual(result.completedPlatforms, ["news"])
        XCTAssertFalse(result.items.isEmpty)
    }

    func testScrapeCustomUrlsEmptyInputReturnsEmpty() async {
        let items = await NetworkManager.shared.scrapeCustomUrls([])
        XCTAssertTrue(items.isEmpty, "Empty URL list should produce no items")
    }
}

// MARK: - RSSParserDelegate Tests

final class RSSParserDelegateTests: XCTestCase {
    private func parse(_ xml: String) -> [RssItem] {
        let delegate = RSSParserDelegate()
        let parser = XMLParser(data: Data(xml.utf8))
        parser.delegate = delegate
        parser.parse()
        return delegate.items
    }

    func testParsesBasicRssItem() {
        let xml = """
        <?xml version="1.0"?><rss version="2.0"><channel>
        <item>
            <title>Hello World</title>
            <link>https://example.com/hello</link>
            <description>A test article</description>
            <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
        </item>
        </channel></rss>
        """
        let items = parse(xml)
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items[0].title, "Hello World")
        XCTAssertEqual(items[0].link, "https://example.com/hello")
        XCTAssertEqual(items[0].description, "A test article")
        XCTAssertNotNil(items[0].pubDate)
    }

    func testParsesMediaThumbnailAttribute() {
        let xml = """
        <?xml version="1.0"?><rss version="2.0"><channel>
        <item>
            <title>Media Item</title>
            <link>https://example.com/media</link>
            <media:thumbnail url="https://example.com/thumb.jpg"/>
        </item>
        </channel></rss>
        """
        let items = parse(xml)
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items[0].thumbnailUrl, "https://example.com/thumb.jpg")
    }

    func testParsesEnclosureImageAttribute() {
        let xml = """
        <?xml version="1.0"?><rss version="2.0"><channel>
        <item>
            <title>Enclosure Item</title>
            <link>https://example.com/enc</link>
            <enclosure url="https://example.com/img.png" type="image/png"/>
        </item>
        </channel></rss>
        """
        let items = parse(xml)
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items[0].thumbnailUrl, "https://example.com/img.png")
    }

    func testEnclosureNonImageIgnored() {
        let xml = """
        <?xml version="1.0"?><rss version="2.0"><channel>
        <item>
            <title>Audio Item</title>
            <link>https://example.com/audio</link>
            <enclosure url="https://example.com/audio.mp3" type="audio/mpeg"/>
        </item>
        </channel></rss>
        """
        let items = parse(xml)
        XCTAssertEqual(items.count, 1)
        XCTAssertNil(items[0].thumbnailUrl)
    }

    func testParsesAtomEntryElement() {
        let xml = """
        <?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry>
            <title>Atom Entry</title>
            <link href="https://example.com/atom"/>
            <summary>Atom summary</summary>
            <published>2024-06-01T10:00:00Z</published>
        </entry>
        </feed>
        """
        let items = parse(xml)
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items[0].title, "Atom Entry")
    }

    func testParsesMultipleItems() {
        let xml = """
        <?xml version="1.0"?><rss version="2.0"><channel>
        <item><title>A</title><link>https://a.com</link></item>
        <item><title>B</title><link>https://b.com</link></item>
        <item><title>C</title><link>https://c.com</link></item>
        </channel></rss>
        """
        let items = parse(xml)
        XCTAssertEqual(items.count, 3)
        XCTAssertEqual(items.map { $0.title }, ["A", "B", "C"])
    }

    func testEmptyFeedReturnsNoItems() {
        let xml = #"<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>"#
        XCTAssertTrue(parse(xml).isEmpty)
    }
}

// MARK: - KeychainHelper Tests (Phase 5.4)

final class KeychainHelperTests: XCTestCase {
    private let testKey = "com.otterpia.oshireader.test.\(UUID().uuidString)"

    override func tearDownWithError() throws {
        KeychainHelper.delete(key: testKey)
        try super.tearDownWithError()
    }

    func testWriteAndReadRoundTrip() {
        KeychainHelper.write(key: testKey, value: "hello keychain")
        XCTAssertEqual(KeychainHelper.read(key: testKey), "hello keychain")
    }

    func testReadReturnsNilForMissingKey() {
        XCTAssertNil(KeychainHelper.read(key: testKey))
    }

    func testDeleteRemovesItem() {
        KeychainHelper.write(key: testKey, value: "to-be-deleted")
        KeychainHelper.delete(key: testKey)
        XCTAssertNil(KeychainHelper.read(key: testKey))
    }

    func testOverwriteReplacesValue() {
        KeychainHelper.write(key: testKey, value: "first")
        KeychainHelper.write(key: testKey, value: "second")
        XCTAssertEqual(KeychainHelper.read(key: testKey), "second")
    }

    func testWritesUnicodeCorrectly() {
        let value = "🎤 アイコ 💙"
        KeychainHelper.write(key: testKey, value: value)
        XCTAssertEqual(KeychainHelper.read(key: testKey), value)
    }

    func testWriteReturnsTrueOnSuccess() {
        XCTAssertTrue(KeychainHelper.write(key: testKey, value: "check-return"), "write should return true when Keychain accepts the item")
    }
}

// MARK: - adminApiToken Migration Tests (Phase 5.5)

final class AdminApiTokenTests: XCTestCase {
    private let keychainKey = "admin_api_token"
    private let udKey = "admin_api_token"

    override func setUp() {
        super.setUp()
        KeychainHelper.delete(key: keychainKey)
        UserDefaults.standard.removeObject(forKey: udKey)
    }

    override func tearDown() {
        KeychainHelper.delete(key: keychainKey)
        UserDefaults.standard.removeObject(forKey: udKey)
        super.tearDown()
    }

    func testMigratesLegacyUserDefaultsToKeychain() {
        UserDefaults.standard.set("legacy-token", forKey: udKey)
        let token = NetworkManager.shared.adminApiToken
        XCTAssertEqual(token, "legacy-token")
        XCTAssertNil(UserDefaults.standard.string(forKey: udKey), "UserDefaults entry should be removed after migration")
        XCTAssertEqual(KeychainHelper.read(key: keychainKey), "legacy-token", "Token should now live in Keychain")
    }

    func testReadsFromKeychain() {
        KeychainHelper.write(key: keychainKey, value: "keychain-token")
        XCTAssertEqual(NetworkManager.shared.adminApiToken, "keychain-token")
    }

    func testReturnsNilWhenNoTokenStored() {
        XCTAssertNil(NetworkManager.shared.adminApiToken)
    }

    func testSetTokenWritesToKeychain() {
        NetworkManager.shared.setAdminApiToken("new-token")
        XCTAssertEqual(KeychainHelper.read(key: keychainKey), "new-token")
    }

    func testSetTokenNilDeletesFromKeychain() {
        KeychainHelper.write(key: keychainKey, value: "to-delete")
        NetworkManager.shared.setAdminApiToken(nil)
        XCTAssertNil(KeychainHelper.read(key: keychainKey))
    }

    func testSetBlankTokenDeletesFromKeychain() {
        KeychainHelper.write(key: keychainKey, value: "to-delete")
        NetworkManager.shared.setAdminApiToken("   ")
        XCTAssertNil(KeychainHelper.read(key: keychainKey))
    }
}

// MARK: - MockURLProtocol

private final class MockURLProtocol: URLProtocol {
    static var handler: ((URLRequest) -> (Data, HTTPURLResponse))?
    // Set this to have the protocol fail with a specific error instead of calling handler.
    static var errorHandler: ((URLRequest) -> Error?)?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        if let errHandler = Self.errorHandler, let error = errHandler(request) {
            client?.urlProtocol(self, didFailWithError: error)
            return
        }
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.unknown))
            return
        }
        // URLSession moves httpBody to httpBodyStream; drain it so handlers can read the body.
        var req = request
        if req.httpBody == nil, let stream = req.httpBodyStream {
            var bodyData = Data()
            stream.open()
            var buf = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let n = stream.read(&buf, maxLength: buf.count)
                if n > 0 { bodyData.append(buf, count: n) }
            }
            stream.close()
            req.httpBody = bodyData
        }
        let (data, response) = handler(req)
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
    private(set) var categories: Set<UNNotificationCategory> = []

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

    func setNotificationCategories(_ categories: Set<UNNotificationCategory>) {
        self.categories = categories
    }
}

private final class MockBackgroundTaskScheduler: BackgroundTaskSchedulerClient {
    private(set) var registeredIdentifier: String?
    private(set) var cancelledIdentifiers: [String] = []
    private(set) var submittedRequests: [BGTaskRequest] = []
    private var launchHandler: ((BGTask) -> Void)?

    func register(
        forTaskWithIdentifier identifier: String,
        using queue: DispatchQueue?,
        launchHandler: @escaping (BGTask) -> Void
    ) -> Bool {
        registeredIdentifier = identifier
        self.launchHandler = launchHandler
        return true
    }

    func cancel(taskRequestWithIdentifier identifier: String) {
        cancelledIdentifiers.append(identifier)
    }

    func submit(_ taskRequest: BGTaskRequest) throws {
        submittedRequests.append(taskRequest)
    }
}

// MARK: - Composition (wallpaper) rendering
final class CompositionRendererTests: XCTestCase {
    private func solidImage(_ color: UIColor, size: CGFloat = 50) -> UIImage {
        UIGraphicsImageRenderer(size: CGSize(width: size, height: size)).image { ctx in
            color.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: size, height: size))
        }
    }

    // Returns true if any pixel in the image is non-transparent.
    private func hasVisiblePixels(_ image: UIImage) -> Bool {
        guard let cg = image.cgImage else { return false }
        let w = cg.width, h = cg.height
        guard w > 0, h > 0 else { return false }
        var px = [UInt8](repeating: 0, count: w * h * 4)
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8,
                                  bytesPerRow: w * 4, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        for i in stride(from: 3, to: px.count, by: 4) where px[i] > 0 { return true }
        return false
    }

    @MainActor
    func testRendersNonEmptyImageFromLayers() throws {
        let red = solidImage(.red)
        let layer = AvatarLayer(imageUrl: "sticker://red", x: 100, y: 100, scale: 1.0, zIndex: 0)
        let image = try XCTUnwrap(
            CompositionRenderer.renderImage(
                layers: [layer], images: ["sticker://red": red], baseSize: 90, canvas: 300, scale: 2
            )
        )
        XCTAssertEqual(image.size.width, 300, accuracy: 1, "logical canvas size")
        XCTAssertEqual(image.size.height, 300, accuracy: 1)
        XCTAssertTrue(hasVisiblePixels(image), "the rendered sticker should produce visible pixels")
    }

    @MainActor
    func testReturnsNilWhenNoImagesLoaded() {
        let layer = AvatarLayer(imageUrl: "sticker://missing", x: 0, y: 0, scale: 1.0, zIndex: 0)
        XCTAssertNil(CompositionRenderer.renderImage(layers: [layer], images: [:], baseSize: 90))
    }
}
