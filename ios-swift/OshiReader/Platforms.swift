import SwiftUI

// Single source of truth for all platform definitions.
// Every behavioral flag, display attribute, and ID aliasing rule lives here.
struct Platform {
    let id: String              // canonical subscription/filter key
    let name: String            // badge / UI display name
    let icon: String
    let accent: Color
    let bg: Color
    let fg: Color
    // Which FeedItem.platform raw values belong to this canonical platform
    let rawPlatformValues: Set<String>
    // Behavioral flags
    let usesStrictKeywordMatching: Bool // news-type sources: reject items whose text doesn't contain the keyword
    let skipDateCutoff: Bool            // forum-type sources: always show, regardless of age filter
    let isMediaPlatform: Bool           // video-first sources shown in media-only filter
    let subscribedByDefault: Bool

    // MARK: - Registry

    static let all: [Platform] = [
        Platform(
            id: "youtube", name: "YouTube", icon: "📹",
            accent: .red, bg: Color(red: 1.0, green: 0.9, blue: 0.9), fg: .red,
            rawPlatformValues: ["youtube"],
            usesStrictKeywordMatching: false, skipDateCutoff: false,
            isMediaPlatform: true, subscribedByDefault: true
        ),
        Platform(
            id: "niconico", name: "NicoNico", icon: "💬",
            accent: .black, bg: .gray.opacity(0.2), fg: .primary,
            rawPlatformValues: ["niconico"],
            usesStrictKeywordMatching: false, skipDateCutoff: false,
            isMediaPlatform: true, subscribedByDefault: true
        ),
        Platform(
            id: "tver", name: "TVer", icon: "📺",
            accent: .blue, bg: Color(red: 0.9, green: 0.95, blue: 1.0), fg: .blue,
            rawPlatformValues: ["tver"],
            usesStrictKeywordMatching: false, skipDateCutoff: false,
            isMediaPlatform: true, subscribedByDefault: true
        ),
        Platform(
            id: "note", name: "Note", icon: "📝",
            accent: Color(red: 0.1, green: 0.7, blue: 0.5),
            bg: Color(red: 0.9, green: 0.97, blue: 0.95),
            fg: Color(red: 0.1, green: 0.7, blue: 0.5),
            rawPlatformValues: ["note"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "girlschannel", name: "GirlsChannel", icon: "👭",
            accent: .pink, bg: Color(red: 1.0, green: 0.92, blue: 0.95), fg: .pink,
            rawPlatformValues: ["girlschannel"],
            usesStrictKeywordMatching: true, skipDateCutoff: true,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "5ch", name: "5ch", icon: "💬",
            accent: .orange, bg: Color(red: 1.0, green: 0.95, blue: 0.9), fg: .orange,
            rawPlatformValues: ["5ch"],
            usesStrictKeywordMatching: true, skipDateCutoff: true,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "togetter", name: "Togetter", icon: "🐧",
            accent: .green, bg: Color(red: 0.9, green: 0.98, blue: 0.92), fg: .green,
            rawPlatformValues: ["togetter"],
            usesStrictKeywordMatching: true, skipDateCutoff: true,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "news", name: "News", icon: "📰",
            accent: .purple, bg: Color(red: 0.96, green: 0.9, blue: 1.0), fg: .purple,
            rawPlatformValues: ["news"],   // "news:*" wildcard handled in normalize()
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "yahoonews", name: "YahooNews", icon: "🇯🇵",
            accent: Color(red: 0.86, green: 0.0, blue: 0.0),
            bg: Color(red: 1.0, green: 0.92, blue: 0.92),
            fg: Color(red: 0.86, green: 0.0, blue: 0.0),
            rawPlatformValues: ["yahoonews", "news:yahoo_ent"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "mdpr", name: "ModelPress", icon: "💅",
            accent: .pink, bg: Color(red: 1.0, green: 0.9, blue: 0.95), fg: .pink,
            rawPlatformValues: ["mdpr", "news:mdpr"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "oricon", name: "Oricon", icon: "🎤",
            accent: Color(red: 0.86, green: 0.12, blue: 0.22),
            bg: Color(red: 1.0, green: 0.92, blue: 0.94),
            fg: Color(red: 0.86, green: 0.12, blue: 0.22),
            rawPlatformValues: ["oricon"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "smartnews", name: "SmartNews", icon: "📰",
            accent: Color(red: 0.80, green: 0.00, blue: 0.00),
            bg: Color(red: 1.0, green: 0.92, blue: 0.92),
            fg: Color(red: 0.80, green: 0.00, blue: 0.00),
            rawPlatformValues: ["smartnews"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "ameblo", name: "Ameblo", icon: "✏️",
            accent: Color(red: 1.00, green: 0.42, blue: 0.00),
            bg: Color(red: 1.0, green: 0.94, blue: 0.88),
            fg: Color(red: 1.00, green: 0.42, blue: 0.00),
            rawPlatformValues: ["ameblo"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "aera", name: "AERA dot.", icon: "📝",
            accent: Color(red: 0.00, green: 0.27, blue: 0.58),
            bg: Color(red: 0.90, green: 0.94, blue: 1.0),
            fg: Color(red: 0.00, green: 0.27, blue: 0.58),
            rawPlatformValues: ["aera"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "hochi", name: "Hochi", icon: "🏅",
            accent: Color(red: 0.82, green: 0.10, blue: 0.10),
            bg: Color(red: 1.0, green: 0.92, blue: 0.92),
            fg: Color(red: 0.82, green: 0.10, blue: 0.10),
            rawPlatformValues: ["hochi"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "sponichi", name: "Sponichi", icon: "⚽",
            accent: Color(red: 0.00, green: 0.27, blue: 0.60),
            bg: Color(red: 0.90, green: 0.94, blue: 1.0),
            fg: Color(red: 0.00, green: 0.27, blue: 0.60),
            rawPlatformValues: ["sponichi"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "livedoor", name: "Livedoor", icon: "🔴",
            accent: Color(red: 0.88, green: 0.00, blue: 0.20),
            bg: Color(red: 1.0, green: 0.92, blue: 0.94),
            fg: Color(red: 0.88, green: 0.00, blue: 0.20),
            rawPlatformValues: ["livedoor"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "mantanweb", name: "Mantan Web", icon: "🎌",
            accent: Color(red: 0.07, green: 0.53, blue: 0.25),
            bg: Color(red: 0.90, green: 1.0, blue: 0.93),
            fg: Color(red: 0.07, green: 0.53, blue: 0.25),
            rawPlatformValues: ["mantanweb"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "realsound", name: "Real Sound", icon: "🎧",
            accent: Color(red: 0.18, green: 0.36, blue: 0.72),
            bg: Color(red: 0.91, green: 0.95, blue: 1.0),
            fg: Color(red: 0.18, green: 0.36, blue: 0.72),
            rawPlatformValues: ["realsound"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "cinemacafe", name: "CinemaCafe", icon: "🎬",
            accent: Color(red: 0.56, green: 0.20, blue: 0.64),
            bg: Color(red: 0.96, green: 0.91, blue: 0.98),
            fg: Color(red: 0.56, green: 0.20, blue: 0.64),
            rawPlatformValues: ["cinemacafe"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "barks", name: "BARKS", icon: "🎸",
            accent: Color(red: 0.13, green: 0.13, blue: 0.13),
            bg: Color(red: 0.93, green: 0.93, blue: 0.93),
            fg: Color(red: 0.13, green: 0.13, blue: 0.13),
            rawPlatformValues: ["barks"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
        Platform(
            id: "custom", name: "Custom Feeds", icon: "🌐",
            accent: .blue, bg: Color(red: 0.9, green: 0.95, blue: 1.0), fg: .blue,
            rawPlatformValues: ["custom"],
            usesStrictKeywordMatching: false, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true
        ),
    ]

    // MARK: - Lookups (built once at startup)

    private static let _rawToId: [String: String] = {
        var map = [String: String]()
        for p in all {
            for raw in p.rawPlatformValues { map[raw] = p.id }
        }
        return map
    }()

    private static let _idToDefinition: [String: Platform] = Dictionary(
        uniqueKeysWithValues: all.map { ($0.id, $0) }
    )

    // Canonical platform ID for a raw FeedItem.platform value.
    // Remaining "news:*" variants that aren't explicitly mapped → "news".
    static func normalize(_ rawPlatformValue: String) -> String {
        if let id = _rawToId[rawPlatformValue] { return id }
        if rawPlatformValue.hasPrefix("news:") { return "news" }
        return rawPlatformValue
    }

    // Look up by canonical ID.
    static func find(_ id: String) -> Platform? { _idToDefinition[id] }

    // Look up by raw FeedItem.platform value.
    static func forRawValue(_ raw: String) -> Platform? { find(normalize(raw)) }
}
