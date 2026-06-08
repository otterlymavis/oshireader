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
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    // Bump this whenever a migration step is added below.
    private static let currentSchemaVersion = 1
    private static let schemaVersionKey = "localdb_schema_version"

    init(directory: URL) {
        self.storeDirectory = directory
        loadAll()
        runMigrationsIfNeeded()
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
        default:
            AppLogger.persistence.warning("No migration handler for schema v\(version)")
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
        queue.async {
            do {
                let data = try self.encoder.encode(value)
                try data.write(to: url, options: [.atomic])
            } catch {
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
    func mergeItems(newItems: [FeedItem]) -> Int {
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

        let sorted = currentMap.values.sorted(by: {
            (parseISO8601Date($0.published_at) ?? .distantPast) >
            (parseISO8601Date($1.published_at) ?? .distantPast)
        })
        let finalItems = Array(sorted.prefix(600)) // Replicate MAX_ITEMS = 600

        // Only notify for items that survived the cap — avoids pinging for articles
        // that were immediately evicted as too old.
        if !addedItems.isEmpty {
            let survivedKeys = Set(finalItems.map { itemKey($0) })
            let notifyItems = addedItems.filter { survivedKeys.contains(itemKey($0)) }
            if !notifyItems.isEmpty {
                let terms = self.terms
                Task {
                    await NotificationManager.shared.notifyForNewItems(notifyItems, terms: terms)
                }
            }
        }

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

    // MARK: - Query Feed (Filtering)
    func queryFeed(keyword: String?, days: Int) -> [FeedItem] {
        let now = Date()
        // days == 0 means "All Time" — no cutoff applied
        let cutoffDate = days > 0 ? Calendar.current.date(byAdding: .day, value: -days, to: now) : nil

        return feedItems.filter { item in
            let key = "\(item.id)::\(item.watch_term_keyword)"
            if hiddenItems.contains(key) { return false }

            // Search pages fallbacks
            if item.id.contains("search:") || item.title?.lowercased().contains("search:") == true { return false }

            // Bare address item (Yahoo News fallback checking)
            if item.platform == "yahoonews" && (item.title?.contains("https://") == true || item.content_text?.contains("https://") == true) {
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
                let term = terms.first(where: { $0.keyword == item.watch_term_keyword })
                let candidates = [item.watch_term_keyword] + (term?.aliases ?? [])
                if !candidates.contains(where: { matchesKeyword(item: item, kw: $0) }) {
                    return false
                }
            }

            // Subscribed platforms
            let platformKey = Platform.normalize(item.platform)
            if !subscribedPlatforms.contains(platformKey) {
                return false
            }

            return true
        }
        .sorted(by: {
            (parseISO8601Date($0.published_at) ?? .distantPast) >
            (parseISO8601Date($1.published_at) ?? .distantPast)
        })
        .reduce(into: ([FeedItem](), Set<String>())) { acc, item in
            // When no keyword filter, deduplicate by URL — the same article can be
            // stored once per matching watch term; only the first (most recent) copy
            // is shown. When filtering by a specific keyword, all matches are shown.
            guard keyword == nil else { acc.0.append(item); return }
            if acc.1.insert(item.url).inserted { acc.0.append(item) }
        }.0
    }

    private func matchesKeyword(item: FeedItem, kw: String) -> Bool {
        let haystack = "\(item.title ?? "") \(item.content_text ?? "")".lowercased()
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
                saved_at: ISO8601DateFormatter().string(from: Date())
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
        let entry = CustomUrl(id: id, url: normalized, title: trimmedTitle.isEmpty ? nil : trimmedTitle, added_at: ISO8601DateFormatter().string(from: Date()))
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

        let now = ISO8601DateFormatter().string(from: Date())
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
        let name = "cache_\(id.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? id)"
        saveToFile(name: name, value: html)
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
