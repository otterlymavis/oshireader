import Foundation

struct IrasutoyaImage: Codable, Identifiable, Hashable {
    var id: String { url }
    let url: String
    let thumb: String
    let title: String
}

class NetworkManager {
    static let shared = NetworkManager()
    
    private init() {}
    
    // MARK: - Backend URL Config
    var apiBase: String {
        return UserDefaults.standard.string(forKey: "api_base_url") ?? "https://otterpia-backend-production.up.railway.app"
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
    
    // MARK: - Create Watch Term
    func createWatchTerm(keyword: String, collectionMode: String) async throws -> WatchTerm {
        let url = URL(string: "\(apiBase)/api/watch-terms/")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
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
    func updateWatchTerm(id: String, isActive: Bool? = nil, collectionMode: String? = nil) async throws -> WatchTerm {
        let url = URL(string: "\(apiBase)/api/watch-terms/\(id)")!
        var request = URLRequest(url: url)
        request.httpMethod = "PATCH"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
        var body: [String: Any] = [:]
        if let isActive = isActive { body["is_active"] = isActive }
        if let collectionMode = collectionMode { body["collection_mode"] = collectionMode }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(WatchTerm.self, from: data)
    }
    
    // MARK: - Delete Watch Term
    func deleteWatchTerm(id: String) async throws {
        let url = URL(string: "\(apiBase)/api/watch-terms/\(id)")!
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        
        let (_, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }
    
    // MARK: - Fetch Feed
    func fetchFeed(termId: Int? = nil, platform: String? = nil, limit: Int = 50) async throws -> [FeedItem] {
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
        let (data, response) = try await URLSession.shared.data(from: url)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode([Credential].self, from: data)
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
        let url = URL(string: "\(apiBase)/api/admin/poll")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        
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
                // Upscale to s400 size matching the React Native code
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
}

class RSSParserDelegate: NSObject, XMLParserDelegate {
    var items = [RssItem]()
    private var currentElement = ""
    private var currentItem: RssItem? = nil
    
    private var currentTitle = ""
    private var currentLink = ""
    private var currentDescription = ""
    private var currentPubDate = ""
    
    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?, qualifiedName qName: String?, attributes attributeDict: [String : String] = [:]) {
        currentElement = elementName
        if elementName == "item" || elementName == "entry" {
            currentItem = RssItem()
            currentTitle = ""
            currentLink = ""
            currentDescription = ""
            currentPubDate = ""
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
