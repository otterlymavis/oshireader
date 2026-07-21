import SwiftUI
import UIKit
import UserNotifications

extension Notification.Name {
    static let oshiReaderForegroundRefreshRequested = Notification.Name("OshiReader.foregroundRefreshRequested")
}

enum OshiTab: String, CaseIterable, Identifiable, Hashable {
    case feed, search, saved, oshi, settings
    var id: String { rawValue }
}

struct ContentView: View {
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    @StateObject private var appearance = AppearanceManager.shared
    @StateObject private var notificationNavigation = NotificationNavigationManager.shared
    
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedTab: OshiTab = ProcessInfo.processInfo.arguments.contains("--uitesting-start-search") ? .search : .feed
    @State private var offscreenForegroundRefreshTask: Task<Void, Never>? = nil
    @State private var offscreenForegroundRefreshID: UUID? = nil
    
    init() {
        // Customize tab bar background/colors to match Otterpia aesthetics
        let appearance = UITabBarAppearance()
        appearance.configureWithOpaqueBackground()
        UITabBar.appearance().scrollEdgeAppearance = appearance
        UITabBar.appearance().standardAppearance = appearance
    }
    
    var body: some View {
        Group {
            if horizontalSizeClass == .regular {
            NavigationSplitView {
                List {
                    ForEach(OshiTab.allCases) { tab in
                        Button(action: {
                            selectedTab = tab
                        }) {
                            sidebarRowContent(for: tab)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(title(for: tab))
                        .accessibilityIdentifier("tab.\(tab.rawValue)")
                        .accessibilityAction {
                            selectedTab = tab
                        }
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
                case .search:
                    SearchView()
                case .saved:
                    SavedView()
                case .oshi:
                    OshiView()
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
                            .accessibilityIdentifier("tab.feed")
                    }
                    .tag(OshiTab.feed)
                
                SearchView()
                    .tabItem {
                        Label(i18n.t("tabSearch"), systemImage: "magnifyingglass")
                            .accessibilityIdentifier("tab.search")
                    }
                    .tag(OshiTab.search)

                SavedView()
                    .tabItem {
                        Label(i18n.t("tabSaved"), systemImage: "bookmark")
                            .accessibilityIdentifier("tab.saved")
                    }
                    .tag(OshiTab.saved)
                
                OshiView()
                    .tabItem {
                        Label(i18n.t("tabOshi"), systemImage: "star")
                            .accessibilityIdentifier("tab.oshi")
                    }
                    .tag(OshiTab.oshi)
                
                SettingsView()
                    .tabItem {
                        Label(i18n.t("tabSettings"), systemImage: "gearshape")
                            .accessibilityIdentifier("tab.settings")
                    }
                    .tag(OshiTab.settings)
            }
            .tint(theme.colors.primary)
            // Ensure standard backgrounds
            .background(theme.colors.bg.ignoresSafeArea())
            .preferredColorScheme(theme.mode == .dark ? .dark : .light)
            }
        }
        .font(appearance.appFont)
        .ifLet(appearance.dynamicTypeSizeOverride) { view, size in
            view.environment(\.dynamicTypeSize, size)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                UNUserNotificationCenter.current().setBadgeCount(0)
                requestForegroundRefreshForActiveScene()
            } else if phase == .background {
                offscreenForegroundRefreshTask?.cancel()
                offscreenForegroundRefreshTask = nil
                offscreenForegroundRefreshID = nil
                BackgroundRefreshManager.shared.schedule()
            }
        }
        .onReceive(notificationNavigation.$selectedItem) { item in
            if item != nil {
                selectedTab = .feed
            }
        }
        .onChange(of: selectedTab) { _, newTab in
            guard newTab == .feed else { return }
            offscreenForegroundRefreshTask?.cancel()
            offscreenForegroundRefreshTask = nil
            offscreenForegroundRefreshID = nil
        }
        .sheet(item: $notificationNavigation.selectedItem) { item in
            NavigationStack {
                ReaderView(feedItem: item)
            }
            .preferredColorScheme(theme.mode == .dark ? .dark : .light)
        }
    }

    private func requestForegroundRefreshForActiveScene() {
        if selectedTab == .feed {
            NotificationCenter.default.post(name: .oshiReaderForegroundRefreshRequested, object: nil)
            return
        }
        launchOffscreenForegroundRefreshIfNeeded()
    }

    private func launchOffscreenForegroundRefreshIfNeeded() {
        guard offscreenForegroundRefreshTask == nil,
              offscreenForegroundRefreshID == nil,
              !NetworkManager.shared.isUnitTesting,
              !NetworkManager.shared.isUITesting else { return }
        let refreshID = UUID()
        offscreenForegroundRefreshID = refreshID
        offscreenForegroundRefreshTask = Task { @MainActor in
            defer {
                if offscreenForegroundRefreshID == refreshID {
                    offscreenForegroundRefreshTask = nil
                    offscreenForegroundRefreshID = nil
                }
            }
            let db = LocalDB.shared
            let pushedTerms = await NetworkManager.shared.syncWatchTermsToBackend(localTerms: db.terms)
            guard !Task.isCancelled else { return }
            let pulledTerms = await NetworkManager.shared.syncTermsFromBackendWithStatus()
            guard !Task.isCancelled else { return }
            let feedScopeRevision = BackgroundRefreshPolicy.currentFeedScopeRevision
            guard BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: db.terms.filter(\.is_active),
                customUrls: db.customUrls,
                subscribedPlatforms: db.subscribedPlatforms,
                items: db.feedItems,
                pulledNewTerms: pulledTerms.changed
            ) else { return }
            _ = await BackgroundRefreshManager.shared.refreshForBackground(
                notifyOnNew: false,
                skipTermSync: pushedTerms && pulledTerms.succeeded,
                feedScopeRevision: feedScopeRevision
            )
        }
    }

    private func sidebarRowContent(for tab: OshiTab) -> some View {
        let isSelected = selectedTab == tab

        return HStack {
            Image(systemName: icon(for: tab))
                .font(.title3)
                .frame(width: 28)
            Text(title(for: tab))
                .font(.body)
                .fontWeight(isSelected ? .bold : .medium)
            Spacer()
        }
        .foregroundColor(isSelected ? .white : theme.colors.text)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(isSelected ? theme.colors.primary : Color.clear)
        .cornerRadius(10)
        .contentShape(Rectangle())
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

private extension View {
    @ViewBuilder
    func ifLet<T>(_ value: T?, transform: (Self, T) -> some View) -> some View {
        if let value {
            transform(self, value)
        } else {
            self
        }
    }
}

#Preview {
    ContentView()
}
