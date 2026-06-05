import Foundation
import Combine

class LocalDB: ObservableObject {
    static let shared = LocalDB()
    
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
    
    private let queue = DispatchQueue(label: "com.otterlymavis.oshireader.db", qos: .userInitiated)
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    
    private init() {
        loadAll()
    }
    
    // MARK: - File Paths
    private func fileURL(for name: String) -> URL {
        let paths = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)
        return paths[0].appendingPathComponent("\(name).json")
    }
    
    // MARK: - Load and Save Helpers
    private func loadAll() {
        self.terms = loadFromFile(name: "terms", defaultValue: [])
        self.feedItems = loadFromFile(name: "feed_items", defaultValue: [])
        self.savedPages = loadFromFile(name: "saved_pages", defaultValue: [])
        self.customUrls = loadFromFile(name: "custom_urls", defaultValue: [])
        self.subscribedPlatforms = loadFromFile(name: "subscribed_platforms", defaultValue: [
            "youtube", "niconico", "tver", "note",
            "girlschannel", "5ch", "togetter", "news", "custom",
            "yahoonews", "mdpr", "oricon", "twitter"
        ])
        var didAddMissingPlatforms = false
        for platform in ["oricon", "twitter", "mdpr", "yahoonews", "togetter"] where !self.subscribedPlatforms.contains(platform) {
            self.subscribedPlatforms.append(platform)
            didAddMissingPlatforms = true
        }
        if didAddMissingPlatforms {
            saveToFile(name: "subscribed_platforms", value: self.subscribedPlatforms)
        }
        self.wallpaper = UserDefaults.standard.string(forKey: "wallpaper_url")
        self.sourcesOrder = UserDefaults.standard.stringArray(forKey: "sources_order")
        self.oshiAvatars = loadFromFile(name: "oshi_avatars", defaultValue: [:])
        self.compositions = loadFromFile(name: "oshi_compositions", defaultValue: [:])
        let hiddenArray: [String] = loadFromFile(name: "hidden_items", defaultValue: [])
        self.hiddenItems = Set(hiddenArray)
    }
    
    private func loadFromFile<T: Decodable>(name: String, defaultValue: T) -> T {
        let url = fileURL(for: name)
        guard FileManager.default.fileExists(atPath: url.path) else { return defaultValue }
        do {
            let data = try Data(contentsOf: url)
            return try decoder.decode(T.self, from: data)
        } catch {
            print("Error loading \(name): \(error)")
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
                print("Error saving \(name): \(error)")
            }
        }
    }
    
    private func runOnMain(_ block: @escaping () -> Void) {
        if Thread.isMainThread {
            block()
        } else {
            DispatchQueue.main.async {
                block()
            }
        }
    }
    
    // MARK: - Watch Terms
    func saveTerm(keyword: String, collectionMode: String = "all_info") -> WatchTerm {
        let term = WatchTerm(keyword: keyword.trimmingCharacters(in: .whitespacesAndNewlines), collection_mode: collectionMode)
        runOnMain {
            self.terms.insert(term, at: 0)
            self.saveToFile(name: "terms", value: self.terms)
        }
        return term
    }
    
    func updateTerm(id: String, isActive: Bool? = nil, collectionMode: String? = nil, notifyOnNew: Bool? = nil) {
        runOnMain {
            if let idx = self.terms.firstIndex(where: { $0.id == id }) {
                var term = self.terms[idx]
                if let isActive = isActive { term.is_active = isActive }
                if let collectionMode = collectionMode { term.collection_mode = collectionMode }
                if let notifyOnNew = notifyOnNew { term.notify_on_new = notifyOnNew }
                self.terms[idx] = term
                self.saveToFile(name: "terms", value: self.terms)
            }
        }
    }

    func addTermFromBackend(_ term: WatchTerm) {
        runOnMain {
            self.terms.insert(term, at: 0)
            self.saveToFile(name: "terms", value: self.terms)
        }
    }

    func replaceTerm(localId: String, with serverTerm: WatchTerm) {
        runOnMain {
            if let idx = self.terms.firstIndex(where: { $0.id == localId }) {
                self.terms[idx] = serverTerm
                self.saveToFile(name: "terms", value: self.terms)
            }
        }
    }
    
    func deleteTerm(id: String) {
        runOnMain {
            if let term = self.terms.firstIndex(where: { $0.id == id }) {
                let keyword = self.terms[term].keyword
                self.terms.remove(at: term)
                self.saveToFile(name: "terms", value: self.terms)
                
                // Also clean up items containing that watch term keyword
                self.feedItems.removeAll(where: { $0.watch_term_keyword == keyword })
                self.saveToFile(name: "feed_items", value: self.feedItems)
            }
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
        for item in self.feedItems {
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
                    (existing.title == nil || existing.title!.contains("...") || item.title!.count > existing.title!.count + 8)
                
                let merged = FeedItem(
                    id: existing.id,
                    platform: existing.platform,
                    url: existing.url,
                    title: shouldReplaceTitle ? item.title : existing.title,
                    content_text: item.content_text ?? existing.content_text,
                    author: item.author ?? existing.author,
                    thumbnail_url: item.thumbnail_url ?? existing.thumbnail_url,
                    media_type: existing.media_type,
                    published_at: min(existing.published_at, item.published_at),
                    watch_term_keyword: existing.watch_term_keyword,
                    fetched_at: item.fetched_at
                )
                currentMap[key] = merged
            }
        }
        
        let sorted = currentMap.values.sorted(by: { $0.published_at > $1.published_at })
        let finalItems = Array(sorted.prefix(600)) // Replicate MAX_ITEMS = 600
        
        runOnMain {
            self.feedItems = finalItems
            self.saveToFile(name: "feed_items", value: self.feedItems)
        }

        if !addedItems.isEmpty {
            let terms = self.terms
            Task {
                await NotificationManager.shared.notifyForNewItems(addedItems, terms: terms)
            }
        }
        
        return addedCount
    }
    
    func deleteFeedItem(id: String, watchTermKeyword: String) {
        let key = "\(id)::\(watchTermKeyword)"
        runOnMain {
            self.hiddenItems.insert(key)
            self.saveToFile(name: "hidden_items", value: Array(self.hiddenItems))
            
            self.feedItems.removeAll(where: { $0.id == id && $0.watch_term_keyword == watchTermKeyword })
            self.saveToFile(name: "feed_items", value: self.feedItems)
        }
    }
    
    // MARK: - Query Feed (Filtering)
    func queryFeed(keyword: String?, days: Int) -> [FeedItem] {
        let now = Date()
        let cutoffDate = Calendar.current.date(byAdding: .day, value: -days, to: now)
        let formatter = ISO8601DateFormatter()
        let cutoffString = cutoffDate != nil ? formatter.string(from: cutoffDate!) : nil
        
        let strictKeywordPlatforms = Set(["mdpr", "news", "tver"])
        
        return feedItems.filter { item in
            let key = "\(item.id)::\(item.watch_term_keyword)"
            if hiddenItems.contains(key) { return false }
            
            // Search pages fallbacks
            if item.id.contains("search:") || item.title?.lowercased().contains("search:") == true { return false }
            
            // Bare address item (Yahoo News fallback checking)
            if item.platform == "yahoonews" && (item.title?.contains("https://") == true || item.content_text?.contains("https://") == true) {
                return false
            }
            
            // Cutoff check (skip limit check for 5ch, girlschannel, togetter)
            let skipCutoff = item.platform == "5ch" || item.platform == "girlschannel" || item.platform == "togetter"
            if let cutoff = cutoffString, item.published_at < cutoff && !skipCutoff {
                return false
            }
            
            // Keyword filter
            if let kw = keyword, !kw.isEmpty {
                if item.platform == "custom" {
                    // Let custom pages pass if custom matches
                } else if item.watch_term_keyword != kw {
                    return false
                }
            }
            
            // Strict keyword matching logic
            if strictKeywordPlatforms.contains(item.platform), !item.watch_term_keyword.isEmpty {
                if !matchesKeyword(item: item, kw: item.watch_term_keyword) {
                    return false
                }
            }
            
            // Subscribed platforms
            let platformKey = normalizedPlatformKey(item.platform)
            if !subscribedPlatforms.contains(platformKey) {
                return false
            }
            
            return true
        }
        .sorted(by: { $0.published_at > $1.published_at })
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
        var isSaved = false
        runOnMain {
            if let idx = self.savedPages.firstIndex(where: { $0.id == item.id }) {
                self.savedPages.remove(at: idx)
            } else {
                let page = SavedPage(
                    id: item.id,
                    url: item.url,
                    title: item.title,
                    platform: item.platform,
                    saved_at: ISO8601DateFormatter().string(from: Date())
                )
                self.savedPages.insert(page, at: 0)
                isSaved = true
            }
            self.saveToFile(name: "saved_pages", value: self.savedPages)
        }
        return isSaved
    }
    
    func removeSaved(id: String) {
        runOnMain {
            self.savedPages.removeAll(where: { $0.id == id })
            self.saveToFile(name: "saved_pages", value: self.savedPages)
        }
    }
    
    // MARK: - Subscribed Platforms
    func setSubscribedPlatforms(platforms: [String]) {
        runOnMain {
            self.subscribedPlatforms = platforms
            self.saveToFile(name: "subscribed_platforms", value: self.subscribedPlatforms)
        }
    }
    
    // MARK: - Custom URLs
    func addCustomUrl(url: String, title: String) {
        let trimmed = url.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalized = trimmed.lowercased().hasPrefix("http://") || trimmed.lowercased().hasPrefix("https://") ? trimmed : "https://\(trimmed)"
        let id = "custom:\(normalized.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed)?.prefix(60) ?? "")"
        runOnMain {
            if self.customUrls.contains(where: { $0.id == id }) { return }
            let trimmedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
            let entry = CustomUrl(id: id, url: normalized, title: trimmedTitle.isEmpty ? nil : trimmedTitle, added_at: ISO8601DateFormatter().string(from: Date()))
            self.customUrls.insert(entry, at: 0)
            self.saveToFile(name: "custom_urls", value: self.customUrls)
        }
    }
    
    func removeCustomUrl(id: String) {
        runOnMain {
            self.customUrls.removeAll(where: { $0.id == id })
            self.saveToFile(name: "custom_urls", value: self.customUrls)
        }
    }

    // MARK: - Data Reset
    func clearAllData() {
        let fileNames = [
            "terms",
            "feed_items",
            "saved_pages",
            "custom_urls",
            "subscribed_platforms",
            "oshi_avatars",
            "oshi_compositions",
            "hidden_items"
        ]

        runOnMain {
            self.terms = []
            self.feedItems = []
            self.savedPages = []
            self.customUrls = []
            self.subscribedPlatforms = [
                "youtube", "niconico", "tver", "note",
                "girlschannel", "5ch", "togetter", "news", "custom",
                "yahoonews", "mdpr", "oricon", "twitter"
            ]
            self.wallpaper = nil
            self.sourcesOrder = nil
            self.oshiAvatars = [:]
            self.compositions = [:]
            self.hiddenItems = []

            for name in fileNames {
                let url = self.fileURL(for: name)
                if FileManager.default.fileExists(atPath: url.path) {
                    try? FileManager.default.removeItem(at: url)
                }
            }
            UserDefaults.standard.removeObject(forKey: "wallpaper_url")
            UserDefaults.standard.removeObject(forKey: "sources_order")
            self.saveToFile(name: "subscribed_platforms", value: self.subscribedPlatforms)
        }
    }
    
    // MARK: - Wallpaper & Custom Order (UserDefaults)
    func setWallpaper(url: String?) {
        runOnMain {
            self.wallpaper = url
            if let url = url {
                UserDefaults.standard.set(url, forKey: "wallpaper_url")
            } else {
                UserDefaults.standard.removeObject(forKey: "wallpaper_url")
            }
        }
    }
    
    func setSourcesOrder(order: [String]) {
        runOnMain {
            self.sourcesOrder = order
            UserDefaults.standard.set(order, forKey: "sources_order")
        }
    }
    
    // MARK: - Oshi Avatars & Compositions
    func setOshiAvatar(keyword: String, imageUrl: String) {
        runOnMain {
            self.oshiAvatars[keyword] = imageUrl
            self.saveToFile(name: "oshi_avatars", value: self.oshiAvatars)
        }
    }
    
    func setOshiComposition(keyword: String, layers: [AvatarLayer]) {
        runOnMain {
            self.compositions[keyword] = layers
            self.saveToFile(name: "oshi_compositions", value: self.compositions)
        }
    }

    // MARK: - UI Test Fixture
    func resetForUITesting() {
        guard ProcessInfo.processInfo.arguments.contains("--uitesting") else { return }

        let now = ISO8601DateFormatter().string(from: Date())
        let term = WatchTerm(id: "ui-term-oshitest", keyword: "UITest Oshi", collection_mode: "all_info", is_active: true, created_at: now)
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

        runOnMain {
            self.terms = [term]
            self.feedItems = [feedItem]
            self.savedPages = [savedPage]
            self.customUrls = [customUrl]
            self.subscribedPlatforms = ["news", "youtube", "tver", "custom"]
            self.wallpaper = nil
            self.sourcesOrder = nil
            self.oshiAvatars = [:]
            self.compositions = [term.keyword: [layer]]
            self.hiddenItems = []
            // Do NOT persist fixture data — only seed in-memory so nothing stains the
            // container after the test process exits.
            UserDefaults.standard.removeObject(forKey: "wallpaper_url")
            UserDefaults.standard.removeObject(forKey: "sources_order")
        }
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
        if FileManager.default.fileExists(atPath: url.path) {
            try? FileManager.default.removeItem(at: url)
        }
    }
    
    // MARK: - Stats
    func getStats() -> (total: Int, byPlatform: [String: Int]) {
        var counts = [String: Int]()
        for item in feedItems {
            let key = normalizedPlatformKey(item.platform)
            counts[key] = (counts[key] ?? 0) + 1
        }
        return (feedItems.count, counts)
    }

    private func normalizedPlatformKey(_ platform: String) -> String {
        if platform == "news:mdpr" { return "mdpr" }
        if platform == "news:yahoo_ent" { return "yahoonews" }
        if platform == "news" || platform.hasPrefix("news:") { return "news" }
        return platform
    }
}
