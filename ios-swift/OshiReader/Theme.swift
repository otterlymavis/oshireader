import SwiftUI

enum AppThemeMode: String, Codable, CaseIterable {
    case light = "light"
    case dark = "dark"
    case sepia = "sepia"
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
        case .light: return Color(red: 0.49, green: 0.23, blue: 0.93) // Violet (#7C3AED)
        case .dark:  return Color(red: 0.65, green: 0.55, blue: 0.98) // Violet Light (#A78BFA)
        case .sepia: return Color(red: 0.58, green: 0.35, blue: 0.0)  // Golden brown
        }
    }
    
    var primaryBg: Color {
        switch mode {
        case .light: return Color(red: 0.95, green: 0.91, blue: 1.0)
        case .dark:  return Color(red: 0.19, green: 0.18, blue: 0.51)
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
    @Published var mode: AppThemeMode = .light
    
    static let shared = ThemeManager()
    
    var colors: AppColors {
        return AppColors(mode: mode)
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
