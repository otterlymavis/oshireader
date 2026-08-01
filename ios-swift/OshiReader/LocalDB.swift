import Foundation
import Combine

enum FeedURLPolicy {
    static func isBackendMatchRedirectURL(_ value: String) -> Bool {
        guard let url = URL(string: value) else { return false }
        let parts = url.path.split(separator: "/").map(String.init)
        guard parts.count == 5 else { return false }
        guard parts[0] == "api",
              parts[1] == "feed",
              parts[2] == "matches",
              parts[4] == "redirect" else {
            return false
        }
        return Int(parts[3]) != nil
    }
}

enum FeedItemPolicy {
    static func isLegacyYouTubeGoogleNewsFallback(_ item: FeedItem) -> Bool {
        guard Platform.normalize(item.platform) == "youtube" else { return false }
        if item.source?.lowercased() == "google_news" { return true }
        if item.id.contains(":gnews:") { return true }
        guard let host = URL(string: item.url)?.host?.lowercased() else { return false }
        return host == "news.google.com" || host.hasSuffix(".news.google.com")
    }

    static func isLegacyUnmarkedYouTubeEstimate(_ item: FeedItem) -> Bool {
        guard Platform.normalize(item.platform) == "youtube" else { return false }
        guard item.source?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != false else {
            return false
        }
        return true
    }

    static func shouldPruneLegacyYouTubeItem(_ item: FeedItem) -> Bool {
        isLegacyYouTubeGoogleNewsFallback(item) || isLegacyUnmarkedYouTubeEstimate(item)
    }

    static func isSearchFallback(_ item: FeedItem) -> Bool {
        item.id.contains("search:") || item.title?.lowercased().contains("search:") == true
    }

    static func isMergeEligibleIncomingItem(
        _ item: FeedItem,
        hiddenItems: Set<String>,
        itemKey: (FeedItem) -> String
    ) -> Bool {
        let key = itemKey(item)
        return !hiddenItems.contains(key) &&
            !isSearchFallback(item) &&
            !shouldPruneLegacyYouTubeItem(item)
    }
}

@MainActor
class LocalDB: ObservableObject {
    private final class PendingFeedItemsSave: @unchecked Sendable {
        private let lock = NSLock()
        private var cancelled = false
        private var completed = false

        func cancel() {
            lock.lock()
            cancelled = true
            lock.unlock()
        }

        func complete() {
            lock.lock()
            completed = true
            lock.unlock()
        }

        var isCancelled: Bool {
            lock.lock()
            let value = cancelled
            lock.unlock()
            return value
        }

        var isPending: Bool {
            lock.lock()
            let value = !cancelled && !completed
            lock.unlock()
            return value
        }
    }

    static let shared = LocalDB(directory: FileManager.default
        .urls(for: .documentDirectory, in: .userDomainMask)[0])

    // Published states for views
    @Published var terms: [WatchTerm] = []
    @Published var feedItems: [FeedItem] = []
    @Published var savedPages: [SavedPage] = []
    @Published var customUrls: [CustomUrl] = []
    @Published var subscribedPlatforms: [String] = []
    @Published var wallpaper: String? = nil
    @Published var sourcesOrder: [String]? = nil
    @Published var oshiAvatars: [String: String] = [:]
    @Published var compositions: [String: [AvatarLayer]] = [:]
    @Published var hiddenItems: Set<String> = []

    private let storeDirectory: URL
    private let queue = DispatchQueue(label: "com.otterlymavis.oshireader.db", qos: .userInitiated)
    private let decoder = JSONDecoder()
    private let iso8601 = ISO8601DateFormatter()
    private let maxContentCacheFiles = 120
    private let maxContentCacheBytes = 1_000_000
    private let maxFeedItems = 600
    private let minFeedItemsPerSubscribedPlatform = 8
    private let minFeedItemsPerDiscussionPlatform = 25
    private let discussionActivityPlatforms: Set<String> = ["5ch", "girlschannel", "togetter"]
    private let termDeleteTombstonesFileName = "term_delete_tombstones"
    private let termDeleteTombstoneLifetime: TimeInterval = 10 * 60
    private let feedItemsSaveCoalescingDelay: DispatchTimeInterval
    private var termDeleteTombstones: [String: Date] = [:]
    private var pendingFeedItemsSave: PendingFeedItemsSave?

    // Bump this whenever a migration step is added below.
    private static let currentSchemaVersion = 5
    private static let schemaVersionKey = "localdb_schema_version"

    init(directory: URL, feedItemsSaveCoalescingDelay: DispatchTimeInterval = .milliseconds(250)) {
        self.storeDirectory = directory
        self.feedItemsSaveCoalescingDelay = feedItemsSaveCoalescingDelay
        let loadChangedFeedScope = loadAll()
        let migrationsChangedFeedScope = runMigrationsIfNeeded()
        logStartupCacheDiagnostics()
        let repairChangedFeedScope = repairMissingTermsFromCachedFeed()
        if loadChangedFeedScope || migrationsChangedFeedScope || repairChangedFeedScope {
            BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        }
        pruneContentCache()
    }

    // MARK: - File Paths
    private func fileURL(for name: String) -> URL {
        storeDirectory.appendingPathComponent("\(name).json")
    }

    // MARK: - Load and Save Helpers
    @discardableResult
    private func loadAll() -> Bool {
        self.terms = loadFromFile(name: "terms", defaultValue: [])
        self.feedItems = loadFromFile(name: "feed_items", defaultValue: [])
        self.savedPages = loadFromFile(name: "saved_pages", defaultValue: [])
        self.customUrls = loadFromFile(name: "custom_urls", defaultValue: [])
        let defaultPlatforms = Platform.all.filter(\.subscribedByDefault).map(\.id)
        self.subscribedPlatforms = loadFromFile(name: "subscribed_platforms", defaultValue: defaultPlatforms)
        self.wallpaper = UserDefaults.standard.string(forKey: "wallpaper_url")
        self.sourcesOrder = UserDefaults.standard.stringArray(forKey: "sources_order")
        self.oshiAvatars = loadFromFile(name: "oshi_avatars", defaultValue: [:])
        self.compositions = loadFromFile(name: "oshi_compositions", defaultValue: [:])
        let hiddenArray: [String] = loadFromFile(name: "hidden_items", defaultValue: [])
        self.hiddenItems = Set(hiddenArray)
        self.termDeleteTombstones = loadFromFile(name: termDeleteTombstonesFileName, defaultValue: [:])
        pruneTermDeleteTombstones()
        let prunedLegacyYouTubeFallbacks = pruneLegacyYouTubeItems()
        return applyPersistedTermDeletes() || prunedLegacyYouTubeFallbacks
    }

    // MARK: - Schema Migrations
    @discardableResult
    private func runMigrationsIfNeeded() -> Bool {
        let stored = UserDefaults.standard.integer(forKey: Self.schemaVersionKey)
        guard stored < Self.currentSchemaVersion else { return false }
        var changedFeedScope = false
        for version in (stored + 1)...Self.currentSchemaVersion {
            changedFeedScope = migrate(to: version) || changedFeedScope
        }
        UserDefaults.standard.set(Self.currentSchemaVersion, forKey: Self.schemaVersionKey)
        AppLogger.persistence.info("Schema migrated from v\(stored) → v\(Self.currentSchemaVersion)")
        return changedFeedScope
    }

    @discardableResult
    private func migrate(to version: Int) -> Bool {
        switch version {
        case 1:
            return addMissingDefaultPlatforms()
        case 2:
            return pruneIrrelevantCachedArticleItems()
        case 3:
            // Add only the newly introduced source. Re-running the full v1 merge
            // would re-enable older sources that the user intentionally disabled.
            if !subscribedPlatforms.contains("twitter") {
                subscribedPlatforms.append("twitter")
                saveToFile(name: "subscribed_platforms", value: subscribedPlatforms)
                return true
            }
            return false
        case 4:
            // Add only the newly introduced source. Re-running the full v1 merge
            // would re-enable older sources that the user intentionally disabled.
            if !subscribedPlatforms.contains("thetv") {
                subscribedPlatforms.append("thetv")
                saveToFile(name: "subscribed_platforms", value: subscribedPlatforms)
                return true
            }
            return false
        case 5:
            return addMissingNewDefaultPlatforms([
                "allkpop",
                "billboardjapan",
                "kpopofficial",
                "natalie",
                "soompi",
            ])
        default:
            AppLogger.persistence.warning("No migration handler for schema v\(version)")
            return false
        }
    }

    @discardableResult
    private func addMissingNewDefaultPlatforms(_ platformIds: [String]) -> Bool {
        let missing = platformIds.filter { !subscribedPlatforms.contains($0) }
        if !missing.isEmpty {
            subscribedPlatforms.append(contentsOf: missing)
            saveToFile(name: "subscribed_platforms", value: subscribedPlatforms)
            return true
        }
        return false
    }

    @discardableResult
    private func addMissingDefaultPlatforms() -> Bool {
        let allIds = Set(Platform.all.filter(\.subscribedByDefault).map(\.id))
        let missing = allIds.subtracting(Set(subscribedPlatforms))
        if !missing.isEmpty {
            subscribedPlatforms.append(contentsOf: missing.sorted())
            saveToFile(name: "subscribed_platforms", value: subscribedPlatforms)
            return true
        }
        return false
    }

    @discardableResult
    private func pruneIrrelevantCachedArticleItems() -> Bool {
        let termsByKeyword = Dictionary(uniqueKeysWithValues: terms.map { ($0.keyword, $0) })
        let originalCount = self.feedItems.count
        self.feedItems.removeAll { item in
            guard Platform.forRawValue(item.platform)?.usesStrictKeywordMatching == true,
                  !item.watch_term_keyword.isEmpty else {
                return false
            }
            guard let term = termsByKeyword[item.watch_term_keyword] else {
                return true
            }
            let candidates = [term.keyword] + term.aliases
            return !candidates.contains(where: { self.matchesKeyword(item: item, kw: $0) })
        }
        if self.feedItems.count != originalCount {
            saveFeedItemsImmediately()
            AppLogger.persistence.info(
                "Pruned \(originalCount - self.feedItems.count) irrelevant cached feed items"
            )
            return true
        }
        return false
    }

    @discardableResult
    private func pruneLegacyYouTubeItems() -> Bool {
        let originalCount = self.feedItems.count
        self.feedItems.removeAll { FeedItemPolicy.shouldPruneLegacyYouTubeItem($0) }
        guard self.feedItems.count != originalCount else { return false }
        saveFeedItemsImmediately()
        AppLogger.persistence.info(
            "Pruned \(originalCount - self.feedItems.count) legacy YouTube feed items"
        )
        return true
    }

    @discardableResult
    private func repairMissingTermsFromCachedFeed() -> Bool {
        let existingKeywords = Set(terms.map(\.keyword))
        let cachedKeywords = Set(feedItems.map(\.watch_term_keyword).filter { !$0.isEmpty })
        let keywords = cachedKeywords.subtracting(existingKeywords).sorted()
        guard !keywords.isEmpty else { return false }
        terms.append(contentsOf: keywords.map {
            WatchTerm(keyword: $0, collection_mode: .allInfo, is_active: true, notify_on_new: true, repaired_from_cache: true)
        })
        saveToFile(name: "terms", value: terms)
        AppLogger.persistence.info("Repaired \(keywords.count) missing watch terms from cached feed items")
        return true
    }

    private func logStartupCacheDiagnostics() {
        let termKeywords = Set(terms.map(\.keyword).filter { !$0.isEmpty })
        let cachedKeywords = Set(feedItems.map(\.watch_term_keyword).filter { !$0.isEmpty })
        let missingTermCount = cachedKeywords.subtracting(termKeywords).count
        let summary = "Local cache startup: terms=\(termKeywords.count) cached_feed_keywords=\(cachedKeywords.count) missing_terms=\(missingTermCount) tombstones=\(termDeleteTombstones.count) feed_items=\(feedItems.count)"
        if missingTermCount > 0 {
            AppLogger.persistence.warning("\(summary)")
        } else {
            AppLogger.persistence.info("\(summary)")
        }
    }

    private func loadFromFile<T: Decodable>(name: String, defaultValue: T) -> T {
        let url = fileURL(for: name)
        guard FileManager.default.fileExists(atPath: url.path) else { return defaultValue }
        do {
            let data = try Data(contentsOf: url)
            return try decoder.decode(T.self, from: data)
        } catch {
            AppLogger.persistence.error("Failed to load \(name): \(error.localizedDescription)")
            return defaultValue
        }
    }

    nonisolated private func performSaveToFile<T: Encodable>(name: String, value: T, url: URL) {
        guard FileManager.default.fileExists(atPath: url.deletingLastPathComponent().path) else { return }
        do {
            let data = try JSONEncoder().encode(value)
            try data.write(to: url, options: [.atomic])
        } catch {
            let nsError = error as NSError
            if nsError.domain == NSCocoaErrorDomain,
               nsError.code == CocoaError.Code.fileNoSuchFile.rawValue {
                return
            }
            guard FileManager.default.fileExists(atPath: url.deletingLastPathComponent().path) else { return }
            AppLogger.persistence.error("Failed to save \(name): \(error.localizedDescription)")
        }
    }

    private func saveToFile<T: Encodable>(name: String, value: T) {
        let url = fileURL(for: name)
        queue.async { [weak self] in
            self?.performSaveToFile(name: name, value: value, url: url)
        }
    }

    private func saveFeedItemsSoon() {
        pendingFeedItemsSave?.cancel()
        let url = fileURL(for: "feed_items")
        let snapshot = feedItems
        let pendingSave = PendingFeedItemsSave()
        pendingFeedItemsSave = pendingSave
        queue.asyncAfter(deadline: .now() + feedItemsSaveCoalescingDelay) { [weak self, weak pendingSave] in
            guard let pendingSave, pendingSave.isPending else { return }
            self?.performSaveToFile(name: "feed_items", value: snapshot, url: url)
            pendingSave.complete()
            Task { @MainActor [weak self, weak pendingSave] in
                guard let self, let pendingSave, self.pendingFeedItemsSave === pendingSave else { return }
                self.pendingFeedItemsSave = nil
            }
        }
    }

    private func saveFeedItemsImmediately() {
        pendingFeedItemsSave?.cancel()
        pendingFeedItemsSave = nil
        saveToFile(name: "feed_items", value: feedItems)
    }

    func flushPendingFeedItemsSave() {
        guard let pendingSave = pendingFeedItemsSave else {
            queue.sync {}
            return
        }
        if pendingSave.isPending {
            pendingSave.cancel()
            self.pendingFeedItemsSave = nil
            saveToFile(name: "feed_items", value: feedItems)
        } else {
            self.pendingFeedItemsSave = nil
        }
        queue.sync {}
    }

    // MARK: - Watch Terms
    func saveTerm(keyword: String, collectionMode: CollectionMode = .allInfo, sourceMode: SourceMode = .all, selectedPlatforms: [String] = []) -> WatchTerm {
        let trimmedKeyword = keyword.trimmingCharacters(in: .whitespacesAndNewlines)
        if termDeleteTombstones.removeValue(forKey: trimmedKeyword) != nil {
            saveTermDeleteTombstones()
        }
        let term = WatchTerm(keyword: trimmedKeyword, collection_mode: collectionMode, source_mode: sourceMode, selected_platforms: selectedPlatforms)
        terms.insert(term, at: 0)
        saveToFile(name: "terms", value: terms)
        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        return term
    }

    func updateTerm(id: String, isActive: Bool? = nil, collectionMode: CollectionMode? = nil, sourceMode: SourceMode? = nil, selectedPlatforms: [String]? = nil, notifyOnNew: Bool? = nil, aliases: [String]? = nil) {
        if let idx = terms.firstIndex(where: { $0.id == id }) {
            var term = terms[idx]
            let originalTerm = term
            if let isActive { term.is_active = isActive }
            if let collectionMode { term.collection_mode = collectionMode }
            if let sourceMode { term.source_mode = sourceMode }
            if let selectedPlatforms { term.selected_platforms = selectedPlatforms }
            if let notifyOnNew { term.notify_on_new = notifyOnNew }
            if let aliases { term.aliases = aliases }
            terms[idx] = term
            saveToFile(name: "terms", value: terms)
            if Self.feedScopeFieldsChanged(from: originalTerm, to: term) {
                BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
            }
        }
    }

    func addTermFromBackend(_ term: WatchTerm) {
        terms.insert(term, at: 0)
        saveToFile(name: "terms", value: terms)
        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
    }

    func replaceTerm(localId: String, with serverTerm: WatchTerm) {
        if let idx = terms.firstIndex(where: { $0.id == localId }) {
            let originalTerm = terms[idx]
            terms[idx] = serverTerm
            saveToFile(name: "terms", value: terms)
            if Self.feedScopeFieldsChanged(from: originalTerm, to: serverTerm) {
                BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
            }
        }
    }

    @discardableResult
    func replaceTerm(localId: String, with serverTerm: WatchTerm, ifUnchangedFrom expectedTerm: WatchTerm) -> Bool {
        guard let idx = terms.firstIndex(where: { $0.id == localId }),
              terms[idx] == expectedTerm else {
            return false
        }
        replaceTerm(localId: localId, with: serverTerm)
        return true
    }

    func term(matchingKeyword keyword: String) -> WatchTerm? {
        terms.first { $0.keyword == keyword }
    }

    private static func feedScopeFieldsChanged(from lhs: WatchTerm, to rhs: WatchTerm) -> Bool {
        lhs.keyword != rhs.keyword ||
            lhs.collection_mode != rhs.collection_mode ||
            lhs.source_mode != rhs.source_mode ||
            lhs.selected_platforms != rhs.selected_platforms ||
            lhs.is_active != rhs.is_active ||
            lhs.aliases != rhs.aliases
    }

    func markTermDeleteConfirmed(_ term: WatchTerm) {
        pruneTermDeleteTombstones()
        termDeleteTombstones[term.keyword] = Date()
        saveTermDeleteTombstones()
    }

    func shouldSkipBackendTermAfterLocalDelete(_ term: WatchTerm) -> Bool {
        pruneTermDeleteTombstones()
        return termDeleteTombstones[term.keyword] != nil
    }

    private func pruneTermDeleteTombstones() {
        let originalCount = termDeleteTombstones.count
        let cutoff = Date().addingTimeInterval(-termDeleteTombstoneLifetime)
        termDeleteTombstones = termDeleteTombstones.filter { $0.value >= cutoff }
        if termDeleteTombstones.count != originalCount {
            saveTermDeleteTombstones()
        }
    }

    private func saveTermDeleteTombstones() {
        saveToFile(name: termDeleteTombstonesFileName, value: termDeleteTombstones)
    }

    @discardableResult
    private func applyPersistedTermDeletes() -> Bool {
        guard !termDeleteTombstones.isEmpty else { return false }
        let deletedKeywords = Set(termDeleteTombstones.keys)
        let originalTermCount = terms.count
        let originalFeedCount = feedItems.count
        terms.removeAll { deletedKeywords.contains($0.keyword) }
        feedItems.removeAll { deletedKeywords.contains($0.watch_term_keyword) }
        guard terms.count != originalTermCount || feedItems.count != originalFeedCount else { return false }
        saveFeedItemsImmediately()
        saveToFile(name: "terms", value: terms)
        AppLogger.persistence.info("Applied \(deletedKeywords.count) persisted watch-term delete tombstones")
        return true
    }

    func deleteTerm(id: String) {
        if let idx = terms.firstIndex(where: { $0.id == id }) {
            let keyword = terms[idx].keyword
            let backendTermID = Int(terms[idx].id)
            terms.remove(at: idx)
            feedItems.removeAll { item in
                item.watch_term_keyword == keyword ||
                    (backendTermID != nil && item.watch_term_id == backendTermID)
            }
            saveFeedItemsImmediately()
            saveToFile(name: "terms", value: terms)
            BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        }
    }

    func deleteTerm(keyword: String) -> WatchTerm? {
        guard let term = term(matchingKeyword: keyword) else { return nil }
        deleteTerm(id: term.id)
        return term
    }

    // MARK: - Feed Items & Merging
    func mergeItems(
        newItems: [FeedItem],
        notifyOnNew: Bool = true,
        preserveIncomingItems: Bool = false
    ) -> Int {
        mergeItemsBatched(
            newItemsBatches: [newItems],
            notifyOnNew: notifyOnNew,
            preserveIncomingItems: preserveIncomingItems
        ).first ?? 0
    }

    @discardableResult
    func mergeItemsBatched(
        newItemsBatches: [[FeedItem]],
        notifyOnNew: Bool = true,
        preserveIncomingItems: Bool = false
    ) -> [Int] {
        if newItemsBatches.allSatisfy(\.isEmpty),
           !feedItems.contains(where: FeedItemPolicy.shouldPruneLegacyYouTubeItem) {
            return Array(repeating: 0, count: newItemsBatches.count)
        }

        // On a fresh install / cleared cache the whole first fetch is "new"; don't fire a
        // burst of notifications for content the user is seeing for the first time anyway.
        let wasFirstLoad = feedItems.isEmpty
        var addedCounts = Array(repeating: 0, count: newItemsBatches.count)
        var addedItems: [FeedItem] = []
        let itemKey = { (i: FeedItem) -> String in "\(i.id)::\(i.watch_term_keyword)" }

        let filteredNewBatches = newItemsBatches.map { newItems in
            newItems.filter { item in
                FeedItemPolicy.isMergeEligibleIncomingItem(
                    item,
                    hiddenItems: self.hiddenItems,
                    itemKey: itemKey
                )
            }
        }
        let filteredNew = filteredNewBatches.flatMap { $0 }

        var currentMap = [String: FeedItem]()
        for item in feedItems {
            if FeedItemPolicy.shouldPruneLegacyYouTubeItem(item) { continue }
            currentMap[itemKey(item)] = item
        }

        for (batchIndex, filteredBatch) in filteredNewBatches.enumerated() {
            for item in filteredBatch {
                let key = itemKey(item)
                if currentMap[key] == nil {
                    currentMap[key] = item
                    addedCounts[batchIndex] += 1
                    addedItems.append(item)
                } else {
                    // Merge/update fields if needed (like title length, content, published date)
                    let existing = currentMap[key]!
                    let shouldReplaceTitle = (item.title?.isEmpty == false) &&
                        (existing.title == nil ||
                         existing.title?.contains("...") == true ||
                         (item.title?.count ?? 0) > (existing.title?.count ?? 0) + 8)

                    let merged = FeedItem(
                        id: existing.id,
                        platform: existing.platform,
                        url: Self.mergedURL(existing: existing.url, incoming: item.url),
                        title: shouldReplaceTitle ? item.title : existing.title,
                        content_text: item.content_text ?? existing.content_text,
                        author: item.author ?? existing.author,
                        thumbnail_url: item.thumbnail_url ?? existing.thumbnail_url,
                        media_type: existing.media_type,
                        published_at: mergedPublishedAt(existing: existing, incoming: item),
                        watch_term_keyword: existing.watch_term_keyword,
                        watch_term_id: existing.watch_term_id ?? item.watch_term_id,
                        fetched_at: item.fetched_at,
                        source: item.source ?? existing.source
                    )
                    currentMap[key] = merged
                }
            }
        }

        let preservedKeys = preserveIncomingItems
            ? Set(filteredNew.map(itemKey))
            : Set<String>()

        let sorted = currentMap.values.sorted { lhs, rhs in
            let lhsDate = parseISO8601Date(lhs.published_at) ?? .distantPast
            let rhsDate = parseISO8601Date(rhs.published_at) ?? .distantPast
            if lhsDate != rhsDate { return lhsDate > rhsDate }

            // Keep ordering deterministic when multiple items share the same timestamp.
            // Without stable tie-breakers, background refreshes can rewrite the same
            // 600 objects in a new order, forcing SwiftUI to churn the feed list.
            let lhsKey = itemKey(lhs)
            let rhsKey = itemKey(rhs)
            if lhsKey != rhsKey { return lhsKey < rhsKey }
            return lhs.url < rhs.url
        }
        let finalItems = cappedFeedItemsPreservingSubscribedPlatforms(
            sorted,
            preservedKeys: preservedKeys
        )

        // Only notify for items that survived the cap — avoids pinging for articles
        // that were immediately evicted as too old.
        let shouldScheduleLocalFallback = BackgroundRefreshPolicy.shouldScheduleLocalFallback(
            hasRegisteredRemoteDeviceForCurrentEnvironment: NetworkManager.shared.hasRegisteredAPNSDeviceForCurrentEnvironment
        )
        if notifyOnNew, shouldScheduleLocalFallback, !addedItems.isEmpty, !wasFirstLoad {
            let survivedKeys = Set(finalItems.map { itemKey($0) })
            let notifyItems = addedItems.filter {
                survivedKeys.contains(itemKey($0)) &&
                    !FeedURLPolicy.isBackendMatchRedirectURL($0.url)
            }
            if !notifyItems.isEmpty {
                let terms = self.terms
                Task {
                    await NotificationManager.shared.notifyForNewItems(notifyItems, terms: terms)
                }
            }
        }

        guard finalItems != feedItems else { return addedCounts }

        feedItems = finalItems
        saveFeedItemsSoon()
        return addedCounts
    }

    private static func mergedURL(existing: String, incoming: String) -> String {
        guard !incoming.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return existing
        }
        guard FeedURLPolicy.isBackendMatchRedirectURL(existing),
              !FeedURLPolicy.isBackendMatchRedirectURL(incoming) else {
            return existing
        }
        return incoming
    }

    private func mergedPublishedAt(existing: FeedItem, incoming: FeedItem) -> String {
        let existingDate = parseISO8601Date(existing.published_at)
        let incomingDate = parseISO8601Date(incoming.published_at)
        guard let existingDate, let incomingDate else {
            return existingDate == nil ? incoming.published_at : existing.published_at
        }

        // For discussion sources, published_at represents latest thread activity,
        // so a newer backend value should bump the cached item in the same way the
        // backend feed does. For ordinary articles/videos, prefer the earlier date:
        // it is usually a correction from a fetch-time placeholder to the real
        // publication/broadcast time.
        if discussionActivityPlatforms.contains(Platform.normalize(incoming.platform)) {
            return incomingDate >= existingDate ? incoming.published_at : existing.published_at
        }
        return existingDate <= incomingDate ? existing.published_at : incoming.published_at
    }

    func deleteFeedItem(id: String, watchTermKeyword: String) {
        let key = "\(id)::\(watchTermKeyword)"
        hiddenItems.insert(key)
        saveToFile(name: "hidden_items", value: Array(hiddenItems))
        feedItems.removeAll(where: { $0.id == id && $0.watch_term_keyword == watchTermKeyword })
        saveFeedItemsImmediately()
    }

    func currentCustomFeedItems(_ items: [FeedItem]) -> [FeedItem] {
        let currentIds = Set(customUrls.map(\.id))
        let currentUrls = Set(customUrls.map(\.url))
        return items.filter { item in
            item.platform != "custom" ||
                currentIds.contains(item.id) ||
                currentUrls.contains(item.url)
        }
    }

    private func cappedFeedItemsPreservingSubscribedPlatforms(
        _ sortedItems: [FeedItem],
        preservedKeys: Set<String> = []
    ) -> [FeedItem] {
        guard sortedItems.count > maxFeedItems else { return sortedItems }

        var selected: [FeedItem] = []
        var selectedKeys = Set<String>()
        let itemKey = { (item: FeedItem) in "\(item.id)::\(item.watch_term_keyword)" }
        let subscribed = Set(subscribedPlatforms.filter { $0 != "custom" })

        // Keep a small slice of every subscribed source before filling the rest by date.
        // Otherwise high-volume sources can crowd out lower-volume sources like TVer even
        // when those items were fetched successfully.
        for platformId in subscribed.sorted() {
            var keptForPlatform = 0
            let targetCount = minRetainedFeedItems(for: platformId)
            for item in sortedItems where Platform.normalize(item.platform) == platformId {
                let key = itemKey(item)
                guard selectedKeys.insert(key).inserted else { continue }
                selected.append(item)
                keptForPlatform += 1
                if keptForPlatform >= targetCount { break }
            }
        }

        for item in sortedItems {
            let key = itemKey(item)
            guard preservedKeys.contains(key) else { continue }
            guard selectedKeys.insert(key).inserted else { continue }
            selected.append(item)
        }

        for item in sortedItems {
            guard selected.count < maxFeedItems else { break }
            let key = itemKey(item)
            guard selectedKeys.insert(key).inserted else { continue }
            selected.append(item)
        }

        return selected.sorted { lhs, rhs in
            let lhsDate = parseISO8601Date(lhs.published_at) ?? .distantPast
            let rhsDate = parseISO8601Date(rhs.published_at) ?? .distantPast
            if lhsDate != rhsDate { return lhsDate > rhsDate }
            let lhsKey = itemKey(lhs)
            let rhsKey = itemKey(rhs)
            if lhsKey != rhsKey { return lhsKey < rhsKey }
            return lhs.url < rhs.url
        }
    }

    private func minRetainedFeedItems(for platformId: String) -> Int {
        discussionActivityPlatforms.contains(platformId)
            ? minFeedItemsPerDiscussionPlatform
            : minFeedItemsPerSubscribedPlatform
    }

    // Sort every item by its real published / last-updated date — never by fetch time.
    // The backend heals forum published_at to the real last-reply date (girlschannel,
    // togetter) and device-side scrapes carry the article's real pubDate, so a batch of
    // forum threads fetched together no longer shares fetched_at≈now and clumps at the
    // top of the feed.
    private func sortDate(for item: FeedItem) -> Date {
        return parseISO8601Date(item.published_at) ?? .distantPast
    }

    private struct FeedQueryCandidate {
        let item: FeedItem
        let platformKey: String
        let sortDate: Date
    }

    // MARK: - Query Feed (Filtering)
    func queryFeed(keyword: String?, days: Int) -> [FeedItem] {
        let now = Date()
        // days == 0 means "All Time" — no cutoff applied
        let cutoffDate = days > 0 ? Calendar.current.date(byAdding: .day, value: -days, to: now) : nil

        let subscribedPlatformSet = Set(subscribedPlatforms)
        let termsByKeyword = terms.reduce(into: [String: WatchTerm]()) { result, term in
            result[term.keyword] = term
        }

        let deduped = feedItems.compactMap { item -> FeedQueryCandidate? in
            let key = "\(item.id)::\(item.watch_term_keyword)"
            if hiddenItems.contains(key) { return nil }

            // Search pages fallbacks
            if FeedItemPolicy.isSearchFallback(item) { return nil }
            if FeedItemPolicy.shouldPruneLegacyYouTubeItem(item) { return nil }
            if FeedURLPolicy.isBackendMatchRedirectURL(item.url) { return nil }

            // Hide broken Yahoo fallback cards whose readable field is only a URL.
            // Valid Google News summaries contain an HTML anchor, so checking for any
            // "https://" substring incorrectly removed every Yahoo article.
            if item.platform == "yahoonews"
                && (Self.isBareWebAddress(item.title) || Self.isBareWebAddress(item.content_text)) {
                return nil
            }

            // Apply cheap, high-selectivity filters before date parsing, strict
            // keyword matching, platform lookup, and URL/title deduplication work below.
            if let kw = keyword, !kw.isEmpty {
                if item.platform != "custom" && item.watch_term_keyword != kw {
                    return nil
                }
            }

            let platformKey = Platform.normalize(item.platform)
            if !subscribedPlatformSet.contains(platformKey) {
                return nil
            }

            let platformDef = Platform.forRawValue(item.platform)
            var parsedPublishedAt: Date?
            let localTerm = item.watch_term_keyword.isEmpty ? nil : termsByKeyword[item.watch_term_keyword]

            if !termsByKeyword.isEmpty, !item.watch_term_keyword.isEmpty, localTerm == nil {
                return nil
            }

            if platformKey == "youtube" {
                let youtubeCutoff = Calendar.current.date(byAdding: .day, value: -31, to: now) ?? .distantFuture
                parsedPublishedAt = parseISO8601Date(item.published_at)
                guard let itemDate = parsedPublishedAt, itemDate >= youtubeCutoff else {
                    return nil
                }
            }

            // Cutoff check — use proper Date comparison so timezone-offset strings sort correctly
            if let cutoff = cutoffDate, platformDef?.skipDateCutoff != true {
                parsedPublishedAt = parsedPublishedAt ?? parseISO8601Date(item.published_at)
                guard let itemDate = parsedPublishedAt, itemDate >= cutoff else {
                    return nil
                }
            }

            // Strict keyword matching — news-type platforms require keyword/alias to appear in content
            if platformDef?.usesStrictKeywordMatching == true, !item.watch_term_keyword.isEmpty {
                let keywordCandidates = [item.watch_term_keyword] + (localTerm?.aliases ?? [])
                if !keywordCandidates.contains(where: { matchesKeyword(item: item, kw: $0) }) {
                    return nil
                }
            }

            return FeedQueryCandidate(
                item: item,
                platformKey: platformKey,
                sortDate: parsedPublishedAt ?? sortDate(for: item)
            )
        }
        .sorted { lhs, rhs in
            lhs.sortDate > rhs.sortDate
        }
        .reduce(into: (items: [FeedItem](), urls: Set<String>(), platformTitles: Set<String>(), articleTitles: Set<String>())) { acc, candidate in
            // Deduplicate the same article arriving from two paths — e.g. a backend copy
            // with a direct URL and a device-scraped Google News copy with a news.google
            // URL (different ids/URLs, same story). Canonical URLs are global duplicates
            // even when tracking params or wrapper params differ. Normalized titles still
            // catch direct-vs-redirect copies within the same canonical platform, and a
            // stricter article title key catches cross-source syndication.
            let item = candidate.item
            let urlKey = Self.normalizedURLKey(item.url)
            guard urlKey.isEmpty || acc.urls.insert(urlKey).inserted else { return }

            let titleKey = Self.normalizedTitleKey(item.title)
            let platformTitleKey = titleKey.isEmpty
                ? ""
                : "\(candidate.platformKey)|\(titleKey)"
            guard platformTitleKey.isEmpty || acc.platformTitles.insert(platformTitleKey).inserted else { return }

            let articleTitleKey = Self.normalizedArticleTitleKey(item.title)
            if Self.shouldDeduplicateArticleTitle(item), articleTitleKey.count >= 10 {
                guard acc.articleTitles.insert(articleTitleKey).inserted else { return }
            }

            acc.items.append(item)
        }.items

        return deduped
    }

    static func normalizedURLKey(_ rawURL: String) -> String {
        let trimmed = rawURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "" }
        guard var components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https" else {
            return trimmed.lowercased()
        }

        let host = (components.host ?? "").lowercased()
        guard !host.isEmpty,
              host.contains(".") || host == "localhost" || host.allSatisfy(\.isNumber) else {
            return ""
        }
        if host == "youtu.be" {
            let videoId = components.path
                .split(separator: "/")
                .first
                .map(String.init) ?? ""
            if !videoId.isEmpty { return "https://youtube.com/watch?v=\(videoId)" }
        }

        if host == "youtube.com" || host == "www.youtube.com" || host == "m.youtube.com" {
            let queryItems = components.queryItems ?? []
            if components.path == "/watch",
               let videoId = queryItems.first(where: { $0.name == "v" })?.value,
               !videoId.isEmpty {
                return "https://youtube.com/watch?v=\(videoId)"
            }
        }

        // Public web links routinely bounce between HTTP and HTTPS while identifying
        // the same page. Prefer the secure form so those variants share one key.
        components.scheme = "https"
        components.host = normalizedHost(host)
        components.fragment = nil
        if (scheme == "https" && components.port == 443) || (scheme == "http" && components.port == 80) {
            components.port = nil
        }

        var path = components.percentEncodedPath
        while path.count > 1 && path.hasSuffix("/") {
            path.removeLast()
        }
        components.percentEncodedPath = path

        let queryItems = (components.queryItems ?? [])
            .filter { !isIgnoredURLQueryItem($0.name) }
            .sorted {
                if $0.name != $1.name { return $0.name < $1.name }
                return ($0.value ?? "") < ($1.value ?? "")
            }
        components.queryItems = queryItems.isEmpty ? nil : queryItems

        return (components.url?.absoluteString ?? trimmed).lowercased()
    }

    // Collapses a title to a comparison key: lowercased, alphanumerics only (keeps CJK,
    // drops spaces/punctuation/suffix separators) so two copies of the same article match.
    static func normalizedTitleKey(_ title: String?) -> String {
        guard let t = title?.lowercased(), !t.isEmpty else { return "" }
        return String(t.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) })
    }

    static func normalizedArticleTitleKey(_ title: String?) -> String {
        guard var text = cleanDisplayText(title)?.lowercased(), !text.isEmpty else { return "" }

        for separator in [" - ", " | ", "｜"] {
            guard let range = text.range(of: separator, options: .backwards) else { continue }
            let prefix = String(text[..<range.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
            let suffix = String(text[range.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
            if prefix.count >= 8, suffix.count <= 40, publisherSuffixLooksLikely(suffix) {
                text = prefix
                break
            }
        }

        text = strippingTrailingPublisherParenthetical(text)
        return normalizedTitleKey(text)
    }

    private static func normalizedHost(_ host: String) -> String {
        var value = host
        if value.hasPrefix("www.") { value.removeFirst(4) }
        if value.hasPrefix("m.") { value.removeFirst(2) }
        return value
    }

    private static func isIgnoredURLQueryItem(_ rawName: String) -> Bool {
        let name = rawName.lowercased()
        return name.hasPrefix("utm_") || [
            "fbclid", "gclid", "yclid", "igshid", "mc_cid", "mc_eid",
            "ref", "ref_src", "spm", "oc", "hl", "gl", "ceid"
        ].contains(name)
    }

    private static func shouldDeduplicateArticleTitle(_ item: FeedItem) -> Bool {
        if item.media_type == "article" || item.media_type == "text" { return true }
        return Platform.forRawValue(item.platform)?.usesStrictKeywordMatching == true
    }

    private static func publisherSuffixLooksLikely(_ suffix: String) -> Bool {
        if suffix.isEmpty { return false }
        let knownWords = [
            "news", "ニュース", "新聞", "online", "web", "press", "times",
            "ナタリー", "oricon", "mdpr", "modelpress", "yahoo", "google"
        ]
        if knownWords.contains(where: { suffix.contains($0) }) { return true }
        return suffix.count <= 14 && !suffix.contains(" ")
    }

    private static func strippingTrailingPublisherParenthetical(_ text: String) -> String {
        let pairs: [(Character, Character)] = [(")", "("), ("）", "（")]
        var current = text.trimmingCharacters(in: .whitespacesAndNewlines)
        for (closing, opening) in pairs where current.last == closing {
            guard let openIndex = current.lastIndex(of: opening) else { continue }
            let prefix = String(current[..<openIndex]).trimmingCharacters(in: .whitespacesAndNewlines)
            let suffix = String(current[current.index(after: openIndex)..<current.index(before: current.endIndex)])
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if prefix.count >= 8, suffix.count <= 40, publisherSuffixLooksLikely(suffix) {
                current = prefix
            }
        }
        return current
    }

    private static func isBareWebAddress(_ value: String?) -> Bool {
        guard let text = value?.trimmingCharacters(in: .whitespacesAndNewlines),
              let url = URL(string: text),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              url.host != nil else {
            return false
        }
        return !text.contains(where: \.isWhitespace)
    }

    private func matchesKeyword(item: FeedItem, kw: String) -> Bool {
        let primaryText = item.title?.trimmingCharacters(in: .whitespacesAndNewlines)
        let haystack = ((primaryText?.isEmpty == false ? primaryText : item.content_text) ?? "").lowercased()
        let needle = kw.lowercased()
        if needle.isEmpty { return true }
        if haystack.contains(needle) { return true }

        let parts = kw.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }
        if parts.count > 1 {
            return parts.allSatisfy { haystack.contains($0.lowercased()) }
        }
        return false
    }

    // MARK: - Bookmarks (Saved)
    func getSaved() -> [SavedPage] {
        return savedPages
    }

    func toggleSaved(item: FeedItem) -> Bool {
        if let idx = savedPages.firstIndex(where: { $0.id == item.id }) {
            savedPages.remove(at: idx)
            saveToFile(name: "saved_pages", value: savedPages)
            return false
        } else {
            let page = SavedPage(
                id: item.id,
                url: item.url,
                title: item.title,
                platform: item.platform,
                saved_at: iso8601.string(from: Date())
            )
            savedPages.insert(page, at: 0)
            saveToFile(name: "saved_pages", value: savedPages)
            return true
        }
    }

    func removeSaved(id: String) {
        savedPages.removeAll(where: { $0.id == id })
        saveToFile(name: "saved_pages", value: savedPages)
    }

    // MARK: - Subscribed Platforms
    func setSubscribedPlatforms(platforms: [String]) {
        let originalPlatforms = Set(subscribedPlatforms)
        subscribedPlatforms = platforms
        saveToFile(name: "subscribed_platforms", value: subscribedPlatforms)
        if Set(platforms) != originalPlatforms {
            BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        }
    }

    // MARK: - Custom URLs
    func addCustomUrl(url: String, title: String) {
        let trimmed = url.trimmingCharacters(in: .whitespacesAndNewlines)
        // Only prepend https:// when there is no scheme (no colon before first slash)
        let hasScheme = trimmed.range(of: #"^[a-zA-Z][a-zA-Z0-9+\-.]*://"#, options: .regularExpression) != nil
        let normalized = hasScheme ? trimmed : "https://\(trimmed)"
        guard let scheme = URL(string: normalized)?.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return }
        let id = "custom:\(normalized.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? normalized)"
        if customUrls.contains(where: { $0.id == id }) { return }
        let trimmedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let entry = CustomUrl(id: id, url: normalized, title: trimmedTitle.isEmpty ? nil : trimmedTitle, added_at: iso8601.string(from: Date()))
        customUrls.insert(entry, at: 0)
        saveToFile(name: "custom_urls", value: customUrls)
        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
    }

    func removeCustomUrl(id: String) {
        let removedUrls = customUrls
            .filter { $0.id == id }
            .map { $0.url }
        let originalCount = customUrls.count
        let originalFeedCount = feedItems.count
        customUrls.removeAll(where: { $0.id == id })
        saveToFile(name: "custom_urls", value: customUrls)
        if customUrls.count != originalCount {
            feedItems.removeAll { item in
                item.platform == "custom" &&
                    (item.id == id || removedUrls.contains(item.url))
            }
            if feedItems.count != originalFeedCount {
                saveFeedItemsImmediately()
            }
            BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
        }
    }

    // MARK: - Data Reset
    func clearAllData() {
        pendingFeedItemsSave?.cancel()
        pendingFeedItemsSave = nil
        queue.sync {}
        let fileNames = [
            "terms", "feed_items", "saved_pages", "custom_urls",
            "subscribed_platforms", "oshi_avatars", "oshi_compositions", "hidden_items",
            termDeleteTombstonesFileName
        ]
        terms = []
        feedItems = []
        savedPages = []
        customUrls = []
        subscribedPlatforms = Platform.all.filter(\.subscribedByDefault).map(\.id)
        wallpaper = nil
        sourcesOrder = nil
        oshiAvatars = [:]
        compositions = [:]
        hiddenItems = []
        termDeleteTombstones = [:]

        for name in fileNames {
            let url = fileURL(for: name)
            if FileManager.default.fileExists(atPath: url.path) {
                try? FileManager.default.removeItem(at: url)
            }
        }
        // Delete all content cache files (cache_*.json) written by saveContentCache.
        if let contents = try? FileManager.default.contentsOfDirectory(
            at: storeDirectory, includingPropertiesForKeys: nil
        ) {
            for cacheUrl in contents where cacheUrl.lastPathComponent.hasPrefix("cache_") {
                try? FileManager.default.removeItem(at: cacheUrl)
            }
        }
        UserDefaults.standard.removeObject(forKey: "wallpaper_url")
        UserDefaults.standard.removeObject(forKey: "sources_order")
        saveToFile(name: "subscribed_platforms", value: subscribedPlatforms)
        BackgroundRefreshPolicy.invalidateRefreshCompletionsForFeedScopeChange()
    }

    // MARK: - Wallpaper & Custom Order (UserDefaults)
    func setWallpaper(url: String?) {
        wallpaper = url
        if let url {
            UserDefaults.standard.set(url, forKey: "wallpaper_url")
        } else {
            UserDefaults.standard.removeObject(forKey: "wallpaper_url")
        }
    }

    func setSourcesOrder(order: [String]) {
        sourcesOrder = order
        UserDefaults.standard.set(order, forKey: "sources_order")
    }

    // MARK: - Oshi Avatars & Compositions
    func setOshiAvatar(keyword: String, imageUrl: String) {
        oshiAvatars[keyword] = imageUrl
        saveToFile(name: "oshi_avatars", value: oshiAvatars)
    }

    func setOshiComposition(keyword: String, layers: [AvatarLayer]) {
        compositions[keyword] = layers
        saveToFile(name: "oshi_compositions", value: compositions)
    }

    // MARK: - UI Test Fixture
    func resetForUITesting() {
        guard ProcessInfo.processInfo.arguments.contains("--uitesting") else { return }

        let now = iso8601.string(from: Date())
        let term = WatchTerm(id: "ui-term-oshitest", keyword: "UITest Oshi", collection_mode: .allInfo, is_active: true, created_at: now)
        let feedItem = FeedItem(
            id: "ui-feed-reader",
            platform: "news",
            url: "https://example.com/oshireader-ui-test",
            title: "UITest Oshi headline",
            content_text: "A seeded article used by OshiReader UI tests.",
            author: "UI Test Desk",
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: term.keyword,
            fetched_at: now
        )
        let redirectFeedItem = FeedItem(
            id: "ui-feed-redirect",
            platform: "news",
            url: "https://backend.example.com/api/feed/matches/404/redirect",
            title: "UITest Oshi stale redirect headline",
            content_text: "A stale backend redirect for UITest Oshi that should not be opened by the feed.",
            author: "UI Test Desk",
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: term.keyword,
            fetched_at: now
        )
        let stableFeedItem = FeedItem(
            id: "ui-feed-stable",
            platform: "news",
            url: "https://example.com/oshireader-stable-ui-test",
            title: "UITest Oshi stable headline",
            content_text: "A stable UITest Oshi article used to prove feed taps avoid stale match redirects.",
            author: "UI Test Desk",
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: term.keyword,
            fetched_at: now
        )
        let savedPage = SavedPage(
            id: "ui-saved-reader",
            url: "https://example.com/oshireader-saved",
            title: "UITest saved article",
            platform: "news",
            saved_at: now
        )
        let customUrl = CustomUrl(
            id: "custom:https%3A%2F%2Fexample.com%2Ffeed.xml",
            url: "https://example.com/feed.xml",
            title: "UITest custom feed",
            added_at: now
        )
        let layer = AvatarLayer(
            id: "ui-avatar-layer",
            imageUrl: "https://example.com/avatar.png",
            x: 105,
            y: 105,
            scale: 1.0,
            zIndex: 1
        )
        let platformFixtureID: String? = {
            let args = ProcessInfo.processInfo.arguments
            guard let index = args.firstIndex(of: "--uitesting-single-platform-feed"),
                  args.indices.contains(index + 1) else {
                return nil
            }
            return args[index + 1]
        }()
        let usesAllPlatformSortFixture = ProcessInfo.processInfo.arguments.contains("--uitesting-all-platform-sort-feed")
        let allPlatformFeedItems = Platform.all.enumerated().map { index, platform in
            let publishedAt = usesAllPlatformSortFixture
                ? iso8601.string(from: Date().addingTimeInterval(TimeInterval(-index * 60)))
                : now
            return FeedItem(
                id: platform.id == "youtube" ? "youtube:ui-platform-youtube" : "ui-platform-\(platform.id)",
                platform: platform.id,
                url: platform.id == "youtube"
                    ? "https://www.youtube.com/watch?v=oshireaderui1"
                    : "https://example.com/oshireader-ui-test/\(platform.id)",
                title: "UITest Oshi \(platform.name) item",
                content_text: "A seeded \(platform.name) item for UITest Oshi used by OshiReader UI tests.",
                author: "UI Test Desk",
                thumbnail_url: nil,
                media_type: platform.isMediaPlatform ? "video" : "article",
                published_at: publishedAt,
                watch_term_keyword: term.keyword,
                fetched_at: now,
                source: platform.id == "youtube" ? "youtube_device_scrape" : nil
            )
        }

        terms = [term]
        if let platformFixtureID {
            feedItems = allPlatformFeedItems.filter { Platform.normalize($0.platform) == platformFixtureID }
        } else if usesAllPlatformSortFixture {
            feedItems = Array(allPlatformFeedItems.reversed())
        } else if ProcessInfo.processInfo.arguments.contains("--uitesting-redirect-feed") {
            feedItems = [redirectFeedItem, stableFeedItem]
        } else {
            feedItems = [feedItem]
        }
        savedPages = [savedPage]
        customUrls = [customUrl]
        if let platformFixtureID {
            subscribedPlatforms = [platformFixtureID]
        } else if usesAllPlatformSortFixture {
            subscribedPlatforms = Platform.all.map(\.id)
        } else {
            subscribedPlatforms = ["news", "youtube", "tver", "custom"]
        }
        wallpaper = nil
        sourcesOrder = nil
        oshiAvatars = [:]
        compositions = [term.keyword: [layer]]
        hiddenItems = []
        // Do NOT persist fixture data — only seed in-memory so nothing stains the
        // container after the test process exits.
        UserDefaults.standard.removeObject(forKey: "wallpaper_url")
        UserDefaults.standard.removeObject(forKey: "sources_order")
    }

    // MARK: - Content Cache (Offline Pages)
    func saveContentCache(id: String, html: String) {
        guard html.utf8.count <= maxContentCacheBytes else {
            AppLogger.persistence.info("Skipped oversized content cache for \(id)")
            return
        }

        let name = "cache_\(id.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? id)"
        saveToFile(name: name, value: html)
        pruneContentCache()
    }

    func getContentCache(id: String) -> String? {
        let name = "cache_\(id.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? id)"
        queue.sync {}
        let result: String? = loadFromFile(name: name, defaultValue: nil)
        return result
    }

    func removeContentCache(id: String) {
        let name = "cache_\(id.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? id)"
        let url = fileURL(for: name)
        queue.async {
            if FileManager.default.fileExists(atPath: url.path) {
                try? FileManager.default.removeItem(at: url)
            }
        }
    }

    private func pruneContentCache() {
        let storeDirectory = storeDirectory
        let maxFiles = maxContentCacheFiles
        queue.async {
            guard let urls = try? FileManager.default.contentsOfDirectory(
                at: storeDirectory,
                includingPropertiesForKeys: [.contentModificationDateKey],
                options: [.skipsHiddenFiles]
            ) else { return }

            let cacheUrls = urls
                .filter { $0.lastPathComponent.hasPrefix("cache_") && $0.pathExtension == "json" }
                .sorted { lhs, rhs in
                    let lhsDate = (try? lhs.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                    let rhsDate = (try? rhs.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                    return lhsDate > rhsDate
                }

            guard cacheUrls.count > maxFiles else { return }

            for url in cacheUrls.dropFirst(maxFiles) {
                try? FileManager.default.removeItem(at: url)
            }
        }
    }

    // MARK: - Stats
    func getStats() -> (total: Int, byPlatform: [String: Int]) {
        var counts = [String: Int]()
        for item in feedItems {
            let key = Platform.normalize(item.platform)
            counts[key] = (counts[key] ?? 0) + 1
        }
        return (feedItems.count, counts)
    }
}
