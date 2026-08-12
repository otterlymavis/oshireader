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

    static let yahooNewsSuffix   = try? NSRegularExpression(pattern: #"\s*[-|]\s*Yahoo!ニュース\s*$"#)
    static let yahooNewsParens   = try? NSRegularExpression(pattern: #"\s*\([^)]*ニュース\)\s*[-|]\s*Yahoo!ニュース\s*$"#)
    static let searchEngineSuffix = try? NSRegularExpression(pattern: #"\s*[-|]\s*(?:Bing|Google)\s*$"#)
}

// MARK: - RSS Data Model

struct RssItem {
    var title: String = ""
    var link: String = ""
    var description: String = ""
    var pubDate: String? = nil
    var thumbnailUrl: String? = nil
}

struct LocalFallbackScrapeResult {
    var items: [FeedItem] = []
    var completedPlatforms: Set<String> = []

    mutating func append(_ other: LocalFallbackScrapeResult) {
        items.append(contentsOf: other.items)
        completedPlatforms.formUnion(other.completedPlatforms)
    }
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

private extension CharacterSet {
    static let urlPathSegmentAllowed: CharacterSet = {
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/")
        return allowed
    }()
}

// MARK: - Scraper Extension

extension NetworkManager {
    private var scraperBrowserUserAgent: String {
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    }

    // MARK: - RSS Fallback (NHK + Google News)

    func scrapeRSSFallback(keyword: String, tagKeyword: String? = nil) async -> [FeedItem] {
        await scrapeRSSFallbackWithCompletion(keyword: keyword, tagKeyword: tagKeyword).items
    }

    func scrapeRSSFallbackWithCompletion(keyword: String, tagKeyword: String? = nil) async -> LocalFallbackScrapeResult {
        var results = [FeedItem]()
        var nhkCompleted = false
        var googleNewsCompleted = false
        let tag = tagKeyword ?? keyword
        let nowString = _scraperISO8601.string(from: Date())

        if let nhkUrl = URL(string: "https://www3.nhk.or.jp/rss/news/cat7.xml") {
            do {
                let nhkItems = try await parseRss(url: nhkUrl)
                nhkCompleted = true
                for item in nhkItems where titleMatchesKeyword(item.title, keyword: keyword) {
                    results.append(FeedItem(
                        id: "news:nhk:\(stableIdHash(item.link))",
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
            } catch {
                AppLogger.scraping.error("NHK RSS fallback failed: \(error.localizedDescription)")
            }
        }

        if let gnewsUrl = googleNewsSearchURL(query: keyword) {
            do {
                let gnewsItems = try await parseRss(url: gnewsUrl)
                googleNewsCompleted = true
                for item in gnewsItems {
                    let cleanedTitle = cleanNewsTitle(item.title)
                    guard titleMatchesKeyword(cleanedTitle, keyword: keyword) else { continue }
                    results.append(FeedItem(
                        id: "news:gnews:\(stableIdHash(item.link))",
                        platform: "news",
                        url: item.link,
                        title: cleanedTitle,
                        content_text: item.description.isEmpty ? nil : item.description,
                        author: "Google News",
                        thumbnail_url: nil,
                        media_type: "article",
                        published_at: item.pubDate ?? nowString,
                        watch_term_keyword: tag,
                        fetched_at: nowString
                    ))
                }
            } catch {
                AppLogger.scraping.error("Google News general fallback failed: \(error.localizedDescription)")
            }
        }

        return LocalFallbackScrapeResult(
            items: results,
            completedPlatforms: nhkCompleted && googleNewsCompleted ? ["news"] : []
        )
    }

    // MARK: - Custom URL Scraping

    func scrapeCustomUrls(_ urls: [CustomUrl]) async -> [FeedItem] {
        guard !urls.isEmpty else { return [] }
        return await withTaskGroup(of: FeedItem?.self) { group in
            for entry in urls { group.addTask { await self.scrapeCustomUrl(entry) } }
            var results = [FeedItem]()
            for await item in group {
                if Task.isCancelled {
                    group.cancelAll()
                    break
                }
                if let item { results.append(item) }
            }
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

    static let googleNewsFallbackSites: [GoogleNewsFallbackSite] =
        Platform.googleNewsFallbackSites()

    func scrapeLocalFallbacks(
        keyword: String,
        tagKeyword: String? = nil,
        platformIds: Set<String>? = nil
    ) async -> [FeedItem] {
        await scrapeLocalFallbacksWithCompletion(
            keyword: keyword,
            tagKeyword: tagKeyword,
            platformIds: platformIds
        ).items
    }

    func scrapeLocalFallbacksWithCompletion(
        keyword: String,
        tagKeyword: String? = nil,
        platformIds: Set<String>? = nil
    ) async -> LocalFallbackScrapeResult {
        let tag = tagKeyword ?? keyword
        return await withTaskGroup(of: LocalFallbackScrapeResult.self) { group in
            let sourceSpecificPlatforms = Set(["youtube", "note"])
            let platformIdsForGenericGoogleNews = (platformIds ?? Set(Platform.googleNewsFallbackSites().map(\.platform)))
                .subtracting(sourceSpecificPlatforms)
            if platformIds?.contains("youtube") != false {
                group.addTask { await self.scrapeYouTubeWithCompletion(keyword: keyword, tagKeyword: tag) }
            }
            if platformIds?.contains("note") != false {
                group.addTask { await self.scrapeNoteWithCompletion(keyword: keyword, tagKeyword: tag) }
            }
            if Platform.usesNewsRSSFallback(for: platformIds) {
                group.addTask { await self.scrapeRSSFallbackWithCompletion(keyword: keyword, tagKeyword: tag) }
            }
            if Platform.usesNiconicoRSSFallback(for: platformIds) {
                group.addTask { await self.scrapeNiconicoRSSWithCompletion(keyword: keyword, tagKeyword: tag) }
            }
            for fallback in Platform.googleNewsFallbackSites(for: platformIdsForGenericGoogleNews) {
                group.addTask {
                    await self.scrapeGoogleNewsSiteWithCompletion(
                        keyword: keyword,
                        site: fallback.site,
                        platform: fallback.platform,
                        tagKeyword: tag,
                        locale: fallback.locale
                    )
                }
            }
            var result = LocalFallbackScrapeResult()
            for await scrapeResult in group {
                if Task.isCancelled {
                    group.cancelAll()
                    break
                }
                result.append(scrapeResult)
            }
            return result
        }
    }

    // MARK: - YouTube Keyless Search

    func scrapeYouTube(keyword: String, tagKeyword: String? = nil) async -> [FeedItem] {
        await scrapeYouTubeWithCompletion(keyword: keyword, tagKeyword: tagKeyword).items
    }

    func scrapeYouTubeWithCompletion(keyword: String, tagKeyword: String? = nil) async -> LocalFallbackScrapeResult {
        let tag = tagKeyword ?? keyword
        let innertube = await fetchYouTubeInnertube(keyword: keyword, tagKeyword: tag)
        if innertube.completed && !innertube.items.isEmpty {
            return LocalFallbackScrapeResult(items: innertube.items, completedPlatforms: ["youtube"])
        }
        let scraped = await fetchYouTubeSearchPage(keyword: keyword, tagKeyword: tag)
        return LocalFallbackScrapeResult(
            items: scraped.items.isEmpty ? innertube.items : scraped.items,
            completedPlatforms: (innertube.completed || scraped.completed) ? ["youtube"] : []
        )
    }

    private func fetchYouTubeInnertube(keyword: String, tagKeyword: String) async -> (items: [FeedItem], completed: Bool) {
        guard let url = URL(string: "https://www.youtube.com/youtubei/v1/search?prettyPrint=false") else {
            return ([], false)
        }
        let payload: [String: Any] = [
            "context": [
                "client": [
                    "clientName": "WEB",
                    "clientVersion": "2.20260617.03.00",
                    "hl": "ja",
                    "gl": "JP"
                ]
            ],
            "query": keyword
        ]
        guard let body = try? JSONSerialization.data(withJSONObject: payload),
              let (data, _) = await httpPOST(
                url,
                body: body,
                headers: [
                    "Content-Type": "application/json",
                    "User-Agent": scraperBrowserUserAgent,
                    "Accept-Language": "ja,ja-JP;q=0.9,en;q=0.8"
                ],
                timeout: 15
              ),
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return ([], false)
        }
        let cutoff = Date().addingTimeInterval(-31 * 86400)
        var items = collectYouTubeVideoRendererItems(from: json, keyword: keyword, tagKeyword: tagKeyword, cutoff: cutoff)
        if items.count < 25 {
            items.append(contentsOf: collectMobileYouTubeItems(from: json, keyword: keyword, tagKeyword: tagKeyword, cutoff: cutoff))
        }
        var seen = Set<String>()
        let deduped = items.filter { seen.insert($0.id).inserted }
        return (Array(deduped.prefix(25)), true)
    }

    private func fetchYouTubeSearchPage(keyword: String, tagKeyword: String) async -> (items: [FeedItem], completed: Bool) {
        guard var components = URLComponents(string: "https://www.youtube.com/results") else {
            return ([], false)
        }
        components.queryItems = [URLQueryItem(name: "search_query", value: keyword)]
        guard let url = components.url else { return ([], false) }
        guard let (data, _) = await httpGET(
            url,
            headers: [
                "User-Agent": scraperBrowserUserAgent,
                "Accept-Language": "ja,ja-JP;q=0.9,en;q=0.8"
            ],
            timeout: 15
        ) else {
            return ([], false)
        }
        guard let html = String(data: data, encoding: .utf8) else {
            return ([], true)
        }
        let cutoff = Date().addingTimeInterval(-31 * 86400)
        guard let json = extractYouTubeInitialData(from: html) else {
            return ([], true)
        }
        var items = collectYouTubeVideoRendererItems(from: json, keyword: keyword, tagKeyword: tagKeyword, cutoff: cutoff)
        if items.isEmpty {
            items = collectMobileYouTubeItems(from: json, keyword: keyword, tagKeyword: tagKeyword, cutoff: cutoff)
        }
        return (items, true)
    }

    private func collectYouTubeVideoRendererItems(
        from json: [String: Any],
        keyword: String,
        tagKeyword: String,
        cutoff: Date
    ) -> [FeedItem] {
        var renderers = [[String: Any]]()
        collectDictionaries(named: "videoRenderer", in: json, into: &renderers)

        return renderers.prefix(25).compactMap { renderer -> FeedItem? in
            guard let videoId = renderer["videoId"] as? String else { return nil }
            let relText = firstText(in: renderer["publishedTimeText"]) ?? ""
            guard let published = youtubeRelativeDate(relText) else { return nil }
            if published < cutoff { return nil }
            let description = (((renderer["detailedMetadataSnippets"] as? [[String: Any]])?.first?["snippetText"] as? [String: Any]))

            return FeedItem(
                id: "youtube:\(videoId)",
                platform: "youtube",
                url: "https://www.youtube.com/watch?v=\(videoId)",
                title: firstText(in: renderer["title"]),
                content_text: firstText(in: description),
                author: firstText(in: renderer["ownerText"]) ?? firstText(in: renderer["shortBylineText"]),
                thumbnail_url: firstThumbnailURL(in: renderer["thumbnail"]),
                media_type: "video",
                published_at: _scraperISO8601.string(from: published),
                watch_term_keyword: tagKeyword,
                fetched_at: _scraperISO8601.string(from: Date()),
                source: "youtube_device_scrape"
            )
        }
    }

    private func collectMobileYouTubeItems(
        from json: [String: Any],
        keyword: String,
        tagKeyword: String,
        cutoff: Date
    ) -> [FeedItem] {
        var items = [FeedItem]()
        var seenIds = Set<String>()
        var videoRenderers = [[String: Any]]()
        var shortsRenderers = [[String: Any]]()
        collectDictionaries(named: "videoWithContextRenderer", in: json, into: &videoRenderers)
        collectDictionaries(named: "shortsLockupViewModel", in: json, into: &shortsRenderers)

        for renderer in videoRenderers {
            guard let videoId = renderer["videoId"] as? String, seenIds.insert(videoId).inserted else { continue }
            let relText = firstText(in: renderer["publishedTimeText"]) ?? ""
            guard let published = youtubeRelativeDate(relText) else { continue }
            if published < cutoff { continue }
            let path = nestedString(renderer, ["navigationEndpoint", "commandMetadata", "webCommandMetadata", "url"])
            let itemURL = path.flatMap {
                URL(string: $0, relativeTo: URL(string: "https://www.youtube.com"))?.absoluteString
            } ?? "https://www.youtube.com/watch?v=\(videoId)"
            items.append(FeedItem(
                id: "youtube:\(videoId)",
                platform: "youtube",
                url: itemURL,
                title: firstText(in: renderer["headline"]),
                content_text: nil,
                author: firstText(in: renderer["shortBylineText"]),
                thumbnail_url: firstThumbnailURL(in: renderer["thumbnail"]),
                media_type: "video",
                published_at: _scraperISO8601.string(from: published),
                watch_term_keyword: tagKeyword,
                fetched_at: _scraperISO8601.string(from: Date()),
                source: "youtube_device_scrape"
            ))
        }

        for renderer in shortsRenderers {
            let videoId = nestedString(renderer, ["onTap", "innertubeCommand", "reelWatchEndpoint", "videoId"])
                ?? (renderer["entityId"] as? String)?.split(separator: "-").last.map(String.init)
            guard let videoId, seenIds.insert(videoId).inserted else { continue }
            let secondary = nestedString(renderer, ["belowThumbnailMetadata", "secondaryText", "content"]) ?? ""
            guard let published = youtubeRelativeDate(secondary) else { continue }
            if published < cutoff { continue }
            items.append(FeedItem(
                id: "youtube:\(videoId)",
                platform: "youtube",
                url: "https://www.youtube.com/shorts/\(videoId)",
                title: nestedString(renderer, ["overlayMetadata", "primaryText", "content"]) ?? (renderer["accessibilityText"] as? String),
                content_text: nil,
                author: nestedString(renderer, ["belowThumbnailMetadata", "primaryText", "content"]),
                thumbnail_url: firstThumbnailURL(in: nestedValue(renderer, ["onTap", "innertubeCommand", "reelWatchEndpoint", "thumbnail"])),
                media_type: "video",
                published_at: _scraperISO8601.string(from: published),
                watch_term_keyword: tagKeyword,
                fetched_at: _scraperISO8601.string(from: Date()),
                source: "youtube_device_scrape"
            ))
        }
        return Array(items.prefix(25))
    }

    private func extractYouTubeInitialData(from html: String) -> [String: Any]? {
        if let regex = try? NSRegularExpression(pattern: #"ytInitialData\s*=\s*(\{.+?\});"#, options: [.dotMatchesLineSeparators]),
           let match = regex.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
           let range = Range(match.range(at: 1), in: html),
           let data = String(html[range]).data(using: .utf8),
           let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            return json
        }

        guard let regex = try? NSRegularExpression(pattern: #"ytInitialData\s*=\s*'((?:\\'|[^'])*)';"#, options: [.dotMatchesLineSeparators]),
              let match = regex.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
              let range = Range(match.range(at: 1), in: html) else {
            return nil
        }
        let decoded = decodeJavaScriptEscapedString(String(html[range]))
        guard let data = decoded.data(using: .utf8),
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return nil
        }
        return json
    }

    private func decodeJavaScriptEscapedString(_ escaped: String) -> String {
        let scalars = Array(escaped.unicodeScalars)
        var output = String.UnicodeScalarView()
        var i = 0

        while i < scalars.count {
            let scalar = scalars[i]
            guard scalar == "\\" else {
                output.append(scalar)
                i += 1
                continue
            }
            let nextIndex = i + 1
            guard nextIndex < scalars.count else {
                output.append(scalar)
                i += 1
                continue
            }
            let next = scalars[nextIndex]
            if next == "x", i + 3 < scalars.count,
               let hi = Int(String(scalars[i + 2]), radix: 16),
               let lo = Int(String(scalars[i + 3]), radix: 16),
               let decoded = UnicodeScalar((hi << 4) + lo) {
                output.append(decoded)
                i += 4
            } else if next == "u", i + 5 < scalars.count {
                let hex = String(String.UnicodeScalarView(scalars[(i + 2)...(i + 5)]))
                if let value = Int(hex, radix: 16), let decoded = UnicodeScalar(value) {
                    output.append(decoded)
                    i += 6
                } else {
                    output.append(next)
                    i += 2
                }
            } else {
                switch next {
                case "n": output.append("\n")
                case "r": output.append("\r")
                case "t": output.append("\t")
                default: output.append(next)
                }
                i += 2
            }
        }
        return String(output)
    }

    private func collectDictionaries(named name: String, in value: Any, into results: inout [[String: Any]]) {
        if let dict = value as? [String: Any] {
            if let match = dict[name] as? [String: Any] {
                results.append(match)
            }
            for child in dict.values {
                collectDictionaries(named: name, in: child, into: &results)
            }
        } else if let array = value as? [Any] {
            for child in array {
                collectDictionaries(named: name, in: child, into: &results)
            }
        }
    }

    private func nestedValue(_ dict: [String: Any], _ path: [String]) -> Any? {
        var current: Any? = dict
        for key in path {
            current = (current as? [String: Any])?[key]
        }
        return current
    }

    private func nestedString(_ dict: [String: Any], _ path: [String]) -> String? {
        nestedValue(dict, path) as? String
    }

    private func firstText(in value: Any?) -> String? {
        guard let dict = value as? [String: Any] else { return nil }
        if let simple = dict["simpleText"] as? String { return simple }
        if let content = dict["content"] as? String { return content }
        if let first = (dict["runs"] as? [[String: Any]])?.first?["text"] as? String { return first }
        return nil
    }

    private func firstThumbnailURL(in value: Any?) -> String? {
        guard let dict = value as? [String: Any] else { return nil }
        return (dict["thumbnails"] as? [[String: Any]])?.first?["url"] as? String
    }

    private func youtubeRelativeDate(_ text: String) -> Date? {
        guard !text.isEmpty,
              let match = text.lowercased().range(
                of: #"(\d+)\s*(second|minute|hour|day|week|month|year|秒|分|時間|日|週間|週|ヶ月|か月|年)"#,
                options: .regularExpression
              ) else {
            return nil
        }
        let token = String(text.lowercased()[match])
        guard let numMatch = token.range(of: #"\d+"#, options: .regularExpression),
              let number = Int(token[numMatch]) else { return nil }
        let day = 86400.0
        if token.contains("second") || token.contains("秒") { return Date().addingTimeInterval(-Double(number)) }
        if token.contains("minute") || token.contains("分") { return Date().addingTimeInterval(-Double(number) * 60) }
        if token.contains("hour") || token.contains("時間") { return Date().addingTimeInterval(-Double(number) * 3600) }
        if token.contains("week") || token.contains("週") { return Date().addingTimeInterval(-Double(number) * 7 * day) }
        if token.contains("month") || token.contains("ヶ月") || token.contains("か月") { return Date().addingTimeInterval(-Double(number) * 30 * day) }
        if token.contains("year") || token.contains("年") { return Date().addingTimeInterval(-Double(number) * 365 * day) }
        if token.contains("day") || token.contains("日") { return Date().addingTimeInterval(-Double(number) * day) }
        return nil
    }

    // MARK: - Note Hashtag RSS

    func scrapeNote(keyword: String, tagKeyword: String? = nil) async -> [FeedItem] {
        await scrapeNoteWithCompletion(keyword: keyword, tagKeyword: tagKeyword).items
    }

    func scrapeNoteWithCompletion(keyword: String, tagKeyword: String? = nil) async -> LocalFallbackScrapeResult {
        let tag = tagKeyword ?? keyword
        let tags = noteRSSFallbackTags(for: keyword)
        var completed = false
        var entries = [RssItem]()
        for noteTag in tags {
            guard let encoded = noteTag.addingPercentEncoding(withAllowedCharacters: .urlPathSegmentAllowed),
                  let url = URL(string: "https://note.com/hashtag/\(encoded)/rss") else {
                continue
            }
            do {
                entries = try await parseRss(url: url)
                completed = true
            } catch {
                AppLogger.scraping.error("Note RSS fallback failed for tag \(noteTag): \(error.localizedDescription)")
                continue
            }
            if !entries.isEmpty { break }
        }

        let nowString = _scraperISO8601.string(from: Date())
        let items = entries.prefix(25).compactMap { item -> FeedItem? in
            guard !item.link.isEmpty else { return nil }
            let itemId = item.link.split(separator: "/").last.map(String.init) ?? item.link
            return FeedItem(
                id: "note:\(itemId)",
                platform: "note",
                url: item.link,
                title: item.title.isEmpty ? nil : item.title,
                content_text: item.description.isEmpty ? nil : item.description,
                author: nil,
                thumbnail_url: item.thumbnailUrl,
                media_type: "article",
                published_at: item.pubDate ?? nowString,
                watch_term_keyword: tag,
                fetched_at: nowString
            )
        }
        return LocalFallbackScrapeResult(
            items: items,
            completedPlatforms: completed ? ["note"] : []
        )
    }

    private func noteRSSFallbackTags(for keyword: String) -> [String] {
        let trimmed = keyword.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }
        let compacted = trimmed.components(separatedBy: .whitespacesAndNewlines).joined()
        return compacted != trimmed && !compacted.isEmpty ? [trimmed, compacted] : [trimmed]
    }

    // MARK: - NicoNico via Google News

    func scrapeNiconicoRSS(keyword: String, tagKeyword: String? = nil) async -> [FeedItem] {
        await scrapeNiconicoRSSWithCompletion(keyword: keyword, tagKeyword: tagKeyword).items
    }

    func scrapeNiconicoRSSWithCompletion(keyword: String, tagKeyword: String? = nil) async -> LocalFallbackScrapeResult {
        await scrapeGoogleNewsSiteWithCompletion(
            keyword: keyword,
            site: "nicovideo.jp",
            platform: "niconico",
            tagKeyword: tagKeyword
        )
    }

    // MARK: - Google News Site Filter RSS

    func scrapeGoogleNewsSite(
        keyword: String,
        site: String,
        platform: String,
        tagKeyword: String? = nil,
        locale: GoogleNewsFallbackLocale = .japan
    ) async -> [FeedItem] {
        await scrapeGoogleNewsSiteWithCompletion(
            keyword: keyword,
            site: site,
            platform: platform,
            tagKeyword: tagKeyword,
            locale: locale
        ).items
    }

    func scrapeGoogleNewsSiteWithCompletion(
        keyword: String,
        site: String,
        platform: String,
        tagKeyword: String? = nil,
        locale: GoogleNewsFallbackLocale = .japan
    ) async -> LocalFallbackScrapeResult {
        let query = "\(keyword) site:\(site)"
        guard let url = googleNewsSearchURL(query: query, locale: locale) else {
            AppLogger.scraping.error("Google News fallback failed platform=\(platform) site=\(site): could not build search URL")
            return LocalFallbackScrapeResult()
        }

        let tag = tagKeyword ?? keyword
        let nowString = _scraperISO8601.string(from: Date())

        let initialItems: [RssItem]
        do {
            initialItems = try await parseRss(url: url, acceptLanguage: locale.acceptLanguage)
        } catch {
            AppLogger.scraping.error("Google News fallback failed platform=\(platform) site=\(site): \(error.localizedDescription)")
            return LocalFallbackScrapeResult()
        }
        var items = initialItems
        if !items.contains(where: { titleMatchesKeyword(cleanNewsTitle($0.title), keyword: keyword) }) {
            let historicalQuery = "\(query) when:10y"
            guard let historicalURL = googleNewsSearchURL(query: historicalQuery, locale: locale) else {
                return LocalFallbackScrapeResult()
            }
            do {
                items = try await parseRss(url: historicalURL, acceptLanguage: locale.acceptLanguage)
            } catch {
                AppLogger.scraping.error("Google News historical fallback failed platform=\(platform) site=\(site): \(error.localizedDescription)")
                return LocalFallbackScrapeResult()
            }
        }

        let feedItems: [FeedItem] = items.compactMap { item in
            guard !item.link.isEmpty else { return nil }
            let cleanedTitle = cleanNewsTitle(item.title)
            guard titleMatchesKeyword(cleanedTitle, keyword: keyword) else { return nil }
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
        return LocalFallbackScrapeResult(
            items: feedItems,
            completedPlatforms: [Platform.normalize(platform)]
        )
    }

    // MARK: - Private Helpers

    private func stableIdHash(_ input: String) -> String {
        var v: UInt64 = 14695981039346656037
        for b in input.utf8 { v ^= UInt64(b); v = v &* 1099511628211 }
        return String(v)
    }

    private func googleNewsSearchURL(query: String, locale: GoogleNewsFallbackLocale = .japan) -> URL? {
        guard var components = URLComponents(string: "https://news.google.com/rss/search") else {
            return nil
        }
        components.queryItems = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "hl", value: locale.hl),
            URLQueryItem(name: "gl", value: locale.gl),
            URLQueryItem(name: "ceid", value: locale.ceid)
        ]
        return components.url
    }

    private func parseRss(
        url: URL,
        acceptLanguage: String = GoogleNewsFallbackLocale.japan.acceptLanguage
    ) async throws -> [RssItem] {
        guard let (data, _) = await httpGET(
            url,
            headers: [
                "User-Agent": scraperBrowserUserAgent,
                "Accept-Language": acceptLanguage
            ],
            timeout: 12
        ) else {
            throw URLError(.badServerResponse)
        }
        let parser = XMLParser(data: data)
        let delegate = RSSParserDelegate()
        parser.delegate = delegate
        parser.parse()
        return delegate.items
    }

    private func httpGET(
        _ url: URL,
        headers: [String: String] = [:],
        timeout: TimeInterval = 12
    ) async -> (Data, HTTPURLResponse)? {
        var request = URLRequest(url: url)
        request.timeoutInterval = timeout
        for (key, value) in headers {
            request.setValue(value, forHTTPHeaderField: key)
        }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                let status = (response as? HTTPURLResponse)?.statusCode
                AppLogger.scraping.error("GET \(url.host ?? url.absoluteString) failed: status=\(status.map(String.init) ?? "unknown")")
                return nil
            }
            return (data, http)
        } catch {
            AppLogger.scraping.error("GET \(url.host ?? url.absoluteString) failed: \(error.localizedDescription)")
            return nil
        }
    }

    private func httpPOST(
        _ url: URL,
        body: Data,
        headers: [String: String] = [:],
        timeout: TimeInterval = 12
    ) async -> (Data, HTTPURLResponse)? {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = body
        request.timeoutInterval = timeout
        for (key, value) in headers {
            request.setValue(value, forHTTPHeaderField: key)
        }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                let status = (response as? HTTPURLResponse)?.statusCode
                AppLogger.scraping.error("POST \(url.host ?? url.absoluteString) failed: status=\(status.map(String.init) ?? "unknown")")
                return nil
            }
            return (data, http)
        } catch {
            AppLogger.scraping.error("POST \(url.host ?? url.absoluteString) failed: \(error.localizedDescription)")
            return nil
        }
    }

    private func cleanNewsTitle(_ title: String) -> String {
        var t = title
        for regex in [_ScraperRegex.yahooNewsSuffix, _ScraperRegex.yahooNewsParens, _ScraperRegex.searchEngineSuffix] {
            if let r = regex { t = r.stringByReplacingMatches(in: t, range: NSRange(t.startIndex..., in: t), withTemplate: "") }
        }
        return t.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func titleMatchesKeyword(_ title: String, keyword: String) -> Bool {
        let haystack = title.lowercased()
        let needle = keyword.lowercased()
        if needle.isEmpty { return true }
        if haystack.contains(needle) { return true }
        let parts = keyword.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }
        return parts.count > 1 && parts.allSatisfy { haystack.contains($0.lowercased()) }
    }
}
