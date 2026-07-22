import SwiftUI

enum PlatformFetchRole: String {
    case backendPrimary
    case backendOnly
    case deviceFallback
    case deviceOnly
}

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
    let fetchRole: PlatformFetchRole
    let usesActivityDateWindow: Bool
    let googleNewsFallbackSite: String?
    let usesNewsRSSFallback: Bool
    let usesNiconicoRSSFallback: Bool

    init(
        id: String,
        name: String,
        icon: String,
        accent: Color,
        bg: Color,
        fg: Color,
        rawPlatformValues: Set<String>,
        usesStrictKeywordMatching: Bool,
        skipDateCutoff: Bool,
        isMediaPlatform: Bool,
        subscribedByDefault: Bool,
        fetchRole: PlatformFetchRole,
        usesActivityDateWindow: Bool = false,
        googleNewsFallbackSite: String? = nil,
        usesNewsRSSFallback: Bool = false,
        usesNiconicoRSSFallback: Bool = false
    ) {
        self.id = id
        self.name = name
        self.icon = icon
        self.accent = accent
        self.bg = bg
        self.fg = fg
        self.rawPlatformValues = rawPlatformValues
        self.usesStrictKeywordMatching = usesStrictKeywordMatching
        self.skipDateCutoff = skipDateCutoff
        self.isMediaPlatform = isMediaPlatform
        self.subscribedByDefault = subscribedByDefault
        self.fetchRole = fetchRole
        self.usesActivityDateWindow = usesActivityDateWindow
        self.googleNewsFallbackSite = googleNewsFallbackSite
        self.usesNewsRSSFallback = usesNewsRSSFallback
        self.usesNiconicoRSSFallback = usesNiconicoRSSFallback
    }

    // MARK: - Registry

    static let all: [Platform] = [
        Platform(
            id: "youtube", name: "YouTube", icon: "📹",
            accent: .red, bg: Color(red: 1.0, green: 0.9, blue: 0.9), fg: .red,
            rawPlatformValues: ["youtube"],
            usesStrictKeywordMatching: false, skipDateCutoff: false,
            isMediaPlatform: true, subscribedByDefault: true,
            fetchRole: .deviceFallback
        ),
        Platform(
            id: "niconico", name: "NicoNico", icon: "💬",
            accent: .black, bg: .gray.opacity(0.2), fg: .primary,
            rawPlatformValues: ["niconico"],
            usesStrictKeywordMatching: false, skipDateCutoff: false,
            isMediaPlatform: true, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            usesNiconicoRSSFallback: true
        ),
        Platform(
            id: "tver", name: "TVer", icon: "📺",
            accent: .blue, bg: Color(red: 0.9, green: 0.95, blue: 1.0), fg: .blue,
            rawPlatformValues: ["tver"],
            usesStrictKeywordMatching: false, skipDateCutoff: false,
            isMediaPlatform: true, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "tver.jp"
        ),
        Platform(
            id: "twitter", name: "X", icon: "𝕏",
            accent: .black, bg: .gray.opacity(0.2), fg: .primary,
            rawPlatformValues: ["twitter", "x"],
            usesStrictKeywordMatching: false, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "x.com"
        ),
        Platform(
            id: "note", name: "Note", icon: "📝",
            accent: Color(red: 0.1, green: 0.7, blue: 0.5),
            bg: Color(red: 0.9, green: 0.97, blue: 0.95),
            fg: Color(red: 0.1, green: 0.7, blue: 0.5),
            rawPlatformValues: ["note"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback
        ),
        Platform(
            id: "girlschannel", name: "GirlsChannel", icon: "👭",
            accent: .pink, bg: Color(red: 1.0, green: 0.92, blue: 0.95), fg: .pink,
            rawPlatformValues: ["girlschannel"],
            usesStrictKeywordMatching: true, skipDateCutoff: true,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            usesActivityDateWindow: true,
            googleNewsFallbackSite: "girlschannel.net"
        ),
        Platform(
            id: "5ch", name: "5ch", icon: "💬",
            accent: .orange, bg: Color(red: 1.0, green: 0.95, blue: 0.9), fg: .orange,
            rawPlatformValues: ["5ch"],
            usesStrictKeywordMatching: true, skipDateCutoff: true,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            usesActivityDateWindow: true,
            googleNewsFallbackSite: "5ch.net"
        ),
        Platform(
            id: "togetter", name: "Togetter", icon: "🐧",
            accent: .green, bg: Color(red: 0.9, green: 0.98, blue: 0.92), fg: .green,
            rawPlatformValues: ["togetter"],
            usesStrictKeywordMatching: true, skipDateCutoff: true,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            usesActivityDateWindow: true,
            googleNewsFallbackSite: "togetter.com"
        ),
        Platform(
            id: "news", name: "News", icon: "📰",
            accent: .purple, bg: Color(red: 0.96, green: 0.9, blue: 1.0), fg: .purple,
            rawPlatformValues: ["news"],   // "news:*" wildcard handled in normalize()
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            usesNewsRSSFallback: true
        ),
        Platform(
            id: "yahoonews", name: "YahooNews", icon: "🇯🇵",
            accent: Color(red: 0.86, green: 0.0, blue: 0.0),
            bg: Color(red: 1.0, green: 0.92, blue: 0.92),
            fg: Color(red: 0.86, green: 0.0, blue: 0.0),
            rawPlatformValues: ["yahoonews", "news:yahoo_ent"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "news.yahoo.co.jp"
        ),
        Platform(
            id: "mdpr", name: "ModelPress", icon: "💅",
            accent: .pink, bg: Color(red: 1.0, green: 0.9, blue: 0.95), fg: .pink,
            rawPlatformValues: ["mdpr", "news:mdpr"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "mdpr.jp"
        ),
        Platform(
            id: "oricon", name: "Oricon", icon: "🎤",
            accent: Color(red: 0.86, green: 0.12, blue: 0.22),
            bg: Color(red: 1.0, green: 0.92, blue: 0.94),
            fg: Color(red: 0.86, green: 0.12, blue: 0.22),
            rawPlatformValues: ["oricon"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "oricon.co.jp"
        ),
        Platform(
            id: "smartnews", name: "SmartNews", icon: "📰",
            accent: Color(red: 0.80, green: 0.00, blue: 0.00),
            bg: Color(red: 1.0, green: 0.92, blue: 0.92),
            fg: Color(red: 0.80, green: 0.00, blue: 0.00),
            rawPlatformValues: ["smartnews"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "smartnews.com"
        ),
        Platform(
            id: "ameblo", name: "Ameblo", icon: "✏️",
            accent: Color(red: 1.00, green: 0.42, blue: 0.00),
            bg: Color(red: 1.0, green: 0.94, blue: 0.88),
            fg: Color(red: 1.00, green: 0.42, blue: 0.00),
            rawPlatformValues: ["ameblo"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "ameblo.jp"
        ),
        Platform(
            id: "aera", name: "AERA dot.", icon: "📝",
            accent: Color(red: 0.00, green: 0.27, blue: 0.58),
            bg: Color(red: 0.90, green: 0.94, blue: 1.0),
            fg: Color(red: 0.00, green: 0.27, blue: 0.58),
            rawPlatformValues: ["aera"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "dot.asahi.com"
        ),
        Platform(
            id: "hochi", name: "Hochi", icon: "🏅",
            accent: Color(red: 0.82, green: 0.10, blue: 0.10),
            bg: Color(red: 1.0, green: 0.92, blue: 0.92),
            fg: Color(red: 0.82, green: 0.10, blue: 0.10),
            rawPlatformValues: ["hochi"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "hochi.news"
        ),
        Platform(
            id: "sponichi", name: "Sponichi", icon: "⚽",
            accent: Color(red: 0.00, green: 0.27, blue: 0.60),
            bg: Color(red: 0.90, green: 0.94, blue: 1.0),
            fg: Color(red: 0.00, green: 0.27, blue: 0.60),
            rawPlatformValues: ["sponichi"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "sponichi.co.jp"
        ),
        Platform(
            id: "livedoor", name: "Livedoor", icon: "🔴",
            accent: Color(red: 0.88, green: 0.00, blue: 0.20),
            bg: Color(red: 1.0, green: 0.92, blue: 0.94),
            fg: Color(red: 0.88, green: 0.00, blue: 0.20),
            rawPlatformValues: ["livedoor"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "news.livedoor.com"
        ),
        Platform(
            id: "mantanweb", name: "Mantan Web", icon: "🎌",
            accent: Color(red: 0.07, green: 0.53, blue: 0.25),
            bg: Color(red: 0.90, green: 1.0, blue: 0.93),
            fg: Color(red: 0.07, green: 0.53, blue: 0.25),
            rawPlatformValues: ["mantanweb"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "mantan-web.jp"
        ),
        Platform(
            id: "realsound", name: "Real Sound", icon: "🎧",
            accent: Color(red: 0.18, green: 0.36, blue: 0.72),
            bg: Color(red: 0.91, green: 0.95, blue: 1.0),
            fg: Color(red: 0.18, green: 0.36, blue: 0.72),
            rawPlatformValues: ["realsound"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "realsound.jp"
        ),
        Platform(
            id: "cinemacafe", name: "CinemaCafe", icon: "🎬",
            accent: Color(red: 0.56, green: 0.20, blue: 0.64),
            bg: Color(red: 0.96, green: 0.91, blue: 0.98),
            fg: Color(red: 0.56, green: 0.20, blue: 0.64),
            rawPlatformValues: ["cinemacafe"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "cinemacafe.net"
        ),
        Platform(
            id: "thetv", name: "TheTV", icon: "📺",
            accent: Color(red: 0.02, green: 0.36, blue: 0.78),
            bg: Color(red: 0.90, green: 0.95, blue: 1.0),
            fg: Color(red: 0.02, green: 0.36, blue: 0.78),
            rawPlatformValues: ["thetv"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "thetv.jp"
        ),
        Platform(
            id: "barks", name: "BARKS", icon: "🎸",
            accent: Color(red: 0.13, green: 0.13, blue: 0.13),
            bg: Color(red: 0.93, green: 0.93, blue: 0.93),
            fg: Color(red: 0.13, green: 0.13, blue: 0.13),
            rawPlatformValues: ["barks"],
            usesStrictKeywordMatching: true, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceFallback,
            googleNewsFallbackSite: "barks.jp"
        ),
        Platform(
            id: "custom", name: "Custom Feeds", icon: "🌐",
            accent: .blue, bg: Color(red: 0.9, green: 0.95, blue: 1.0), fg: .blue,
            rawPlatformValues: ["custom"],
            usesStrictKeywordMatching: false, skipDateCutoff: false,
            isMediaPlatform: false, subscribedByDefault: true,
            fetchRole: .deviceOnly
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

    static func shouldFetchFromBackend(_ platformId: String) -> Bool {
        guard let platform = find(normalize(platformId)) else { return false }
        return platform.fetchRole != .deviceOnly
    }

    static func shouldUseDateWindowRefresh(_ platformId: String) -> Bool {
        find(normalize(platformId))?.usesActivityDateWindow == true
    }

    static func shouldRunDeviceFallback(_ platformId: String) -> Bool {
        guard let platform = find(normalize(platformId)) else { return false }
        return platform.fetchRole == .deviceFallback
    }

    static func googleNewsFallbackSites(for platformIds: Set<String>? = nil) -> [(site: String, platform: String)] {
        all.compactMap { platform in
            guard shouldInclude(platform.id, in: platformIds),
                  let site = platform.googleNewsFallbackSite else {
                return nil
            }
            return (site: site, platform: platform.id)
        }
    }

    static func usesNewsRSSFallback(for platformIds: Set<String>? = nil) -> Bool {
        all.contains { shouldInclude($0.id, in: platformIds) && $0.usesNewsRSSFallback }
    }

    static func usesNiconicoRSSFallback(for platformIds: Set<String>? = nil) -> Bool {
        all.contains { shouldInclude($0.id, in: platformIds) && $0.usesNiconicoRSSFallback }
    }

    private static func shouldInclude(_ platformId: String, in platformIds: Set<String>?) -> Bool {
        guard let platformIds else { return true }
        return platformIds.contains(platformId)
    }
}
