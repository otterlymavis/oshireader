import Foundation
import UIKit

struct IrasutoyaImage: Codable, Identifiable, Hashable {
    var id: String { url }
    let url: String
    let thumb: String
    let title: String
}

class NetworkManager {
    static let shared = NetworkManager()
    
    private init() {}

    private var isUITesting: Bool {
        ProcessInfo.processInfo.arguments.contains("--uitesting")
    }
    
    // MARK: - Backend URL Config
    private let fallbackProductionAPIBase = "https://oshireader.onrender.com"

    var apiBase: String {
        configuredBundleValue(forKey: "OshiReaderAPIBaseURL") ?? fallbackProductionAPIBase
    }

    var environmentName: String {
        configuredBundleValue(forKey: "OshiReaderEnvironment") ?? "Production"
    }

    private func configuredBundleValue(forKey key: String) -> String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: key) as? String else {
            return nil
        }

        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !trimmed.hasPrefix("$(") else {
            return nil
        }
        return trimmed
    }

    var adminApiToken: String? {
        if let token = UserDefaults.standard.string(forKey: "admin_api_token"),
           !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return token
        }
        let envToken = ProcessInfo.processInfo.environment["OSHI_READER_ADMIN_API_TOKEN"] ?? ""
        let trimmed = envToken.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private var apnsEnvironment: String {
        #if DEBUG
        return "sandbox"
        #else
        return "production"
        #endif
    }

    private func applyAdminAuthorization(to request: inout URLRequest) {
        guard let adminApiToken else { return }
        request.setValue("Bearer \(adminApiToken)", forHTTPHeaderField: "Authorization")
    }
    
    // MARK: - Fetch Watch Terms
    func fetchWatchTerms() async throws -> [WatchTerm] {
        let url = URL(string: "\(apiBase)/api/watch-terms/")!
        let (data, response) = try await URLSession.shared.data(from: url)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode([WatchTerm].self, from: data)
    }

    // MARK: - Sync Local Terms to Backend
    // Pushes any local watch terms that are missing from the backend (e.g. after a database reset).
    func syncWatchTermsToBackend(localTerms: [WatchTerm]) async {
        guard !isUITesting, !localTerms.isEmpty else { return }
        guard let backendTerms = try? await fetchWatchTerms() else { return }
        let backendKeywords = Set(backendTerms.map { $0.keyword })
        for term in localTerms where !backendKeywords.contains(term.keyword) {
            if let serverTerm = try? await createWatchTerm(keyword: term.keyword, collectionMode: term.collection_mode) {
                LocalDB.shared.replaceTerm(localId: term.id, with: serverTerm)
            }
        }
    }
    
    // MARK: - Create Watch Term
    func createWatchTerm(keyword: String, collectionMode: String) async throws -> WatchTerm {
        if isUITesting {
            return WatchTerm(keyword: keyword, collection_mode: collectionMode)
        }

        let url = URL(string: "\(apiBase)/api/watch-terms/")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        applyAdminAuthorization(to: &request)
        
        let body: [String: String] = [
            "keyword": keyword,
            "collection_mode": collectionMode
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(WatchTerm.self, from: data)
    }
    
    // MARK: - Update Watch Term
    func updateWatchTerm(id: String, isActive: Bool? = nil, collectionMode: String? = nil, notifyOnNew: Bool? = nil) async throws -> WatchTerm {
        if isUITesting {
            throw URLError(.cancelled)
        }

        let url = URL(string: "\(apiBase)/api/watch-terms/\(id)")!
        var request = URLRequest(url: url)
        request.httpMethod = "PATCH"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        applyAdminAuthorization(to: &request)
        
        var body: [String: Any] = [:]
        if let isActive = isActive { body["is_active"] = isActive }
        if let collectionMode = collectionMode { body["collection_mode"] = collectionMode }
        if let notifyOnNew = notifyOnNew { body["notify_on_new"] = notifyOnNew }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(WatchTerm.self, from: data)
    }
    
    // MARK: - Delete Watch Term
    func deleteWatchTerm(id: String) async throws {
        if isUITesting {
            return
        }

        let url = URL(string: "\(apiBase)/api/watch-terms/\(id)")!
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        applyAdminAuthorization(to: &request)
        
        let (_, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }
    
    // MARK: - Fetch Feed
    func fetchFeed(termId: Int? = nil, platform: String? = nil, limit: Int = 50) async throws -> [FeedItem] {
        if isUITesting {
            return []
        }

        var components = URLComponents(string: "\(apiBase)/api/feed/")!
        var queryItems: [URLQueryItem] = [URLQueryItem(name: "limit", value: String(limit))]
        if let termId = termId {
            queryItems.append(URLQueryItem(name: "term_id", value: String(termId)))
        }
        if let platform = platform {
            queryItems.append(URLQueryItem(name: "platform", value: platform))
        }
        components.queryItems = queryItems
        
        let (data, response) = try await URLSession.shared.data(from: components.url!)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        
        let backendItems = try JSONDecoder().decode([BackendFeedItem].self, from: data)
        return backendItems.map { $0.toFeedItem() }
    }
    
    // MARK: - Fetch Credentials
    func fetchCredentials() async throws -> [Credential] {
        let url = URL(string: "\(apiBase)/api/credentials/")!
        var request = URLRequest(url: url)
        applyAdminAuthorization(to: &request)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode([Credential].self, from: data)
    }

    func updateCredential(platform: String, apiKey: String? = nil, bearerToken: String? = nil) async throws -> Credential {
        let url = URL(string: "\(apiBase)/api/credentials/\(platform)")!
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        applyAdminAuthorization(to: &request)

        var body = [String: String]()
        if let apiKey {
            body["api_key"] = apiKey
        }
        if let bearerToken {
            body["bearer_token"] = bearerToken
        }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(Credential.self, from: data)
    }

    // MARK: - APNs Device Token
    func registerAPNSDeviceToken(_ token: String) async throws {
        let url = URL(string: "\(apiBase)/api/devices/apns-token")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let deviceId = await MainActor.run {
            UIDevice.current.identifierForVendor?.uuidString ?? ""
        }

        let body: [String: String] = [
            "token": token,
            "environment": apnsEnvironment,
            "device_id": deviceId
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (_, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }
    
    // MARK: - Check Health
    func checkHealth() async throws -> Bool {
        let url = URL(string: "\(apiBase)/api/health")!
        let (data, response) = try await URLSession.shared.data(from: url)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            return false
        }
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let status = json["status"] as? String, status == "ok" {
            return true
        }
        return false
    }
    
    // MARK: - Trigger Scraper Polling
    func triggerPoll() async throws {
        if isUITesting {
            return
        }

        let url = URL(string: "\(apiBase)/api/admin/poll")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        applyAdminAuthorization(to: &request)
        
        let (_, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }
    
    // MARK: - Google Translate Helper
    func translateToJapanese(_ text: String) async -> String {
        // Checks if contains Japanese characters
        let isJapanese = text.range(of: "\\p{Hiragana}|\\p{Katakana}|\\p{Han}", options: .regularExpression) != nil
        if isJapanese { return text }
        
        let query = text.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? text
        let urlString = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ja&dt=t&q=\(query)"
        guard let url = URL(string: urlString) else { return text }
        
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            if let json = try? JSONSerialization.jsonObject(with: data) as? [Any],
               let firstArray = json.first as? [Any] {
                var translatedText = ""
                for chunk in firstArray {
                    if let chunkArray = chunk as? [Any], let string = chunkArray.first as? String {
                        translatedText += string
                    }
                }
                return translatedText.isEmpty ? text : translatedText
            }
        } catch {
            print("Translation failed: \(error)")
        }
        return text
    }
    
    // MARK: - Irasutoya popular / search
    func getPopularIrasutoya() async throws -> [IrasutoyaImage] {
        let feed1 = "https://www.irasutoya.com/feeds/posts/default?alt=json&max-results=20"
        let categoryUrl = "https://www.irasutoya.com/feeds/posts/default/-/\(URLQueryItem(name: "", value: "人物").value!.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")?alt=json&max-results=15"
        
        let items1 = try await fetchBloggerFeed(feed1)
        let items2 = (try? await fetchBloggerFeed(categoryUrl)) ?? []
        
        var combined = [IrasutoyaImage]()
        var seen = Set<String>()
        
        for item in items1 + items2 {
            if !seen.contains(item.url) && combined.count < 30 {
                seen.insert(item.url)
                combined.append(item)
            }
        }
        return combined
    }
    
    func searchIrasutoya(query: String) async throws -> [IrasutoyaImage] {
        let jaQuery = await translateToJapanese(query)
        let escaped = jaQuery.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? jaQuery
        
        let feedUrl1 = "https://www.irasutoya.com/feeds/posts/default?alt=json&q=\(escaped)&max-results=36&start-index=1"
        let feedUrl2 = "https://www.irasutoya.com/feeds/posts/default?alt=json&q=\(escaped)&max-results=36&start-index=37"
        
        let items1 = (try? await fetchBloggerFeed(feedUrl1)) ?? []
        let items2 = (try? await fetchBloggerFeed(feedUrl2)) ?? []
        
        var combined = [IrasutoyaImage]()
        var seen = Set<String>()
        
        for item in items1 + items2 {
            if !seen.contains(item.url) && combined.count < 72 {
                seen.insert(item.url)
                combined.append(item)
            }
        }
        return combined
    }
    
    private func fetchBloggerFeed(_ urlString: String) async throws -> [IrasutoyaImage] {
        guard let url = URL(string: urlString) else { return [] }
        var request = URLRequest(url: url)
        request.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1", forHTTPHeaderField: "User-Agent")
        
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            return []
        }
        
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let feed = json["feed"] as? [String: Any],
              let entries = feed["entry"] as? [[String: Any]] else {
            return []
        }
        
        var list = [IrasutoyaImage]()
        for entry in entries {
            let titleContainer = entry["title"] as? [String: Any]
            let title = titleContainer?["$t"] as? String ?? ""
            
            let links = entry["link"] as? [[String: Any]] ?? []
            let altLink = links.first(where: { ($0["rel"] as? String) == "alternate" })?["href"] as? String ?? ""
            
            var thumb = ""
            if let mediaThumb = entry["media$thumbnail"] as? [String: Any] {
                thumb = mediaThumb["url"] as? String ?? ""
            }
            if thumb.isEmpty, let contentContainer = entry["content"] as? [String: Any],
               let contentHtml = contentContainer["$t"] as? String {
                let pattern = #"src="(https?://[^"]+\.(?:png|jpg|jpeg|gif))""#
                if let regex = try? NSRegularExpression(pattern: pattern, options: .caseInsensitive),
                   let match = regex.firstMatch(in: contentHtml, range: NSRange(contentHtml.startIndex..., in: contentHtml)) {
                    if let range = Range(match.range(at: 1), in: contentHtml) {
                        thumb = String(contentHtml[range])
                    }
                }
            }
            
            if !altLink.isEmpty && !thumb.isEmpty {
                // Upscale small Blogger thumbnails for the editor picker.
                let upscaled = thumb
                    .replacingOccurrences(of: "/s72-c/", with: "/s400-c/")
                    .replacingOccurrences(of: "/s72-c$", with: "/s400-c", options: .regularExpression)
                    .replacingOccurrences(of: "/s1600/", with: "/s400/")
                list.append(IrasutoyaImage(url: altLink, thumb: upscaled, title: title))
            }
        }
        return list
    }
    
    // MARK: - Local RSS Parsing Fallback
    func scrapeRSSFallback(keyword: String) async -> [FeedItem] {
        var results = [FeedItem]()
        let nowString = ISO8601DateFormatter().string(from: Date())
        
        // NHK general RSS
        if let nhkUrl = URL(string: "https://www3.nhk.or.jp/rss/news/cat7.xml"),
           let nhkItems = try? await parseRss(url: nhkUrl) {
            for item in nhkItems {
                let match = matchesKeyword(title: item.title, desc: item.description, kw: keyword)
                if match {
                    let cleanedTitle = cleanNewsTitle(item.title)
                    results.append(FeedItem(
                        id: "news:nhk:\(UUID().uuidString)",
                        platform: "news",
                        url: item.link,
                        title: cleanedTitle,
                        content_text: item.description.isEmpty ? nil : item.description,
                        author: "NHK",
                        thumbnail_url: nil,
                        media_type: "article",
                        published_at: item.pubDate ?? nowString,
                        watch_term_keyword: keyword,
                        fetched_at: nowString
                    ))
                }
            }
        }
        
        // Google News RSS search
        let encodedKeyword = keyword.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? keyword
        if let gnewsUrl = URL(string: "https://news.google.com/rss/search?q=\(encodedKeyword)&hl=ja&gl=JP&ceid=JP%3Aja"),
           let gnewsItems = try? await parseRss(url: gnewsUrl) {
            for item in gnewsItems {
                let cleanedTitle = cleanNewsTitle(item.title)
                results.append(FeedItem(
                    id: "news:gnews:\(UUID().uuidString)",
                    platform: "news",
                    url: item.link,
                    title: cleanedTitle,
                    content_text: item.description.isEmpty ? nil : item.description,
                    author: "Google News",
                    thumbnail_url: nil,
                    media_type: "article",
                    published_at: item.pubDate ?? nowString,
                    watch_term_keyword: keyword,
                    fetched_at: nowString
                ))
            }
        }
        
        return results
    }

    // MARK: - Custom URL Scraping
    func scrapeCustomUrls(_ urls: [CustomUrl]) async -> [FeedItem] {
        guard !urls.isEmpty else { return [] }

        return await withTaskGroup(of: FeedItem?.self) { group in
            for entry in urls {
                group.addTask {
                    await self.scrapeCustomUrl(entry)
                }
            }

            var results = [FeedItem]()
            for await item in group {
                if let item {
                    results.append(item)
                }
            }
            return results
        }
    }

    private func scrapeCustomUrl(_ entry: CustomUrl) async -> FeedItem? {
        let normalized = normalizedCustomUrl(entry.url)
        guard let url = URL(string: normalized) else { return nil }

        let nowString = ISO8601DateFormatter().string(from: Date())
        var title = entry.title?.trimmingCharacters(in: .whitespacesAndNewlines)
        var description: String?

        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 12
            request.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", forHTTPHeaderField: "User-Agent")

            let (data, _) = try await URLSession.shared.data(for: request)
            if let html = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .shiftJIS) {
                title = extractTagContent(named: "title", from: html) ?? title
                description = extractMetaDescription(from: html)
            }
        } catch {
            print("Custom URL scrape failed for \(entry.url): \(error)")
        }

        return FeedItem(
            id: entry.id,
            platform: "custom",
            url: normalized,
            title: title?.isEmpty == false ? title : normalized,
            content_text: description?.isEmpty == false ? description : nil,
            author: URL(string: normalized)?.host,
            thumbnail_url: nil,
            media_type: "article",
            published_at: entry.added_at,
            watch_term_keyword: "",
            fetched_at: nowString
        )
    }

    private func normalizedCustomUrl(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.lowercased().hasPrefix("http://") || trimmed.lowercased().hasPrefix("https://") {
            return trimmed
        }
        return "https://\(trimmed)"
    }

    private func extractTagContent(named tag: String, from html: String) -> String? {
        let pattern = #"<\#(tag)[^>]*>([^<]{1,240})</\#(tag)>"#
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive, .dotMatchesLineSeparators]),
              let match = regex.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
              let range = Range(match.range(at: 1), in: html) else {
            return nil
        }
        return cleanDisplayText(String(html[range]))
    }

    private func extractMetaDescription(from html: String) -> String? {
        let patterns = [
            #"<meta[^>]+name=["']description["'][^>]+content=["']([^"']{1,360})["'][^>]*>"#,
            #"<meta[^>]+content=["']([^"']{1,360})["'][^>]+name=["']description["'][^>]*>"#,
            #"<meta[^>]+property=["']og:description["'][^>]+content=["']([^"']{1,360})["'][^>]*>"#,
            #"<meta[^>]+content=["']([^"']{1,360})["'][^>]+property=["']og:description["'][^>]*>"#
        ]

        for pattern in patterns {
            guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]),
                  let match = regex.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
                  let range = Range(match.range(at: 1), in: html) else {
                continue
            }
            return cleanDisplayText(String(html[range]))
        }
        return nil
    }
    
    // MARK: - Consolidated Local Fallback (runs all platform scrapers in parallel)
    func scrapeLocalFallbacks(keyword: String) async -> [FeedItem] {
        return await withTaskGroup(of: [FeedItem].self) { group in
            group.addTask { await self.scrapeRSSFallback(keyword: keyword) }
            group.addTask { await self.scrapeNiconicoRSS(keyword: keyword) }
            group.addTask { await self.scrapeGoogleNewsSite(keyword: keyword, site: "news.yahoo.co.jp", platform: "yahoonews") }
            group.addTask { await self.scrapeGoogleNewsSite(keyword: keyword, site: "mdpr.jp", platform: "mdpr") }
            group.addTask { await self.scrapeGoogleNewsSite(keyword: keyword, site: "oricon.co.jp", platform: "oricon") }

            var all = [FeedItem]()
            for await items in group {
                all.append(contentsOf: items)
            }
            return all
        }
    }

    // MARK: - NicoNico via Google News (tag RSS returns 403)
    func scrapeNiconicoRSS(keyword: String) async -> [FeedItem] {
        return await scrapeGoogleNewsSite(keyword: keyword, site: "nicovideo.jp", platform: "niconico")
    }

    // MARK: - Google News Site Filter RSS
    func scrapeGoogleNewsSite(keyword: String, site: String, platform: String) async -> [FeedItem] {
        let query = "\(keyword) site:\(site)"
        let encoded = query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? query
        guard let url = URL(string: "https://news.google.com/rss/search?q=\(encoded)&hl=ja&gl=JP&ceid=JP%3Aja") else { return [] }

        let nowString = ISO8601DateFormatter().string(from: Date())

        guard let items = try? await parseRss(url: url) else { return [] }

        return items.compactMap { item in
            guard !item.link.isEmpty else { return nil }
            let cleanedTitle = cleanNewsTitle(item.title)
            return FeedItem(
                id: "\(platform):gnews:\(stableIdHash(item.link))",
                platform: platform,
                url: item.link,
                title: cleanedTitle.isEmpty ? nil : cleanedTitle,
                content_text: item.description.isEmpty ? nil : item.description,
                author: nil,
                thumbnail_url: nil,
                media_type: "article",
                published_at: item.pubDate ?? nowString,
                watch_term_keyword: keyword,
                fetched_at: nowString
            )
        }
    }

    private func stableIdHash(_ input: String) -> String {
        var v: UInt64 = 14695981039346656037
        for b in input.utf8 {
            v ^= UInt64(b)
            v = v &* 1099511628211
        }
        return String(v)
    }

    private func parseRss(url: URL) async throws -> [RssItem] {
        let (data, _) = try await URLSession.shared.data(from: url)
        let parser = XMLParser(data: data)
        let delegate = RSSParserDelegate()
        parser.delegate = delegate
        parser.parse()
        return delegate.items
    }
    
    private func cleanNewsTitle(_ title: String) -> String {
        return title
            .replacingOccurrences(of: "\\s*[-|]\\s*Yahoo!ニュース\\s*$", with: "", options: .regularExpression, range: nil)
            .replacingOccurrences(of: "\\s*\\([^)]*ニュース\\)\\s*[-|]\\s*Yahoo!ニュース\\s*$", with: "", options: .regularExpression, range: nil)
            .replacingOccurrences(of: "\\s*[-|]\\s*(?:Bing|Google)\\s*$", with: "", options: .regularExpression, range: nil)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
    
    private func matchesKeyword(title: String, desc: String, kw: String) -> Bool {
        let haystack = "\(title) \(desc)".lowercased()
        let needle = kw.lowercased()
        if needle.isEmpty { return true }
        if haystack.contains(needle) { return true }
        let parts = kw.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }
        if parts.count > 1 {
            return parts.allSatisfy { haystack.contains($0.lowercased()) }
        }
        return false
    }
}

// MARK: - RSS XML Parser Helper
struct RssItem {
    var title: String = ""
    var link: String = ""
    var description: String = ""
    var pubDate: String? = nil
    var thumbnailUrl: String? = nil
}

class RSSParserDelegate: NSObject, XMLParserDelegate {
    var items = [RssItem]()
    private var currentElement = ""
    private var currentItem: RssItem? = nil

    private var currentTitle = ""
    private var currentLink = ""
    private var currentDescription = ""
    private var currentPubDate = ""
    private var currentThumbnailUrl: String? = nil

    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?, qualifiedName qName: String?, attributes attributeDict: [String : String] = [:]) {
        currentElement = elementName
        if elementName == "item" || elementName == "entry" {
            currentItem = RssItem()
            currentTitle = ""
            currentLink = ""
            currentDescription = ""
            currentPubDate = ""
            currentThumbnailUrl = nil
        }
        if currentItem != nil {
            if elementName == "media:thumbnail" || elementName == "media:content" {
                if let url = attributeDict["url"], currentThumbnailUrl == nil {
                    currentThumbnailUrl = url
                }
            }
            if elementName == "enclosure",
               let url = attributeDict["url"],
               attributeDict["type"]?.hasPrefix("image") == true,
               currentThumbnailUrl == nil {
                currentThumbnailUrl = url
            }
        }
    }
    
    func parser(_ parser: XMLParser, foundCharacters string: String) {
        let cleaned = string.trimmingCharacters(in: .newlines)
        guard !cleaned.isEmpty else { return }
        
        switch currentElement {
        case "title":
            currentTitle += string
        case "link":
            currentLink += cleaned
        case "description", "summary":
            currentDescription += string
        case "pubDate", "published", "dc:date":
            currentPubDate += cleaned
        default:
            break
        }
    }
    
    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?, qualifiedName qName: String?) {
        if elementName == "item" || elementName == "entry" {
            if var item = currentItem {
                item.title = currentTitle.trimmingCharacters(in: .whitespacesAndNewlines)
                item.link = currentLink.trimmingCharacters(in: .whitespacesAndNewlines)
                item.description = currentDescription.trimmingCharacters(in: .whitespacesAndNewlines)
                item.thumbnailUrl = currentThumbnailUrl
                
                // Try to parse pubDate into ISO8601
                let dateString = currentPubDate.trimmingCharacters(in: .whitespacesAndNewlines)
                let df = DateFormatter()
                df.locale = Locale(identifier: "en_US_POSIX")
                
                // Try different formats
                var date: Date? = nil
                let formats = [
                    "E, d MMM yyyy HH:mm:ss Z",
                    "yyyy-MM-dd'T'HH:mm:ssZ",
                    "yyyy-MM-dd'T'HH:mm:ss.SSSZ",
                    "yyyy-MM-dd'T'HH:mm:ss'Z'"
                ]
                for format in formats {
                    df.dateFormat = format
                    if let d = df.date(from: dateString) {
                        date = d
                        break
                    }
                }
                
                if let date = date {
                    item.pubDate = ISO8601DateFormatter().string(from: date)
                } else {
                    item.pubDate = dateString
                }
                
                items.append(item)
            }
            currentItem = nil
        }
    }
}
