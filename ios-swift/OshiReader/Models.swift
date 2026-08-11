import Foundation

enum _ISO8601Cache {
    private static let lock = NSLock()
    private static var parsedDates: [String: Date?] = [:]
    private static let maxParsedDateCacheEntries = 4096

    static let withFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    static let withoutFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    static let naiveFormatters: [DateFormatter] = {
        ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss.SSS", "yyyy-MM-dd'T'HH:mm:ss"].map {
            let df = DateFormatter()
            df.locale = Locale(identifier: "en_US_POSIX")
            df.timeZone = TimeZone(identifier: "UTC")
            df.dateFormat = $0
            return df
        }
    }()

    static func cachedDate(from value: String) -> Date? {
        lock.lock()
        if let index = parsedDates.index(forKey: value) {
            let cached = parsedDates[index].value
            lock.unlock()
            return cached
        }

        let parsed = parseDateLocked(value)
        if parsedDates.count >= maxParsedDateCacheEntries {
            parsedDates.removeAll(keepingCapacity: true)
        }
        parsedDates.updateValue(parsed, forKey: value)
        lock.unlock()
        return parsed
    }

    private static func parseDateLocked(_ value: String) -> Date? {
        if let date = withFractional.date(from: value) { return date }
        if let date = withoutFractional.date(from: value) { return date }
        // Naive datetime (no timezone) from older backend rows — treat as UTC
        for df in naiveFormatters {
            if let date = df.date(from: value) { return date }
        }
        return nil
    }
}

func parseISO8601Date(_ value: String) -> Date? {
    _ISO8601Cache.cachedDate(from: value)
}

private let _relativeDateTimeFormatter: RelativeDateTimeFormatter = {
    let formatter = RelativeDateTimeFormatter()
    formatter.unitsStyle = .abbreviated
    return formatter
}()

func relativeTimeString(from date: Date, relativeTo reference: Date = Date()) -> String {
    _relativeDateTimeFormatter.localizedString(for: date, relativeTo: reference)
}

private enum _DisplayTextRegex {
    static let htmlTags  = try! NSRegularExpression(pattern: "<[^>]+>")
    static let whitespace = try! NSRegularExpression(pattern: "\\s+")
}

func cleanDisplayText(_ value: String?) -> String? {
    guard var text = value else { return nil }
    // Strip HTML tags first so entity-decoded < > aren't mistaken for tags
    var range = NSRange(text.startIndex..., in: text)
    text = _DisplayTextRegex.htmlTags.stringByReplacingMatches(in: text, range: range, withTemplate: "")
    let replacements: [(String, String)] = [
        ("&amp;", "&"), ("&quot;", "\""), ("&#39;", "'"), ("&apos;", "'"),
        ("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">")
    ]
    for (needle, replacement) in replacements {
        text = text.replacingOccurrences(of: needle, with: replacement)
    }
    range = NSRange(text.startIndex..., in: text)
    text = _DisplayTextRegex.whitespace.stringByReplacingMatches(in: text, range: range, withTemplate: " ")
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : trimmed
}

// MARK: - CollectionMode
enum CollectionMode: String, Codable, Hashable {
    case allInfo  = "all_info"
    case mediaOnly = "media_only"
}

enum SourceMode: String, Codable, Hashable {
    case all
    case selected
}

// MARK: - MediaFilter
enum MediaFilter {
    case all
    case mediaOnly
}

// MARK: - WatchTerm
struct WatchTerm: Identifiable, Codable, Hashable {
    let id: String
    var keyword: String
    var collection_mode: CollectionMode
    var source_mode: SourceMode
    var selected_platforms: [String]
    var is_active: Bool
    var notify_on_new: Bool
    var aliases: [String]
    var repaired_from_cache: Bool
    let created_at: String

    enum CodingKeys: String, CodingKey {
        case id, keyword, collection_mode, source_mode, selected_platforms, is_active, notify_on_new, aliases, repaired_from_cache, created_at
    }

    init(id: String = UUID().uuidString, keyword: String, collection_mode: CollectionMode = .allInfo, source_mode: SourceMode = .all, selected_platforms: [String] = [], is_active: Bool = true, notify_on_new: Bool = false, aliases: [String] = [], repaired_from_cache: Bool = false, created_at: String = _ISO8601Cache.withoutFractional.string(from: Date())) {
        self.id = id
        self.keyword = keyword
        self.collection_mode = collection_mode
        self.source_mode = source_mode
        self.selected_platforms = selected_platforms
        self.is_active = is_active
        self.notify_on_new = notify_on_new
        self.aliases = aliases
        self.repaired_from_cache = repaired_from_cache
        self.created_at = created_at
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        // Decodes ID as String or Int coerced to String
        if let stringId = try? container.decode(String.self, forKey: .id) {
            self.id = stringId
        } else if let intId = try? container.decode(Int.self, forKey: .id) {
            self.id = String(intId)
        } else {
            self.id = UUID().uuidString
        }
        self.keyword = try container.decode(String.self, forKey: .keyword)
        // Gracefully fall back to .allInfo for unknown/missing values from older backend rows.
        self.collection_mode = (try? container.decode(CollectionMode.self, forKey: .collection_mode)) ?? .allInfo
        self.source_mode = (try? container.decode(SourceMode.self, forKey: .source_mode)) ?? .all
        self.selected_platforms = try container.decodeIfPresent([String].self, forKey: .selected_platforms) ?? []
        self.is_active = try container.decodeIfPresent(Bool.self, forKey: .is_active) ?? true
        self.notify_on_new = try container.decodeIfPresent(Bool.self, forKey: .notify_on_new) ?? false
        self.aliases = try container.decodeIfPresent([String].self, forKey: .aliases) ?? []
        self.repaired_from_cache = try container.decodeIfPresent(Bool.self, forKey: .repaired_from_cache) ?? false
        self.created_at = try container.decodeIfPresent(String.self, forKey: .created_at) ?? _ISO8601Cache.withoutFractional.string(from: Date())
    }
}

// MARK: - SourceItem
struct SourceItem: Codable, Hashable, Identifiable {
    let id: String
    let platform: String
    let url: String
    let published_at: String
    let author: String?
    let title: String?
    let content_text: String?
    let media_type: String?
    let thumbnail_url: String?
    var source: String? = nil
}

// MARK: - Local Flat FeedItem
struct FeedItem: Codable, Hashable, Identifiable {
    let id: String
    let platform: String
    let url: String
    let title: String?
    let content_text: String?
    let author: String?
    let thumbnail_url: String?
    let media_type: String
    let published_at: String
    let watch_term_keyword: String
    var watch_term_id: Int? = nil
    let fetched_at: String
    var source: String? = nil
}

// MARK: - Backend Nested FeedItem
struct BackendFeedItem: Codable {
    let match_id: Int
    let watch_term_id: Int
    let watch_term_keyword: String
    let item: SourceItem
    let matched_at: String
    
    func toFeedItem() -> FeedItem {
        return FeedItem(
            id: item.id,
            platform: item.platform,
            url: item.url,
            title: item.title,
            content_text: item.content_text,
            author: item.author,
            thumbnail_url: item.thumbnail_url,
            media_type: item.media_type ?? "article",
            published_at: item.published_at,
            watch_term_keyword: watch_term_keyword,
            watch_term_id: watch_term_id,
            fetched_at: matched_at,
            source: item.source
        )
    }
}

// MARK: - SavedPage
struct SavedPage: Codable, Hashable, Identifiable {
    let id: String
    let url: String
    let title: String?
    let platform: String
    let saved_at: String
}

// MARK: - CustomUrl
struct CustomUrl: Codable, Hashable, Identifiable {
    let id: String
    let url: String
    let title: String?
    let added_at: String
}

// MARK: - AvatarLayer
struct AvatarLayer: Codable, Hashable, Identifiable {
    let id: String
    let imageUrl: String
    var x: Double
    var y: Double
    var scale: Double
    var cropX: Double?
    var cropY: Double?
    var cropScale: Double?
    var rotation: Double?
    var zIndex: Int
    
    init(id: String = UUID().uuidString, imageUrl: String, x: Double, y: Double, scale: Double = 1.0, cropX: Double? = 0.0, cropY: Double? = 0.0, cropScale: Double? = 1.0, rotation: Double? = 0.0, zIndex: Int) {
        self.id = id
        self.imageUrl = imageUrl
        self.x = x
        self.y = y
        self.scale = scale
        self.cropX = cropX
        self.cropY = cropY
        self.cropScale = cropScale
        self.rotation = rotation
        self.zIndex = zIndex
    }
}

// MARK: - Credential
struct Credential: Codable, Hashable {
    let platform: String
    let has_bearer_token: Bool
    let has_api_key: Bool
    let has_api_secret: Bool
    let updated_at: String?
}

struct APNSDeviceRegistrationResponse: Codable, Hashable {
    let token: String
    let environment: String
    let device_id: String?
    let last_seen_at: String?
    let is_verified: Bool?
    let verification_error: String?
}

// MARK: - Client Diagnostics
struct ClientDiagnosticEvent: Codable, Hashable {
    let strategy: String
    let status: String
    let item_count: Int
    let added_count: Int
    let detail: String?
}

struct ClientDiagnosticReport: Codable, Hashable {
    let reason: String
    let environment: String
    let api_base: String
    let app_version: String?
    let build: String?
    let active_terms_count: Int
    let subscribed_platforms: [String]
    let cached_feed_count: Int
    let events: [ClientDiagnosticEvent]
}
