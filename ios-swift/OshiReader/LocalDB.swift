import Foundation
import Combine

@MainActor
class LocalDB: ObservableObject {
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

    // Bump this whenever a migration step is added below.
    private static let currentSchemaVersion = 2
    private static let schemaVersionKey = "localdb_schema_version"

    init(directory: URL) {
        self.storeDirectory = directory
        loadAll()
        runMigrationsIfNeeded()
        pruneContentCache()
    }

    // MARK: - File Paths
    private func fileURL(for name: String) -> URL {
        storeDirectory.appendingPathComponent("\(name).json")
    }

    // MARK: - Load and Save Helpers
    private func loadAll() {
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
    }

    // MARK: - Schema Migrations
    private func runMigrationsIfNeeded() {
        let stored = UserDefaults.standard.integer(forKey: Self.schemaVersionKey)
        guard stored < Self.currentSchemaVersion else { return }
        for version in (stored + 1)...Self.currentSchemaVersion {
            migrate(to: version)
        }
        UserDefaults.standard.set(Self.currentSchemaVersion, forKey: Self.schemaVersionKey)
        AppLogger.persistence.info("Schema migrated from v\(stored) → v\(Self.currentSchemaVersion)")
    }

    private func migrate(to version: Int) {
        switch version {
        case 1:
            // Ensure every platform defined in Platform.all is present in the subscription list.
            // Handles installs that existed before a platform was added to the registry.
            let allIds = Set(Platform.all.filter(\.subscribedByDefault).map(\.id))
            let missing = allIds.subtracting(Set(subscribedPlatforms))
            if !missing.isEmpty {
                subscribedPlatforms.append(contentsOf: missing)
                saveToFile(name: "subscribed_platforms", value: subscribedPlatforms)
            }
        case 2:
            pruneIrrelevantCachedArticleItems()
        default:
            AppLogger.persistence.warning("No migration handler for schema v\(version)")
        }
    }

    private func pruneIrrelevantCachedArticleItems() {
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
            saveToFile(name: "feed_items", value: self.feedItems)
            AppLogger.persistence.info(
                "Pruned \(originalCount - self.feedItems.count) irrelevant cached feed items"
            )
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

    private func saveToFile<T: Encodable>(name: String, value: T) {
        let url = fileURL(for: name)
        queue.async { [weak self] in
            guard self != nil else { return }
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
    }

    // MARK: - Watch Terms
    func saveTerm(keyword: String, collectionMode: CollectionMode = .allInfo) -> WatchTerm {
        let term = WatchTerm(keyword: keyword.trimmingCharacters(in: .whitespacesAndNewlines), collection_mode: collectionMode)
        terms.insert(term, at: 0)
        saveToFile(name: "terms", value: terms)
        return term
    }

    func updateTerm(id: String, isActive: Bool? = nil, collectionMode: CollectionMode? = nil, notifyOnNew: Bool? = nil, aliases: [String]? = nil) {
        if let idx = terms.firstIndex(where: { $0.id == id }) {
            var term = terms[idx]
            if let isActive { term.is_active = isActive }
            if let collectionMode { term.collection_mode = collectionMode }
            if let notifyOnNew { term.notify_on_new = notifyOnNew }
            if let aliases { term.aliases = aliases }
            terms[idx] = term
            saveToFile(name: "terms", value: terms)
        }
    }

    func addTermFromBackend(_ term: WatchTerm) {
        terms.insert(term, at: 0)
        saveToFile(name: "terms", value: terms)
    }

    func replaceTerm(localId: String, with serverTerm: WatchTerm) {
        if let idx = terms.firstIndex(where: { $0.id == localId }) {
            terms[idx] = serverTerm
            saveToFile(name: "terms", value: terms)
        }
    }

    func deleteTerm(id: String) {
        if let idx = terms.firstIndex(where: { $0.id == id }) {
            let keyword = terms[idx].keyword
            terms.remove(at: idx)
            saveToFile(name: "terms", value: terms)
            feedItems.removeAll(where: { $0.watch_term_keyword == keyword })
            saveToFile(name: "feed_items", value: feedItems)
        }
    }

    // MARK: - Feed Items & Merging
    func mergeItems(newItems: [FeedItem], notifyOnNew: Bool = true) -> Int {
        // On a fresh install / cleared cache the whole first fetch is "new"; don't fire a
        // burst of notifications for content the user is seeing for the first time anyway.
        let wasFirstLoad = feedItems.isEmpty
        var addedCount = 0
        var addedItems: [FeedItem] = []
        let itemKey = { (i: FeedItem) -> String in "\(i.id)::\(i.watch_term_keyword)" }

        let filteredNew = newItems.filter { item in
            let key = itemKey(item)
            let isHidden = self.hiddenItems.contains(key)
            let isSearchFallback = item.id.contains("search:") || item.title?.lowercased().contains("search:") == true
            return !isHidden && !isSearchFallback
        }

        var currentMap = [String: FeedItem]()
        for item in feedItems {
            currentMap[itemKey(item)] = item
        }

        for item in filteredNew {
            let key = itemKey(item)
            if currentMap[key] == nil {
                currentMap[key] = item
                addedCount += 1
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
                    url: existing.url,
                    title: shouldReplaceTitle ? item.title : existing.title,
                    content_text: item.content_text ?? existing.content_text,
                    author: item.author ?? existing.author,
                    thumbnail_url: item.thumbnail_url ?? existing.thumbnail_url,
                    media_type: existing.media_type,
                    published_at: {
                        let ed = parseISO8601Date(existing.published_at) ?? .distantFuture
                        let nd = parseISO8601Date(item.published_at) ?? .distantFuture
                        return ed <= nd ? existing.published_at : item.published_at
                    }(),
                    watch_term_keyword: existing.watch_term_keyword,
                    fetched_at: item.fetched_at
                )
                currentMap[key] = merged
            }
        }

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
        let finalItems = cappedFeedItemsPreservingSubscribedPlatforms(sorted)

        // Only notify for items that survived the cap — avoids pinging for articles
        // that were immediately evicted as too old.
        let shouldScheduleLocalFallback = BackgroundRefreshPolicy.shouldScheduleLocalFallback(
            hasRegisteredRemoteDeviceForCurrentEnvironment: NetworkManager.shared.hasRegisteredAPNSDeviceForCurrentEnvironment
        )
        if notifyOnNew, shouldScheduleLocalFallback, !addedItems.isEmpty, !wasFirstLoad {
            let survivedKeys = Set(finalItems.map { itemKey($0) })
            let notifyItems = addedItems.filter { survivedKeys.contains(itemKey($0)) }
            if !notifyItems.isEmpty {
                let terms = self.terms
                Task {
                    await NotificationManager.shared.notifyForNewItems(notifyItems, terms: terms)
                }
            }
        }

        guard finalItems != feedItems else { return addedCount }

        feedItems = finalItems
        saveToFile(name: "feed_items", value: feedItems)
        return addedCount
    }

    func deleteFeedItem(id: String, watchTermKeyword: String) {
        let key = "\(id)::\(watchTermKeyword)"
        hiddenItems.insert(key)
        saveToFile(name: "hidden_items", value: Array(hiddenItems))
        feedItems.removeAll(where: { $0.id == id && $0.watch_term_keyword == watchTermKeyword })
        saveToFile(name: "feed_items", value: feedItems)
    }

    private func cappedFeedItemsPreservingSubscribedPlatforms(_ sortedItems: [FeedItem]) -> [FeedItem] {
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
            for item in sortedItems where Platform.normalize(item.platform) == platformId {
                let key = itemKey(item)
                guard selectedKeys.insert(key).inserted else { continue }
                selected.append(item)
                keptForPlatform += 1
                if keptForPlatform >= minFeedItemsPerSubscribedPlatform { break }
            }
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

    // Sort every item by its real published / last-updated date — never by fetch time.
    // The backend heals forum published_at to the real last-reply date (girlschannel,
    // togetter) and device-side scrapes carry the article's real pubDate, so a batch of
    // forum threads fetched together no longer shares fetched_at≈now and clumps at the
    // top of the feed.
    private func sortDate(for item: FeedItem) -> Date {
        return parseISO8601Date(item.published_at) ?? .distantPast
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

        let deduped = feedItems.filter { item in
            let key = "\(item.id)::\(item.watch_term_keyword)"
            if hiddenItems.contains(key) { return false }

            // Search pages fallbacks
            if item.id.contains("search:") || item.title?.lowercased().contains("search:") == true { return false }

            // Hide broken Yahoo fallback cards whose readable field is only a URL.
            // Valid Google News summaries contain an HTML anchor, so checking for any
            // "https://" substring incorrectly removed every Yahoo article.
            if item.platform == "yahoonews"
                && (Self.isBareWebAddress(item.title) || Self.isBareWebAddress(item.content_text)) {
                return false
            }

            let platformDef = Platform.forRawValue(item.platform)

            // Cutoff check — use proper Date comparison so timezone-offset strings sort correctly
            if let cutoff = cutoffDate, platformDef?.skipDateCutoff != true {
                guard let itemDate = parseISO8601Date(item.published_at), itemDate >= cutoff else {
                    return false
                }
            }

            // Keyword filter
            if let kw = keyword, !kw.isEmpty {
                if item.platform == "custom" {
                    // Let custom pages pass if custom matches
                } else if item.watch_term_keyword != kw {
                    return false
                }
            }

            // Strict keyword matching — news-type platforms require keyword/alias to appear in content
            if platformDef?.usesStrictKeywordMatching == true, !item.watch_term_keyword.isEmpty {
                let term = termsByKeyword[item.watch_term_keyword]
                let candidates = [item.watch_term_keyword] + (term?.aliases ?? [])
                if !candidates.contains(where: { matchesKeyword(item: item, kw: $0) }) {
                    return false
                }
            }

            // Subscribed platforms
            let platformKey = Platform.normalize(item.platform)
            if !subscribedPlatformSet.contains(platformKey) {
                return false
            }

            return true
        }
        .sorted { lhs, rhs in
            sortDate(for: lhs) > sortDate(for: rhs)
        }
        .reduce(into: ([FeedItem](), Set<String>())) { acc, item in
            // Deduplicate the same article arriving from two paths — e.g. a backend copy
            // with a direct URL and a device-scraped Google News copy with a news.google
            // URL (different ids/URLs, same story). Key on canonical platform + normalized
            // title, falling back to URL for titleless items. Only the first (most recent
            // by sort) copy survives.
            let titleKey = Self.normalizedTitleKey(item.title)
            let dedupKey = titleKey.isEmpty
                ? "u:\(item.url)"
                : "t:\(Platform.normalize(item.platform))|\(titleKey)"
            if acc.1.insert(dedupKey).inserted { acc.0.append(item) }
        }.0

        return Self.diversifiedFeedOrder(deduped)
    }

    // Collapses a title to a comparison key: lowercased, alphanumerics only (keeps CJK,
    // drops spaces/punctuation/suffix separators) so two copies of the same article match.
    static func normalizedTitleKey(_ title: String?) -> String {
        guard let t = title?.lowercased(), !t.isEmpty else { return "" }
        return String(t.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) })
    }

    private static func diversifiedFeedOrder(_ items: [FeedItem]) -> [FeedItem] {
        guard items.count > 2 else { return items }

        var remaining = items
        var ordered: [FeedItem] = []
        ordered.reserveCapacity(items.count)

        while !remaining.isEmpty {
            let searchLimit = min(remaining.count, 12)
            var bestIndex = 0
            var bestScore = repetitionScore(for: remaining[0], after: ordered)

            if bestScore > 0, searchLimit > 1 {
                for index in 1..<searchLimit {
                    let score = repetitionScore(for: remaining[index], after: ordered)
                    if score < bestScore {
                        bestIndex = index
                        bestScore = score
                        if score == 0 { break }
                    }
                }
            }

            ordered.append(remaining.remove(at: bestIndex))
        }

        return ordered
    }

    private static func repetitionScore(for item: FeedItem, after ordered: [FeedItem]) -> Int {
        guard !ordered.isEmpty else { return 0 }

        let platform = Platform.normalize(item.platform)
        let keyword = normalizedKeywordKey(item.watch_term_keyword)
        let titleCluster = titleClusterKey(item.title)
        let recent = ordered.suffix(4)
        var score = 0

        if let previous = ordered.last {
            if Platform.normalize(previous.platform) == platform { score += 3 }
            if normalizedKeywordKey(previous.watch_term_keyword) == keyword, !keyword.isEmpty { score += 2 }
            if titleClusterKey(previous.title) == titleCluster, !titleCluster.isEmpty { score += 8 }
        }

        if recent.filter({ Platform.normalize($0.platform) == platform }).count >= 2 { score += 4 }
        if !keyword.isEmpty,
           recent.filter({ normalizedKeywordKey($0.watch_term_keyword) == keyword }).count >= 3 {
            score += 2
        }

        if !titleCluster.isEmpty {
            let matchingRecentStories = ordered.suffix(8).filter { titleClusterKey($0.title) == titleCluster }.count
            if matchingRecentStories > 0 { score += 6 + matchingRecentStories }
        }

        return score
    }

    private static func normalizedKeywordKey(_ keyword: String) -> String {
        keyword.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private static func titleClusterKey(_ title: String?) -> String {
        let key = normalizedTitleKey(title)
        guard key.count >= 24 else { return key }
        return String(key.prefix(32))
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
        subscribedPlatforms = platforms
        saveToFile(name: "subscribed_platforms", value: subscribedPlatforms)
    }

    // MARK: - Custom URLs
    func addCustomUrl(url: String, title: String) {
        let trimmed = url.trimmingCharacters(in: .whitespacesAndNewlines)
        // Only prepend https:// when there is no scheme (no colon before first slash)
        let hasScheme = trimmed.range(of: #"^[a-zA-Z][a-zA-Z0-9+\-.]*://"#, options: .regularExpression) != nil
        let normalized = hasScheme ? trimmed : "https://\(trimmed)"
        guard let scheme = URL(string: normalized)?.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return }
        let id = "custom:\(normalized.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed)?.prefix(60) ?? "")"
        if customUrls.contains(where: { $0.id == id }) { return }
        let trimmedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let entry = CustomUrl(id: id, url: normalized, title: trimmedTitle.isEmpty ? nil : trimmedTitle, added_at: iso8601.string(from: Date()))
        customUrls.insert(entry, at: 0)
        saveToFile(name: "custom_urls", value: customUrls)
    }

    func removeCustomUrl(id: String) {
        customUrls.removeAll(where: { $0.id == id })
        saveToFile(name: "custom_urls", value: customUrls)
    }

    // MARK: - Data Reset
    func clearAllData() {
        let fileNames = [
            "terms", "feed_items", "saved_pages", "custom_urls",
            "subscribed_platforms", "oshi_avatars", "oshi_compositions", "hidden_items"
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

        terms = [term]
        feedItems = [feedItem]
        savedPages = [savedPage]
        customUrls = [customUrl]
        subscribedPlatforms = ["news", "youtube", "tver", "custom"]
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
