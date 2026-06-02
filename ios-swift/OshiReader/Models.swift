import Foundation

func parseISO8601Date(_ value: String) -> Date? {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: value) {
        return date
    }
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.date(from: value)
}

func cleanDisplayText(_ value: String?) -> String? {
    guard var text = value else { return nil }
    let replacements = [
        "&amp;": "&",
        "&quot;": "\"",
        "&#39;": "'",
        "&apos;": "'",
        "&nbsp;": " "
    ]
    for (needle, replacement) in replacements {
        text = text.replacingOccurrences(of: needle, with: replacement)
    }
    text = text.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
    text = text.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : trimmed
}

// MARK: - WatchTerm
struct WatchTerm: Identifiable, Codable, Hashable {
    let id: String
    var keyword: String
    var collection_mode: String // "all_info" | "media_only"
    var is_active: Bool
    var notify_on_new: Bool
    let created_at: String
    
    enum CodingKeys: String, CodingKey {
        case id, keyword, collection_mode, is_active, notify_on_new, created_at
    }
    
    init(id: String = UUID().uuidString, keyword: String, collection_mode: String = "all_info", is_active: Bool = true, notify_on_new: Bool = false, created_at: String = ISO8601DateFormatter().string(from: Date())) {
        self.id = id
        self.keyword = keyword
        self.collection_mode = collection_mode
        self.is_active = is_active
        self.notify_on_new = notify_on_new
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
        self.collection_mode = try container.decode(String.self, forKey: .collection_mode)
        self.is_active = try container.decodeIfPresent(Bool.self, forKey: .is_active) ?? true
        self.notify_on_new = try container.decodeIfPresent(Bool.self, forKey: .notify_on_new) ?? false
        self.created_at = try container.decodeIfPresent(String.self, forKey: .created_at) ?? ISO8601DateFormatter().string(from: Date())
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
    let fetched_at: String
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
            fetched_at: matched_at
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
    let updated_at: String?
}

// MARK: - ScraperLog
struct ScraperLog: Codable, Hashable {
    let name: String
    let count: Int
    let error: String?
    let ms: Int
}

// MARK: - ScrapeRun
struct ScrapeRun: Codable, Hashable {
    let keyword: String
    let ran_at: String
    let logs: [ScraperLog]
}
