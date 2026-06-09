import Foundation

private let _scraperISO8601 = ISO8601DateFormatter()

private enum _ScraperRegex {
    static let titleTag = try? NSRegularExpression(
        pattern: #"<title[^>]*>([^<]{1,240})</title>"#,
        options: [.caseInsensitive, .dotMatchesLineSeparators]
    )
    static let metaDescription: [NSRegularExpression] = [
        #"<meta[^>]+name=["']description["'][^>]+content=["']([^"']{1,360})["'][^>]*>"#,
        #"<meta[^>]+content=["']([^"']{1,360})["'][^>]+name=["']description["'][^>]*>"#,
        #"<meta[^>]+property=["']og:description["'][^>]+content=["']([^"']{1,360})["'][^>]*>"#,
        #"<meta[^>]+content=["']([^"']{1,360})["'][^>]+property=["']og:description["'][^>]*>"#,
    ].compactMap { try? NSRegularExpression(pattern: $0, options: [.caseInsensitive]) }
}

// MARK: - RSS Data Model

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

    private let _dateFormatter: DateFormatter = {
        let df = DateFormatter()
        df.locale = Locale(identifier: "en_US_POSIX")
        return df
    }()
    private static let _dateFormats = [
        "E, d MMM yyyy HH:mm:ss Z",
        "yyyy-MM-dd'T'HH:mm:ssZ",
        "yyyy-MM-dd'T'HH:mm:ss.SSSZ",
        "yyyy-MM-dd'T'HH:mm:ss'Z'"
    ]
    private let _iso8601Out = ISO8601DateFormatter()

    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?, qualifiedName qName: String?, attributes attributeDict: [String: String] = [:]) {
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
        case "title":       currentTitle += string
        case "link":        currentLink += cleaned
        case "description", "summary": currentDescription += string
        case "pubDate", "published", "dc:date": currentPubDate += cleaned
        default: break
        }
    }

    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?, qualifiedName qName: String?) {
        guard elementName == "item" || elementName == "entry", var item = currentItem else { return }
        item.title = currentTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        item.link = currentLink.trimmingCharacters(in: .whitespacesAndNewlines)
        item.description = currentDescription.trimmingCharacters(in: .whitespacesAndNewlines)
        item.thumbnailUrl = currentThumbnailUrl

        let dateString = currentPubDate.trimmingCharacters(in: .whitespacesAndNewlines)
        var parsedDate: Date? = nil
        for format in Self._dateFormats {
            _dateFormatter.dateFormat = format
            if let d = _dateFormatter.date(from: dateString) { parsedDate = d; break }
        }
        item.pubDate = parsedDate.map { _iso8601Out.string(from: $0) } ?? dateString

        items.append(item)
        currentItem = nil
    }
}

// MARK: - Scraper Extension

extension NetworkManager {

    // MARK: - RSS Fallback (NHK + Google News)

    func scrapeRSSFallback(keyword: String, tagKeyword: String? = nil) async -> [FeedItem] {
        var results = [FeedItem]()
        let tag = tagKeyword ?? keyword
        let nowString = _scraperISO8601.string(from: Date())

        if let nhkUrl = URL(string: "https://www3.nhk.or.jp/rss/news/cat7.xml"),
           let nhkItems = try? await parseRss(url: nhkUrl) {
            for item in nhkItems where matchesKeyword(title: item.title, desc: item.description, kw: keyword) {
                results.append(FeedItem(
                    id: "news:nhk:\(UUID().uuidString)",
                    platform: "news",
                    url: item.link,
                    title: cleanNewsTitle(item.title),
                    content_text: item.description.isEmpty ? nil : item.description,
                    author: "NHK",
                    thumbnail_url: nil,
                    media_type: "article",
                    published_at: item.pubDate ?? nowString,
                    watch_term_keyword: tag,
                    fetched_at: nowString
                ))
            }
        }

        let encodedKeyword = keyword.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? keyword
        if let gnewsUrl = URL(string: "https://news.google.com/rss/search?q=\(encodedKeyword)&hl=ja&gl=JP&ceid=JP%3Aja"),
           let gnewsItems = try? await parseRss(url: gnewsUrl) {
            for item in gnewsItems {
                results.append(FeedItem(
                    id: "news:gnews:\(UUID().uuidString)",
                    platform: "news",
                    url: item.link,
                    title: cleanNewsTitle(item.title),
                    content_text: item.description.isEmpty ? nil : item.description,
                    author: "Google News",
                    thumbnail_url: nil,
                    media_type: "article",
                    published_at: item.pubDate ?? nowString,
                    watch_term_keyword: tag,
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
            for entry in urls { group.addTask { await self.scrapeCustomUrl(entry) } }
            var results = [FeedItem]()
            for await item in group { if let item { results.append(item) } }
            return results
        }
    }

    private func scrapeCustomUrl(_ entry: CustomUrl) async -> FeedItem? {
        let normalized = normalizedCustomUrl(entry.url)
        guard let url = URL(string: normalized) else { return nil }

        let nowString = _scraperISO8601.string(from: Date())
        var title = entry.title?.trimmingCharacters(in: .whitespacesAndNewlines)
        var description: String?

        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 12
            request.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", forHTTPHeaderField: "User-Agent")
            let (data, _) = try await session.data(for: request)
            if let html = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .shiftJIS) {
                title = extractTitleTag(from: html) ?? title
                description = extractMetaDescription(from: html)
            }
        } catch {
            AppLogger.scraping.error("Custom URL scrape failed for \(entry.url): \(error.localizedDescription)")
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

    private func extractTitleTag(from html: String) -> String? {
        guard let regex = _ScraperRegex.titleTag,
              let match = regex.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
              let range = Range(match.range(at: 1), in: html) else {
            return nil
        }
        return cleanDisplayText(String(html[range]))
    }

    private func extractMetaDescription(from html: String) -> String? {
        for regex in _ScraperRegex.metaDescription {
            guard let match = regex.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
                  let range = Range(match.range(at: 1), in: html) else { continue }
            return cleanDisplayText(String(html[range]))
        }
        return nil
    }

    // MARK: - Consolidated Local Fallback

    func scrapeLocalFallbacks(keyword: String, tagKeyword: String? = nil) async -> [FeedItem] {
        let tag = tagKeyword ?? keyword
        return await withTaskGroup(of: [FeedItem].self) { group in
            group.addTask { await self.scrapeRSSFallback(keyword: keyword, tagKeyword: tag) }
            group.addTask { await self.scrapeNiconicoRSS(keyword: keyword, tagKeyword: tag) }
            group.addTask { await self.scrapeGoogleNewsSite(keyword: keyword, site: "news.yahoo.co.jp", platform: "yahoonews", tagKeyword: tag) }
            group.addTask { await self.scrapeGoogleNewsSite(keyword: keyword, site: "mdpr.jp", platform: "mdpr", tagKeyword: tag) }
            group.addTask { await self.scrapeGoogleNewsSite(keyword: keyword, site: "oricon.co.jp", platform: "oricon", tagKeyword: tag) }
            group.addTask { await self.scrapeGoogleNewsSite(keyword: keyword, site: "girlschannel.net", platform: "girlschannel", tagKeyword: tag) }
            group.addTask { await self.scrapeGoogleNewsSite(keyword: keyword, site: "5ch.net", platform: "5ch", tagKeyword: tag) }
            var all = [FeedItem]()
            for await items in group { all.append(contentsOf: items) }
            return all
        }
    }

    // MARK: - NicoNico via Google News

    func scrapeNiconicoRSS(keyword: String, tagKeyword: String? = nil) async -> [FeedItem] {
        await scrapeGoogleNewsSite(keyword: keyword, site: "nicovideo.jp", platform: "niconico", tagKeyword: tagKeyword)
    }

    // MARK: - Google News Site Filter RSS

    func scrapeGoogleNewsSite(keyword: String, site: String, platform: String, tagKeyword: String? = nil) async -> [FeedItem] {
        let query = "\(keyword) site:\(site)"
        let encoded = query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? query
        guard let url = URL(string: "https://news.google.com/rss/search?q=\(encoded)&hl=ja&gl=JP&ceid=JP%3Aja") else { return [] }

        let tag = tagKeyword ?? keyword
        let nowString = _scraperISO8601.string(from: Date())

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
                watch_term_keyword: tag,
                fetched_at: nowString
            )
        }
    }

    // MARK: - Private Helpers

    private func stableIdHash(_ input: String) -> String {
        var v: UInt64 = 14695981039346656037
        for b in input.utf8 { v ^= UInt64(b); v = v &* 1099511628211 }
        return String(v)
    }

    private func parseRss(url: URL) async throws -> [RssItem] {
        let (data, _) = try await session.data(from: url)
        let parser = XMLParser(data: data)
        let delegate = RSSParserDelegate()
        parser.delegate = delegate
        parser.parse()
        return delegate.items
    }

    private func cleanNewsTitle(_ title: String) -> String {
        title
            .replacingOccurrences(of: "\\s*[-|]\\s*Yahoo!ニュース\\s*$", with: "", options: .regularExpression)
            .replacingOccurrences(of: "\\s*\\([^)]*ニュース\\)\\s*[-|]\\s*Yahoo!ニュース\\s*$", with: "", options: .regularExpression)
            .replacingOccurrences(of: "\\s*[-|]\\s*(?:Bing|Google)\\s*$", with: "", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func matchesKeyword(title: String, desc: String, kw: String) -> Bool {
        let haystack = "\(title) \(desc)".lowercased()
        let needle = kw.lowercased()
        if needle.isEmpty { return true }
        if haystack.contains(needle) { return true }
        let parts = kw.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }
        return parts.count > 1 && parts.allSatisfy { haystack.contains($0.lowercased()) }
    }
}
