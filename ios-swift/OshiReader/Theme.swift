import SwiftUI

enum AppThemeMode: String, Codable, CaseIterable {
    case light = "light"
    case dark = "dark"
    case sepia = "sepia"
}

enum AppColorStyle: String, CaseIterable, Identifiable {
    case colourful = "colourful"
    case standard = "standard"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .colourful: return "Colourful"
        case .standard: return "Standard"
        }
    }
}

enum AppFontChoice: String, CaseIterable, Identifiable {
    case normal = "normal"
    case comicSans = "comic_sans"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .normal: return "Normal"
        case .comicSans: return "Comic Sans"
        }
    }

    var cssFamily: String {
        switch self {
        case .normal:
            return "-apple-system, BlinkMacSystemFont, \"Helvetica Neue\", Arial, sans-serif"
        case .comicSans:
            return "\"Comic Sans MS\", \"Comic Sans\", ChalkboardSE-Regular, Chalkboard, cursive"
        }
    }
}

enum AppFontSizeChoice: String, CaseIterable, Identifiable {
    case normal = "normal"
    case large = "large"
    case extraLarge = "extra_large"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .normal: return "Normal"
        case .large: return "Large"
        case .extraLarge: return "Extra Large"
        }
    }

    var scale: CGFloat {
        switch self {
        case .normal: return 1.0
        case .large: return 1.15
        case .extraLarge: return 1.3
        }
    }
}

struct AppColors {
    let mode: AppThemeMode
    
    var bg: Color {
        switch mode {
        case .light: return Color(red: 0.98, green: 0.98, blue: 1.0)
        case .dark:  return Color(red: 0.08, green: 0.08, blue: 0.1)
        case .sepia: return Color(red: 0.96, green: 0.93, blue: 0.86)
        }
    }
    
    var card: Color {
        switch mode {
        case .light: return Color.white
        case .dark:  return Color(red: 0.12, green: 0.12, blue: 0.15)
        case .sepia: return Color(red: 0.98, green: 0.96, blue: 0.91)
        }
    }
    
    var text: Color {
        switch mode {
        case .light: return Color(red: 0.1, green: 0.1, blue: 0.15)
        case .dark:  return Color(red: 0.95, green: 0.95, blue: 0.98)
        case .sepia: return Color(red: 0.22, green: 0.15, blue: 0.05)
        }
    }
    
    var textSub: Color {
        switch mode {
        case .light: return Color(red: 0.35, green: 0.35, blue: 0.45)
        case .dark:  return Color(red: 0.7, green: 0.7, blue: 0.78)
        case .sepia: return Color(red: 0.4, green: 0.3, blue: 0.15)
        }
    }
    
    var textMuted: Color {
        switch mode {
        case .light: return Color(red: 0.55, green: 0.55, blue: 0.65)
        case .dark:  return Color(red: 0.5, green: 0.5, blue: 0.58)
        case .sepia: return Color(red: 0.55, green: 0.48, blue: 0.38)
        }
    }
    
    var primary: Color {
        switch mode {
        case .light: return Color(red: 0.72, green: 0.52, blue: 0.65) // Opera mauve (#B784A7)
        case .dark:  return Color(red: 0.82, green: 0.64, blue: 0.76) // Light opera mauve
        case .sepia: return Color(red: 0.58, green: 0.35, blue: 0.0)  // Golden brown
        }
    }
    
    var primaryBg: Color {
        switch mode {
        case .light: return Color(red: 0.98, green: 0.93, blue: 0.96)
        case .dark:  return Color(red: 0.28, green: 0.18, blue: 0.24)
        case .sepia: return Color(red: 0.93, green: 0.88, blue: 0.78)
        }
    }
    
    var border: Color {
        switch mode {
        case .light: return Color(red: 0.9, green: 0.9, blue: 0.95)
        case .dark:  return Color(red: 0.2, green: 0.2, blue: 0.25)
        case .sepia: return Color(red: 0.88, green: 0.84, blue: 0.76)
        }
    }
    
    var divider: Color {
        switch mode {
        case .light: return Color(red: 0.93, green: 0.93, blue: 0.96)
        case .dark:  return Color(red: 0.16, green: 0.16, blue: 0.2)
        case .sepia: return Color(red: 0.9, green: 0.86, blue: 0.78)
        }
    }
    
    var accentGreen: Color {
        return Color(red: 0.13, green: 0.77, blue: 0.37) // #22C55E
    }
}

struct PlatformMetadata {
    let name: String
    let icon: String
    let accent: Color
    let bg: Color
    let fg: Color
}

class ThemeManager: ObservableObject {
    @Published var mode: AppThemeMode = .light {
        didSet { UserDefaults.standard.set(mode.rawValue, forKey: "app_theme_mode") }
    }

    @Published var style: AppColorStyle {
        didSet { UserDefaults.standard.set(style.rawValue, forKey: "app_color_style") }
    }
    
    static let shared = ThemeManager()

    private init() {
        let storedMode = UserDefaults.standard.string(forKey: "app_theme_mode") ?? AppThemeMode.light.rawValue
        self.mode = AppThemeMode(rawValue: storedMode) ?? .light
        let storedStyle = UserDefaults.standard.string(forKey: "app_color_style") ?? AppColorStyle.colourful.rawValue
        self.style = AppColorStyle(rawValue: storedStyle) ?? .colourful
    }
    
    var colors: AppColors {
        return AppColors(mode: mode)
    }

    var standardAccent: Color {
        switch mode {
        case .light: return Color(red: 0.72, green: 0.52, blue: 0.65)
        case .dark: return Color(red: 0.82, green: 0.64, blue: 0.76)
        case .sepia: return Color(red: 0.58, green: 0.35, blue: 0.0)
        }
    }

    var standardBadgeBg: Color {
        switch mode {
        case .light: return Color(red: 0.95, green: 0.94, blue: 0.98)
        case .dark: return Color(red: 0.19, green: 0.18, blue: 0.24)
        case .sepia: return Color(red: 0.93, green: 0.88, blue: 0.78)
        }
    }

    var standardBadgeFg: Color {
        switch mode {
        case .light: return Color(red: 0.28, green: 0.25, blue: 0.36)
        case .dark: return Color(red: 0.83, green: 0.82, blue: 0.9)
        case .sepia: return Color(red: 0.35, green: 0.25, blue: 0.12)
        }
    }
    
    func metadata(for platform: String) -> PlatformMetadata {
        let p = platform.lowercased()
        if p == "youtube" {
            return PlatformMetadata(name: "YouTube", icon: "📹", accent: Color.red, bg: Color(red: 1.0, green: 0.9, blue: 0.9), fg: Color.red)
        } else if p == "tver" {
            return PlatformMetadata(name: "TVer", icon: "📺", accent: Color.blue, bg: Color(red: 0.9, green: 0.95, blue: 1.0), fg: Color.blue)
        } else if p == "niconico" {
            return PlatformMetadata(name: "NicoNico", icon: "💬", accent: Color.black, bg: Color.gray.opacity(0.2), fg: Color.primary)
        } else if p == "yahoonews" {
            return PlatformMetadata(name: "YahooNews", icon: "🇯🇵", accent: Color(red: 0.86, green: 0.0, blue: 0.0), bg: Color(red: 1.0, green: 0.92, blue: 0.92), fg: Color(red: 0.86, green: 0.0, blue: 0.0))
        } else if p == "mdpr" {
            return PlatformMetadata(name: "ModelPress", icon: "💅", accent: Color.pink, bg: Color(red: 1.0, green: 0.9, blue: 0.95), fg: Color.pink)
        } else if p == "oricon" {
            return PlatformMetadata(name: "Oricon", icon: "🎤", accent: Color(red: 0.86, green: 0.12, blue: 0.22), bg: Color(red: 1.0, green: 0.92, blue: 0.94), fg: Color(red: 0.86, green: 0.12, blue: 0.22))
        } else if p == "twitter" {
            return PlatformMetadata(name: "X", icon: "𝕏", accent: Color.black, bg: Color.gray.opacity(0.16), fg: Color.primary)
        } else if p == "5ch" {
            return PlatformMetadata(name: "5ch", icon: "💬", accent: Color.orange, bg: Color(red: 1.0, green: 0.95, blue: 0.9), fg: Color.orange)
        } else if p == "girlschannel" {
            return PlatformMetadata(name: "GirlsChannel", icon: "👭", accent: Color.pink, bg: Color(red: 1.0, green: 0.92, blue: 0.95), fg: Color.pink)
        } else if p == "togetter" {
            return PlatformMetadata(name: "Togetter", icon: "🐧", accent: Color.green, bg: Color(red: 0.9, green: 0.98, blue: 0.92), fg: Color.green)
        } else if p == "note" {
            return PlatformMetadata(name: "Note", icon: "📝", accent: Color(red: 0.1, green: 0.7, blue: 0.5), bg: Color(red: 0.9, green: 0.97, blue: 0.95), fg: Color(red: 0.1, green: 0.7, blue: 0.5))
        } else if p == "news" {
            return PlatformMetadata(name: "News", icon: "📰", accent: Color.purple, bg: Color(red: 0.96, green: 0.9, blue: 1.0), fg: Color.purple)
        } else {
            return PlatformMetadata(name: platform.capitalized, icon: "🌐", accent: colors.primary, bg: colors.primaryBg, fg: colors.primary)
        }
    }
}

class AppearanceManager: ObservableObject {
    static let shared = AppearanceManager()

    @Published var fontChoice: AppFontChoice {
        didSet { UserDefaults.standard.set(fontChoice.rawValue, forKey: "app_font_choice") }
    }

    @Published var fontSizeChoice: AppFontSizeChoice {
        didSet { UserDefaults.standard.set(fontSizeChoice.rawValue, forKey: "app_font_size_choice") }
    }

    private init() {
        let storedFont = UserDefaults.standard.string(forKey: "app_font_choice") ?? AppFontChoice.normal.rawValue
        self.fontChoice = AppFontChoice(rawValue: storedFont) ?? .normal

        let storedSize = UserDefaults.standard.string(forKey: "app_font_size_choice") ?? AppFontSizeChoice.normal.rawValue
        self.fontSizeChoice = AppFontSizeChoice(rawValue: storedSize) ?? .normal
    }

    var appFont: Font {
        let baseSize = 17.0 * fontSizeChoice.scale
        switch fontChoice {
        case .normal:
            return .system(size: baseSize)
        case .comicSans:
            return .custom("Comic Sans MS", size: baseSize, relativeTo: .body)
        }
    }

    var readerFontSize: CGFloat {
        16.0 * fontSizeChoice.scale
    }

    var readerFontFamilyCSS: String {
        fontChoice.cssFamily
    }
}
