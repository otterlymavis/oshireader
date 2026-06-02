import SwiftUI
import UIKit

enum OshiTab: String, CaseIterable, Identifiable {
    case feed, saved, oshi, search, settings
    var id: String { rawValue }
}

struct ContentView: View {
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selectedTab: OshiTab = .feed
    
    init() {
        // Customize tab bar background/colors to match Otterpia aesthetics
        let appearance = UITabBarAppearance()
        appearance.configureWithOpaqueBackground()
        UITabBar.appearance().scrollEdgeAppearance = appearance
        UITabBar.appearance().standardAppearance = appearance
    }
    
    var body: some View {
        if horizontalSizeClass == .regular {
            NavigationSplitView {
                List {
                    ForEach(OshiTab.allCases) { tab in
                        Button(action: {
                            selectedTab = tab
                        }) {
                            HStack {
                                Image(systemName: icon(for: tab))
                                    .font(.title3)
                                    .foregroundColor(selectedTab == tab ? .white : theme.colors.primary)
                                    .frame(width: 28)
                                Text(title(for: tab))
                                    .font(.body)
                                    .fontWeight(selectedTab == tab ? .bold : .medium)
                                    .foregroundColor(selectedTab == tab ? .white : theme.colors.text)
                                Spacer()
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 10)
                            .background(selectedTab == tab ? theme.colors.primary : Color.clear)
                            .cornerRadius(10)
                        }
                        .buttonStyle(PlainButtonStyle())
                        .listRowInsets(EdgeInsets(top: 2, leading: 6, bottom: 2, trailing: 6))
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                    }
                }
                .navigationTitle(i18n.t("appTitle"))
                .listStyle(.sidebar)
            } detail: {
                switch selectedTab {
                case .feed:
                    FeedView()
                case .saved:
                    SavedView()
                case .oshi:
                    OshiView()
                case .search:
                    SearchView()
                case .settings:
                    SettingsView()
                }
            }
            .tint(theme.colors.primary)
            .preferredColorScheme(theme.mode == .dark ? .dark : .light)
        } else {
            TabView(selection: $selectedTab) {
                FeedView()
                    .tabItem {
                        Label(i18n.t("tabFeed"), systemImage: "house")
                    }
                    .tag(OshiTab.feed)
                
                SavedView()
                    .tabItem {
                        Label(i18n.t("tabSaved"), systemImage: "bookmark")
                    }
                    .tag(OshiTab.saved)
                
                OshiView()
                    .tabItem {
                        Label(i18n.t("tabOshi"), systemImage: "star")
                    }
                    .tag(OshiTab.oshi)
                
                SearchView()
                    .tabItem {
                        Label(i18n.t("tabSearch"), systemImage: "magnifyingglass")
                    }
                    .tag(OshiTab.search)
                
                SettingsView()
                    .tabItem {
                        Label(i18n.t("tabSettings"), systemImage: "gearshape")
                    }
                    .tag(OshiTab.settings)
            }
            .tint(theme.colors.primary)
            // Ensure standard backgrounds
            .background(theme.colors.bg.ignoresSafeArea())
            .preferredColorScheme(theme.mode == .dark ? .dark : .light)
        }
    }
    
    private func title(for tab: OshiTab) -> String {
        switch tab {
        case .feed: return i18n.t("tabFeed")
        case .saved: return i18n.t("tabSaved")
        case .oshi: return i18n.t("tabOshi")
        case .search: return i18n.t("tabSearch")
        case .settings: return i18n.t("tabSettings")
        }
    }
    
    private func icon(for tab: OshiTab) -> String {
        switch tab {
        case .feed: return "house"
        case .saved: return "bookmark"
        case .oshi: return "star"
        case .search: return "magnifyingglass"
        case .settings: return "gearshape"
        }
    }
}

#Preview {
    ContentView()
}
