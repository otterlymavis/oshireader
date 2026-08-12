import Foundation
import ImageIO
import SwiftUI
import UIKit

/// Shared thumbnail loading for feed cards.
///
/// Feed cards only display small images, so decoding the full source image wastes
/// memory and can cause scroll hitches. The actor also prevents concurrent access
/// to the shared in-memory cache.
actor FeedThumbnailLoader {
    static let shared = FeedThumbnailLoader()

    private let cache = NSCache<NSURL, UIImage>()
    private let session: URLSession
    private let maxPixelSize: Int
    private var inFlight: [URL: Task<UIImage?, Never>] = [:]

    init(session: URLSession = .shared, maxPixelSize: Int = 144) {
        self.session = session
        self.maxPixelSize = maxPixelSize
        cache.countLimit = 100
        cache.totalCostLimit = 24 * 1024 * 1024
    }

    func image(for url: URL) async -> UIImage? {
        if let cached = cache.object(forKey: url as NSURL) {
            return cached
        }

        if let existingTask = inFlight[url] {
            return await existingTask.value
        }

        let session = session
        let maxPixelSize = maxPixelSize
        let task = Task { () -> UIImage? in
            guard !Task.isCancelled,
                  let (data, response) = try? await session.data(from: url),
                  let httpResponse = response as? HTTPURLResponse,
                  200..<300 ~= httpResponse.statusCode,
                  !Task.isCancelled else {
                return nil
            }
            return Self.downsample(data: data, maxPixelSize: maxPixelSize)
        }
        inFlight[url] = task
        let image = await task.value
        inFlight[url] = nil

        if let image {
            cache.setObject(image, forKey: url as NSURL, cost: imageCost(image))
        }
        return image
    }

    private func imageCost(_ image: UIImage) -> Int {
        guard let cgImage = image.cgImage else { return 1 }
        return cgImage.bytesPerRow * cgImage.height
    }

    nonisolated static func downsample(data: Data, maxPixelSize: Int) -> UIImage? {
        guard maxPixelSize > 0,
              let source = CGImageSourceCreateWithData(data as CFData, nil),
              let cgImage = CGImageSourceCreateThumbnailAtIndex(
                source,
                0,
                [
                    kCGImageSourceCreateThumbnailFromImageAlways: true,
                    kCGImageSourceCreateThumbnailWithTransform: true,
                    kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
                ] as CFDictionary
              ) else {
            return nil
        }
        return UIImage(cgImage: cgImage)
    }
}

private struct FeedThumbnailView: View {
    let url: URL
    let size: CGFloat

    @State private var image: UIImage?

    init(url: URL, size: CGFloat = 72) {
        self.url = url
        self.size = size
    }

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                Color.gray.opacity(0.1)
            }
        }
        .frame(width: size, height: size)
        .clipped()
        .cornerRadius(8)
        .task(id: url) {
            image = nil
            let loadedImage = await FeedThumbnailLoader.shared.image(for: url)
            guard !Task.isCancelled else { return }
            image = loadedImage
        }
    }
}

private struct FeedRefreshAttempt {
    let strategy: String
    let status: String
    let itemCount: Int
    let addedCount: Int
    let detail: String?
    let completedDevicePlatforms: Set<String>
    let items: [FeedItem]

    init(
        strategy: String,
        status: String,
        itemCount: Int = 0,
        addedCount: Int = 0,
        detail: String? = nil,
        completedDevicePlatforms: Set<String> = [],
        items: [FeedItem] = []
    ) {
        self.strategy = strategy
        self.status = status
        self.itemCount = itemCount
        self.addedCount = addedCount
        self.detail = detail
        self.completedDevicePlatforms = completedDevicePlatforms
        self.items = items
    }

    var diagnosticEvent: ClientDiagnosticEvent {
        ClientDiagnosticEvent(
            strategy: strategy,
            status: status,
            item_count: itemCount,
            added_count: addedCount,
            detail: detail
        )
    }
}

private struct FeedRefreshReport {
    var attempts: [FeedRefreshAttempt] = []
    var backendReturnedItems = false
    var addedCount = 0
    var completedBackendPlatforms = Set<String>()
    var backendCompletedCheck = false
    var backendConnectivityFailed = false
    var customCompletedCheck = false
    var completedDevicePlatforms = Set<String>()
    var feedScopeRevision: Int?

    mutating func record(
        strategy: String,
        status: String,
        itemCount: Int = 0,
        addedCount: Int = 0,
        detail: String? = nil,
        completedDevicePlatforms: Set<String> = []
    ) {
        attempts.append(FeedRefreshAttempt(
            strategy: strategy,
            status: status,
            itemCount: itemCount,
            addedCount: addedCount,
            detail: detail,
            completedDevicePlatforms: completedDevicePlatforms
        ))
        if Self.didBackendReturnItems(strategy: strategy, itemCount: itemCount) {
            backendReturnedItems = true
        }
        updateCompletedChecks(
            strategy: strategy,
            status: status,
            completedDevicePlatforms: completedDevicePlatforms
        )
        self.addedCount += addedCount
    }

    mutating func append(_ attempt: FeedRefreshAttempt) {
        attempts.append(attempt)
        if Self.didBackendReturnItems(strategy: attempt.strategy, itemCount: attempt.itemCount) {
            backendReturnedItems = true
        }
        updateCompletedChecks(
            strategy: attempt.strategy,
            status: attempt.status,
            completedDevicePlatforms: attempt.completedDevicePlatforms
        )
        addedCount += attempt.addedCount
    }

    mutating func append(_ other: FeedRefreshReport) {
        attempts.append(contentsOf: other.attempts)
        backendReturnedItems = backendReturnedItems || other.backendReturnedItems
        completedBackendPlatforms.formUnion(other.completedBackendPlatforms)
        backendCompletedCheck = backendCompletedCheck || other.backendCompletedCheck
        backendConnectivityFailed = backendConnectivityFailed || other.backendConnectivityFailed
        customCompletedCheck = customCompletedCheck || other.customCompletedCheck
        completedDevicePlatforms.formUnion(other.completedDevicePlatforms)
        if feedScopeRevision == nil {
            feedScopeRevision = other.feedScopeRevision
        }
        addedCount += other.addedCount
    }

    mutating func markCompletedDevicePlatforms(_ platforms: Set<String>) {
        completedDevicePlatforms.formUnion(platforms)
    }

    private static func didBackendReturnItems(strategy: String, itemCount: Int) -> Bool {
        guard itemCount > 0 else { return false }
        return strategy.hasPrefix("backend_feed") || strategy.hasPrefix("backend_platform_")
    }

    private mutating func updateCompletedChecks(
        strategy: String,
        status: String,
        completedDevicePlatforms: Set<String>
    ) {
        guard status == "items" || status == "empty" else { return }
        if strategy.hasPrefix("backend_feed") || strategy.hasPrefix("backend_platform_") {
            backendCompletedCheck = true
            if let platform = Self.backendPlatformID(from: strategy) {
                completedBackendPlatforms.insert(platform)
            }
        } else if strategy == "custom_urls" {
            customCompletedCheck = true
        } else if strategy == "device_local_scrapers" {
            self.completedDevicePlatforms.formUnion(completedDevicePlatforms)
        }
    }

    private static func backendPlatformID(from strategy: String) -> String? {
        guard strategy.hasPrefix("backend_platform_") else { return nil }
        return String(strategy.dropFirst("backend_platform_".count))
    }
}

struct FeedView: View {
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    
    @State private var selectedKeyword: String? = nil
    @State private var selectedPlatform: String? = nil
    @State private var mediaFilter: MediaFilter = .all
    @State private var daysFilter: Int = 30
    
    @State private var isRefreshing = false
    @State private var isScrapingFallback = false
    @State private var refreshTask: Task<Void, Never>? = nil
    @State private var foregroundRefreshTask: Task<Void, Never>? = nil
    @State private var refreshErrorMessage: String? = nil
    @State private var hasLoadedOnce = false
    @State private var displayedCount: Int = 20
    @State private var cachedFilteredItems: [FeedItem] = []
    @State private var cachedVisibleItems: [FeedItem] = []
    @State private var showFilterSheet = false
    @State private var showAddUrlSheet = false
    @State private var showReorderSheet = false
    @State private var pendingHiddenFeedItem: FeedItem? = nil
    
    @State private var customUrlString = ""
    @State private var customUrlTitle = ""
    
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selectedItem: FeedItem? = nil

    init() {
        let db = LocalDB.shared
        let initialFilteredItems = Self.makeFilteredItems(
            db: db,
            keyword: nil,
            platform: nil,
            mediaFilter: .all,
            days: 30
        )
        _cachedFilteredItems = State(initialValue: initialFilteredItems)
        _cachedVisibleItems = State(initialValue: Array(initialFilteredItems.prefix(20)))
    }
    
    private let timeRanges = [
        (label: "allTime", days: 0),
        (label: "days3", days: 3),
        (label: "month1", days: 30),
        (label: "months3", days: 90),
        (label: "months6", days: 180)
    ]
    
    private var savedItemIds: Set<String> {
        Set(db.savedPages.map(\.id))
    }

    private var canLoadMore: Bool {
        displayedCount < min(cachedFilteredItems.count, 100)
    }

    private var remainingLoadMoreCount: Int {
        max(min(cachedFilteredItems.count, 100) - displayedCount, 0)
    }

    // Full filtered list (all matching items). This is intentionally kept as the
    // single projection used to refresh the cached list so FeedView preserves the
    // existing LocalDB query semantics while avoiding repeated body-time work.
    private func makeFilteredItems() -> [FeedItem] {
        Self.makeFilteredItems(
            db: db,
            keyword: selectedKeyword,
            platform: selectedPlatform,
            mediaFilter: mediaFilter,
            days: daysFilter
        )
    }

    private static func makeFilteredItems(
        db: LocalDB,
        keyword: String?,
        platform: String?,
        mediaFilter: MediaFilter,
        days: Int
    ) -> [FeedItem] {
        var result = db.queryFeed(keyword: keyword, days: days)
        if let platform {
            result = result.filter { matchesPlatform($0, platformId: platform) }
        }
        if mediaFilter == .mediaOnly {
            result = result.filter { $0.media_type == "video" || $0.media_type == "image" || Platform.forRawValue($0.platform)?.isMediaPlatform == true }
        }
        return result
    }
    
    var orderedPlatforms: [String] {
        let subs = db.subscribedPlatforms
        guard let order = db.sourcesOrder else { return subs }
        let orderSet = Set(order)
        let ordered = order.filter { subs.contains($0) }
        let unordered = subs.filter { !orderSet.contains($0) }
        return ordered + unordered
    }
    
    var body: some View {
        ZStack {
            theme.colors.bg.ignoresSafeArea()
            
            // Custom Wallpaper (from localDB) — a remote URL or a locally rendered
            // composition file (file://). Loaded off the main thread and cached so the
            // feed body doesn't read disk on every render.
            if let wallpaperUrl = db.wallpaper {
                WallpaperBackgroundView(urlString: wallpaperUrl)
            }
            
            if horizontalSizeClass == .regular {
                HStack(spacing: 0) {
                    NavigationStack {
                        mainContentColumn
                    }
                    .frame(width: 380)
                    
                    Divider()
                        .background(theme.colors.divider)
                    
                    NavigationStack {
                        if let item = selectedItem {
                            ReaderView(feedItem: item)
                                .id(item.id)
                        } else {
                            VStack(spacing: 16) {
                                Text("📖")
                                    .font(.system(size: 64))
                                Text(i18n.t("feedSelectArticle"))
                                    .font(.headline)
                                    .foregroundColor(theme.colors.textSub)
                            }
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .background(theme.colors.bg)
                        }
                    }
                }
            } else {
                NavigationStack {
                    mainContentColumn
                }
            }
        }
    }
    
    private var mainContentColumn: some View {
        ZStack(alignment: .bottomTrailing) {
            VStack(spacing: 0) {
                // Filter Summary bar (collapsible trigger)
                Button(action: { showFilterSheet.toggle() }) {
                    HStack {
                        Image(systemName: "slider.horizontal.3")
                            .foregroundColor(filterCount > 0 ? theme.colors.primary : theme.colors.textMuted)
                        Text(i18n.t("filter"))
                            .font(.subheadline)
                            .fontWeight(.semibold)
                            .foregroundColor(filterCount > 0 ? theme.colors.primary : theme.colors.textSub)
                        
                        if filterCount > 0 {
                            Text("\(filterCount)")
                                .font(.caption2)
                                .bold()
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(theme.colors.primary)
                                .foregroundColor(.white)
                                .clipShape(Capsule())
                        }
                        
                        if let sk = selectedKeyword {
                            PillView(text: sk, theme: theme)
                        }
                        if let sp = selectedPlatform {
                            let meta = theme.metadata(for: sp)
                            PillView(text: "\(meta.icon) \(meta.name)", bgColor: meta.bg, fgColor: meta.fg)
                        }
                        if mediaFilter == .mediaOnly {
                            PillView(text: "📹 " + i18n.t("mediaOnly"), theme: theme)
                        }

                        Spacer()
                        Image(systemName: showFilterSheet ? "chevron.up" : "chevron.down")
                            .foregroundColor(theme.colors.textMuted)
                            .font(.caption)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(theme.colors.card)
                    .overlay(
                        Rectangle()
                            .frame(height: 0.5)
                            .foregroundColor(theme.colors.divider),
                        alignment: .bottom
                    )
                }
                .accessibilityIdentifier("feed.filterButton")
                
                // Horizontal platform strip
                if !orderedPlatforms.isEmpty {
                    HStack(spacing: 0) {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 8) {
                                // "All" button
                                Button(action: {
                                    selectedPlatform = nil
                                    foregroundRefreshTask?.cancel()
                                    refreshTask?.cancel()
                                    let prevForeground = foregroundRefreshTask
                                    let prevRefresh = refreshTask
                                    refreshTask = Task {
                                        await prevForeground?.value
                                        await prevRefresh?.value
                                        await refreshFeed()
                                    }
                                }) {
                                    VStack(spacing: 3) {
                                        Text("🌐")
                                            .font(.system(size: 18))
                                        Text(i18n.t("all"))
                                            .font(.system(size: 11, weight: selectedPlatform == nil ? .bold : .medium))
                                            .foregroundColor(selectedPlatform == nil ? .white : theme.colors.textMuted)
                                    }
                                    .frame(width: 58, height: 58)
                                    .background(selectedPlatform == nil ? theme.colors.primary : theme.colors.divider)
                                    .cornerRadius(10)
                                }
                                .accessibilityIdentifier("feed.platform.all")
                                
                                // Individual platforms
                                ForEach(orderedPlatforms, id: \.self) { platformId in
                                    let meta = theme.metadata(for: platformId)
                                    let isSelected = selectedPlatform == platformId
                                    let bg = theme.style == .standard
                                        ? (isSelected ? theme.colors.primary : theme.standardBadgeBg)
                                        : (isSelected ? meta.accent : meta.bg)
                                    let fg = theme.style == .standard
                                        ? (isSelected ? Color.white : theme.standardBadgeFg)
                                        : (isSelected ? Color.white : meta.fg)
                                    Button(action: {
                                        selectedPlatform = isSelected ? nil : platformId
                                        foregroundRefreshTask?.cancel()
                                        refreshTask?.cancel()
                                        let prevForeground = foregroundRefreshTask
                                        let prevRefresh = refreshTask
                                        refreshTask = Task {
                                            await prevForeground?.value
                                            await prevRefresh?.value
                                            if isSelected {
                                                await refreshFeed()
                                            } else {
                                                await refreshFeed(selectedPlatformOverride: platformId)
                                            }
                                        }
                                    }) {
                                        VStack(spacing: 3) {
                                            Text(meta.icon)
                                                .font(.system(size: 18))
                                            Text(meta.name)
                                                .font(.system(size: 11, weight: isSelected ? .bold : .medium))
                                                .foregroundColor(fg)
                                                .lineLimit(1)
                                        }
                                        .frame(width: 58, height: 58)
                                        .background(bg)
                                        .cornerRadius(10)
                                    }
                                    .accessibilityIdentifier("feed.platform.\(platformId)")
                                }
                            }
                            .padding(.horizontal, 14)
                            .padding(.vertical, 6)
                        }
                        
                        // Reorder button
                        Button(action: { showReorderSheet.toggle() }) {
                            Text("≡")
                                .font(.title3)
                                .foregroundColor(theme.colors.textMuted)
                                .frame(width: 44, height: 58)
                                .background(theme.colors.divider)
                                .cornerRadius(10)
                                .padding(.trailing, 10)
                        }
                        .accessibilityIdentifier("feed.reorderSourcesButton")
                    }
                    .background(theme.colors.card)
                    .overlay(
                        Rectangle()
                            .frame(height: 0.5)
                            .foregroundColor(theme.colors.divider),
                        alignment: .bottom
                    )
                }
                
                // Fallback scraper banner — shown while offline local sources are searched
                if isScrapingFallback {
                    HStack(spacing: 6) {
                        ProgressView()
                            .scaleEffect(0.7)
                            .tint(theme.colors.textMuted)
                        Text(i18n.t("feedSearchingOffline"))
                            .font(.caption)
                            .foregroundColor(theme.colors.textMuted)
                        Spacer()
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 6)
                    .background(theme.colors.card)
                    .overlay(
                        Rectangle().frame(height: 0.5).foregroundColor(theme.colors.divider),
                        alignment: .bottom
                    )
                    .accessibilityIdentifier("feed.fallbackBanner")
                }

                // Offline / error banner — shown until the next successful refresh
                if let msg = refreshErrorMessage {
                    HStack(spacing: 6) {
                        Image(systemName: "wifi.slash")
                            .font(.caption2)
                        Text(i18n.t(msg))
                            .font(.caption)
                        Spacer()
                        Button {
                            refreshErrorMessage = nil
                        } label: {
                            Image(systemName: "xmark")
                                .font(.caption2)
                        }
                    }
                    .foregroundColor(.orange)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 7)
                    .background(Color.orange.opacity(0.08))
                    .overlay(
                        Rectangle().frame(height: 0.5).foregroundColor(Color.orange.opacity(0.25)),
                        alignment: .bottom
                    )
                    .accessibilityIdentifier("feed.errorBanner")
                }

                // Main Feed List
                if isRefreshing && cachedFilteredItems.isEmpty {
                    Spacer()
                    ProgressView()
                        .tint(theme.colors.primary)
                    Spacer()
                } else if cachedFilteredItems.isEmpty {
                    Spacer()
                    VStack(spacing: 12) {
                        Text("≽՞•ﻌ•՞≼")
                            .font(.system(size: 40))
                        Text(i18n.t("feedEmpty"))
                            .font(.headline)
                            .foregroundColor(theme.colors.primary)
                        Text(i18n.t("feedEmptyBody"))
                            .font(.subheadline)
                            .foregroundColor(theme.colors.textMuted)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 32)
                    }
                    Spacer()
                } else {
                    let savedIds = savedItemIds
                    List {
                        ForEach(cachedVisibleItems) { item in
                            if horizontalSizeClass == .regular {
                                Button(action: { selectedItem = item }) {
                                    FeedCard(item: item, isSaved: savedIds.contains(item.id), theme: theme)
                                        .overlay(
                                            RoundedRectangle(cornerRadius: 12)
                                                .stroke(theme.colors.primary, lineWidth: selectedItem?.id == item.id ? 2 : 0)
                                        )
                                }
                                .buttonStyle(PlainButtonStyle())
                                .accessibilityIdentifier("feed.card")
                                .listRowInsets(EdgeInsets(top: 4, leading: 14, bottom: 4, trailing: 14))
                                .listRowBackground(Color.clear)
                                .listRowSeparator(.hidden)
                                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                    hidePostButton(for: item)
                                    Button(role: .destructive) {
                                        deleteFeedItem(item, clearSelection: true)
                                    } label: {
                                        Label(i18n.t("delete"), systemImage: "trash")
                                    }
                                }
                                .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                    saveToggleButton(for: item)
                                }
                                .contextMenu {
                                    saveToggleButton(for: item)
                                    hidePostButton(for: item)
                                }
                            } else {
                                NavigationLink(destination: ReaderView(feedItem: item)) {
                                    FeedCard(item: item, isSaved: savedIds.contains(item.id), theme: theme)
                                }
                                .buttonStyle(PlainButtonStyle())
                                .accessibilityIdentifier("feed.card")
                                .listRowInsets(EdgeInsets(top: 4, leading: 14, bottom: 4, trailing: 14))
                                .listRowBackground(Color.clear)
                                .listRowSeparator(.hidden)
                                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                    hidePostButton(for: item)
                                    Button(role: .destructive) {
                                        deleteFeedItem(item, clearSelection: false)
                                    } label: {
                                        Label(i18n.t("delete"), systemImage: "trash")
                                    }
                                }
                                .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                    saveToggleButton(for: item)
                                }
                                .contextMenu {
                                    saveToggleButton(for: item)
                                    hidePostButton(for: item)
                                }
                            }
                        }

                        if canLoadMore {
                            Button {
                                loadMoreFeedItems()
                            } label: {
                                HStack {
                                    Spacer()
                                    Text(i18n.tFormat("feedLoadMoreFmt", remainingLoadMoreCount))
                                        .font(.subheadline)
                                        .foregroundColor(theme.colors.primary)
                                    Spacer()
                                }
                                .padding(.vertical, 12)
                            }
                            .listRowBackground(Color.clear)
                            .listRowSeparator(.hidden)
                        }
                    }
                    .listStyle(.plain)
                    .refreshable {
                        await refreshFeed()
                    }
                }
            }
            
            // Floating Action Button
            Button(action: { showAddUrlSheet.toggle() }) {
                Image(systemName: "plus")
                    .font(.title2)
                    .foregroundColor(.white)
                    .frame(width: 52, height: 52)
                    .background(theme.colors.primary)
                    .clipShape(Circle())
                    .shadow(color: Color.black.opacity(0.25), radius: 8, x: 0, y: 3)
            }
            .accessibilityIdentifier("feed.addCustomUrlButton")
            .padding(.trailing, 20)
            .padding(.bottom, 24)
        }
        .navigationTitle(i18n.t("appTitle"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                if isRefreshing {
                    ProgressView()
                        .tint(theme.colors.primary)
                } else {
                    Button(action: { launchRefresh() }) {
                        Image(systemName: "arrow.clockwise")
                            .foregroundColor(theme.colors.primary)
                    }
                    .accessibilityIdentifier("feed.refreshButton")
                }
            }
        }
        .sheet(isPresented: $showFilterSheet) {
            FilterPanel(selectedKeyword: $selectedKeyword, mediaFilter: $mediaFilter, daysFilter: $daysFilter, theme: theme, i18n: i18n, timeRanges: timeRanges)
                .presentationDetents([.medium])
        }
        .sheet(isPresented: $showAddUrlSheet) {
            AddUrlSheet(customUrlString: $customUrlString, customUrlTitle: $customUrlTitle, theme: theme, i18n: i18n) {
                db.addCustomUrl(url: customUrlString, title: customUrlTitle)
                Task {
                    let customItems = await NetworkManager.shared.scrapeCustomUrls(db.customUrls)
                    let currentItems = db.currentCustomFeedItems(customItems)
                    if !currentItems.isEmpty {
                        _ = db.mergeItems(newItems: currentItems)
                        db.flushPendingFeedItemsSave()
                    }
                }
                customUrlString = ""
                customUrlTitle = ""
                showAddUrlSheet = false
            }
            .presentationDetents([.medium])
        }
        .sheet(isPresented: $showReorderSheet) {
            ReorderSourcesSheet(theme: theme, i18n: i18n)
        }
        .alert(
            i18n.tFormat("hidePostTitleFmt", pendingHiddenFeedItem?.title ?? pendingHiddenFeedItem?.watch_term_keyword ?? ""),
            isPresented: Binding(
                get: { pendingHiddenFeedItem != nil },
                set: { if !$0 { pendingHiddenFeedItem = nil } }
            )
        ) {
            Button(i18n.t("cancel"), role: .cancel) {
                pendingHiddenFeedItem = nil
            }
            Button(i18n.t("hidePostConfirm"), role: .destructive) {
                confirmHidePost()
            }
        } message: {
            Text(i18n.t("hidePostMessage"))
        }
        .accessibilityIdentifier("feed.screen")
        .onChange(of: selectedKeyword) { rebuildFeedCache(resetDisplayedCount: true) }
        .onChange(of: selectedPlatform) { rebuildFeedCache(resetDisplayedCount: true) }
        .onChange(of: daysFilter) { _, newDays in
            rebuildFeedCache(resetDisplayedCount: true)
            if newDays == 0 { launchRefresh() }
        }
        .onChange(of: mediaFilter) { rebuildFeedCache(resetDisplayedCount: true) }
        .onChange(of: db.feedItems) { rebuildFeedCache() }
        .onChange(of: db.hiddenItems) { rebuildFeedCache() }
        .onChange(of: db.subscribedPlatforms) { rebuildFeedCache() }
        .onChange(of: db.terms) { rebuildFeedCache() }
        .onAppear {
            rebuildFeedCache()
            guard !hasLoadedOnce else { return }
            launchForegroundRefreshIfNeeded()
        }
        .onReceive(NotificationCenter.default.publisher(for: .oshiReaderForegroundRefreshRequested)) { _ in
            rebuildFeedCache()
            launchForegroundRefreshIfNeeded()
        }
        .onDisappear {
            foregroundRefreshTask?.cancel()
            refreshTask?.cancel()
        }
    }

    private func launchForegroundRefreshIfNeeded() {
        guard !isRefreshing else { return }
        foregroundRefreshTask?.cancel()
        foregroundRefreshTask = Task {
            defer {
                if !Task.isCancelled {
                    hasLoadedOnce = true
                }
            }
            // Push local terms to backend (handles post-DB-reset state)
            let pushedTerms = await NetworkManager.shared.syncWatchTermsToBackend(
                localTerms: db.terms,
                timeout: BackgroundRefreshPolicy.foregroundBackendTimeout
            )
            guard !Task.isCancelled else { return }
            // Pull backend terms that aren't local yet (fresh install / multi-device)
            let pulledTerms = pushedTerms
                ? await NetworkManager.shared.syncTermsFromBackendWithStatus(
                    timeout: BackgroundRefreshPolicy.foregroundBackendTimeout
                )
                : BackendTermSyncResult(succeeded: false, changed: false)
            guard !Task.isCancelled else { return }
            let feedScopeRevision = BackgroundRefreshPolicy.currentFeedScopeRevision
            if BackgroundRefreshPolicy.shouldLaunchForegroundRefresh(
                activeTerms: db.terms.filter(\.is_active),
                customUrls: db.customUrls,
                subscribedPlatforms: db.subscribedPlatforms,
                items: db.feedItems,
                pulledNewTerms: pulledTerms.changed
            ) {
                await refreshFeed(
                    skipTermSync: pushedTerms && pulledTerms.succeeded,
                    feedScopeRevision: feedScopeRevision
                )
            }
        }
    }
    
    // Cancel any in-flight task then launch a new refresh.
    // Guards on isRefreshing so we never lose the handle to an already-running task.
    private func launchRefresh() {
        guard !isRefreshing else { return }
        foregroundRefreshTask?.cancel()
        refreshTask?.cancel()
        refreshTask = Task { await refreshFeed() }
    }

    private var filterCount: Int {
        var count = 0
        if selectedKeyword != nil { count += 1 }
        if selectedPlatform != nil { count += 1 }
        if mediaFilter == .mediaOnly { count += 1 }
        return count
    }

    private func rebuildFeedCache(resetDisplayedCount: Bool = false) {
        let effectiveDisplayedCount = resetDisplayedCount ? 20 : displayedCount
        if displayedCount != effectiveDisplayedCount {
            displayedCount = effectiveDisplayedCount
        }

        let filtered = makeFilteredItems()
        if cachedFilteredItems != filtered {
            cachedFilteredItems = filtered
        }
        updateVisibleFeedCache(displayedCount: effectiveDisplayedCount, filteredItems: filtered)
    }

    private func updateVisibleFeedCache(displayedCount: Int, filteredItems: [FeedItem]? = nil) {
        let sourceItems = filteredItems ?? cachedFilteredItems
        let visible = Array(sourceItems.prefix(displayedCount))
        if cachedVisibleItems != visible {
            cachedVisibleItems = visible
        }
    }

    private func loadMoreFeedItems() {
        let nextCount = min(displayedCount + 20, 100)
        guard nextCount != displayedCount else { return }
        displayedCount = nextCount
        updateVisibleFeedCache(displayedCount: nextCount)
    }

    private func recordCompletedRefreshCheckIfNeeded(
        _ report: FeedRefreshReport,
        feedScopeRevision: Int
    ) {
        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: report.completedBackendPlatforms,
            customRefreshed: report.customCompletedCheck,
            completedDevicePlatforms: report.completedDevicePlatforms,
            activeTerms: db.terms.filter(\.is_active),
            customUrls: db.customUrls,
            subscribedPlatforms: db.subscribedPlatforms,
            feedScopeRevision: feedScopeRevision
        )
    }

    private func fallbackPlatformsNeedingDevice(selectedPlatform: String?) -> Set<String> {
        // Backend feed responses are stored rows, not proof that a fallback source
        // was freshly reachable from the backend, so subscribed fallback platforms
        // stay eligible for the device-side pass.
        BackgroundRefreshPolicy.fallbackPlatformsNeedingDevice(
            selectedPlatform: selectedPlatform,
            subscribedPlatforms: db.subscribedPlatforms
        )
    }
    
    private func refreshFeed(
        skipTermSync: Bool = false,
        feedScopeRevision: Int? = nil,
        selectedPlatformOverride: String? = nil
    ) async {
        guard !isRefreshing else { return }
        isRefreshing = true
        refreshErrorMessage = nil
        let refreshPlatform = selectedPlatformOverride ?? selectedPlatform
        defer {
            db.flushPendingFeedItemsSave()
            isRefreshing = false
            isScrapingFallback = false
        }

        var report = await quickRefresh(
            skipTermSync: skipTermSync,
            feedScopeRevision: feedScopeRevision,
            selectedPlatform: refreshPlatform
        )

        guard !Task.isCancelled else { return }
        let feedScopeRevision = report.feedScopeRevision ?? BackgroundRefreshPolicy.currentFeedScopeRevision
        let fallbackPlatformsNeedingDevice = fallbackPlatformsNeedingDevice(selectedPlatform: refreshPlatform)
        guard !report.backendReturnedItems || !fallbackPlatformsNeedingDevice.isEmpty else {
            recordCompletedRefreshCheckIfNeeded(report, feedScopeRevision: feedScopeRevision)
            await sendNoResultsDiagnosticIfNeeded(report)
            return
        }
        // Scrape device-side after the backend pass. The backend's Render datacenter IP is
        // blocked by Google News (503) and niconico (403), so many sources (niconico, 5ch,
        // smartnews, ameblo, aera, hochi, sponichi, livedoor, mantanweb, barks,
        // realsound, cinemacafe, mdpr, oricon, yahoo) only return data when fetched
        // from the device's own network.
        // mergeItems() dedupes against the backend results.
        //
        // Throttle it: each run fires ~15 Google News requests from the phone, so skip on
        // rapid successive refreshes (but always run on a cold/empty cache). Without this
        // the device risks the same 503 rate-limiting.
        let cacheIsEmpty = db.feedItems.isEmpty

        let elapsed = Self.lastDeviceScrapeAt.map { Date().timeIntervalSince($0) } ?? .greatestFiniteMagnitude
        guard BackgroundRefreshPolicy.shouldStartForegroundDeviceRefresh(
            cacheIsEmpty: cacheIsEmpty,
            elapsedSinceLastDeviceScrape: elapsed,
            throttle: Self.deviceScrapeThrottle,
            forceRefresh: refreshPlatform != nil && !fallbackPlatformsNeedingDevice.isEmpty
        ) else {
            report.record(strategy: "device_scrape", status: "throttled", detail: "\(Int(elapsed))s since last run")
            recordCompletedRefreshCheckIfNeeded(report, feedScopeRevision: feedScopeRevision)
            await sendNoResultsDiagnosticIfNeeded(report)
            return
        }
        isScrapingFallback = true
        report.append(await deepFallback(
            triggerBackendPoll: refreshPlatform == nil && !report.backendReturnedItems && !report.backendConnectivityFailed,
            fallbackPlatforms: fallbackPlatformsNeedingDevice
        ))
        guard !Task.isCancelled else { return }
        clearRefreshErrorIfLocalFeedIsUsable(report)
        recordCompletedRefreshCheckIfNeeded(report, feedScopeRevision: feedScopeRevision)
        await sendNoResultsDiagnosticIfNeeded(report)
    }

    // Device-side scrape throttle — shared across FeedView instances for the session.
    private static let lastDeviceScrapeAtKey = "feed.last_device_scrape_at"
    private static var lastDeviceScrapeAt: Date? {
        get {
            let timestamp = UserDefaults.standard.double(forKey: lastDeviceScrapeAtKey)
            return timestamp > 0 ? Date(timeIntervalSince1970: timestamp) : nil
        }
        set {
            if let newValue {
                UserDefaults.standard.set(newValue.timeIntervalSince1970, forKey: lastDeviceScrapeAtKey)
            } else {
                UserDefaults.standard.removeObject(forKey: lastDeviceScrapeAtKey)
            }
        }
    }
    private static let deviceScrapeThrottle: TimeInterval = 30 * 60

    // Syncs terms, fetches backend feed + per-platform items, and scrapes custom URLs.
    private func quickRefresh(
        skipTermSync: Bool = false,
        feedScopeRevision: Int? = nil,
        selectedPlatform: String? = nil
    ) async -> FeedRefreshReport {
        var report = FeedRefreshReport()

        // 0. Bidirectional term sync
        let currentFeedScopeRevision = BackgroundRefreshPolicy.currentFeedScopeRevision
        if skipTermSync,
           let feedScopeRevision,
           feedScopeRevision == currentFeedScopeRevision {
            report.feedScopeRevision = currentFeedScopeRevision
        } else {
            let pushedTerms = await NetworkManager.shared.syncWatchTermsToBackend(
                localTerms: db.terms,
                timeout: BackgroundRefreshPolicy.foregroundBackendTimeout
            )
            guard !Task.isCancelled else { return report }
            if pushedTerms {
                await NetworkManager.shared.syncTermsFromBackendWithStatus(
                    timeout: BackgroundRefreshPolicy.foregroundBackendTimeout
                )
            }
            guard !Task.isCancelled else { return report }
            report.feedScopeRevision = BackgroundRefreshPolicy.currentFeedScopeRevision
        }

        // 1. Determine fetch window.
        //    First load (empty cache): fetch 90 days of history.
        //    Subsequent refreshes: only items newer than the latest cached,
        //    so the backend never re-sends articles already stored.
        //    "All Time" filter bypasses the since-optimisation to show old items.
        let wantsFullHistory = daysFilter == 0
        let latestSince: String? = {
            guard !wantsFullHistory, !db.feedItems.isEmpty else { return nil }
            // Use fetched_at (reliable grab time) not published_at — items with a bad
            // published_at=now() would otherwise block older legitimate content.
            // Re-fetch a short overlap so device-clock skew or a local scrape cannot
            // advance the cursor past a just-created backend match.
            return BackgroundRefreshPolicy.incrementalSince(in: db.feedItems)
        }()
        let fetchDays = wantsFullHistory ? 0 : (db.feedItems.isEmpty ? 90 : 30)
        let backendTimeout = BackgroundRefreshPolicy.foregroundBackendTimeout
        var shouldSkipBackendRetries = false
        let focusedPlatform = selectedPlatform.map(Platform.normalize)

        // 2. Main backend feed fetch. If the incremental fetch is empty, retry with
        // broader windows before declaring "no results".
        if focusedPlatform == nil {
            do {
                let freshItems: [FeedItem]
                if let since = latestSince {
                    freshItems = try await NetworkManager.shared.fetchFeed(
                        limit: 200,
                        since: since,
                        timeout: backendTimeout
                    )
                    let added = db.mergeItems(newItems: freshItems)
                    report.record(
                        strategy: "backend_feed_since",
                        status: freshItems.isEmpty ? "empty" : "items",
                        itemCount: freshItems.count,
                        addedCount: added,
                        detail: since
                    )
                } else {
                    freshItems = try await NetworkManager.shared.fetchFeed(
                        limit: 120,
                        days: fetchDays,
                        timeout: backendTimeout
                    )
                    let added = db.mergeItems(newItems: freshItems)
                    report.record(
                        strategy: "backend_feed_days_\(fetchDays)",
                        status: freshItems.isEmpty ? "empty" : "items",
                        itemCount: freshItems.count,
                        addedCount: added
                    )
                }
            } catch {
                AppLogger.network.error("fetchFeed failed [\(Self.refreshErrorKind(error))]: \(error.localizedDescription)")
                refreshErrorMessage = Self.refreshErrorLabel(error)
                shouldSkipBackendRetries = BackgroundRefreshPolicy.shouldSkipBackendRetriesAfterFailure(error)
                report.backendConnectivityFailed = shouldSkipBackendRetries
                report.record(
                    strategy: latestSince == nil ? "backend_feed_days_\(fetchDays)" : "backend_feed_since",
                    status: "failed",
                    detail: "\(Self.refreshErrorKind(error)): \(error.localizedDescription)"
                )
            }
        }

        if focusedPlatform == nil, !report.backendReturnedItems, !shouldSkipBackendRetries {
            let retryDays = [fetchDays, 90, 180, 0].reduce(into: [Int]()) { values, days in
                if !values.contains(days) { values.append(days) }
            }
            for days in retryDays {
                guard !Task.isCancelled, !report.backendReturnedItems else { break }
                do {
                    let items = try await NetworkManager.shared.fetchFeed(
                        limit: 200,
                        days: days,
                        timeout: backendTimeout
                    )
                    let added = db.mergeItems(newItems: items)
                    report.record(
                        strategy: "backend_feed_retry_days_\(days)",
                        status: items.isEmpty ? "empty" : "items",
                        itemCount: items.count,
                        addedCount: added
                    )
                } catch {
                    report.record(
                        strategy: "backend_feed_retry_days_\(days)",
                        status: "failed",
                        detail: "\(Self.refreshErrorKind(error)): \(error.localizedDescription)"
                    )
                }
            }
        }
        guard !Task.isCancelled else { return report }

        // 3. Per-platform fetches in parallel
        let platformsToFetch: [String] = {
            guard !shouldSkipBackendRetries else { return [] }
            if let focusedPlatform {
                if Platform.shouldRunDeviceFallback(focusedPlatform) {
                    return []
                }
                return Platform.shouldFetchFromBackend(focusedPlatform) ? [focusedPlatform] : []
            }
            return Array(Set(db.subscribedPlatforms.filter {
                Platform.shouldFetchFromBackend($0)
            }))
        }()
        await withTaskGroup(of: FeedRefreshAttempt.self) { group in
            for platform in platformsToFetch {
                let platformSince = BackgroundRefreshPolicy.shouldUseDateWindowForPlatformRefresh(platform)
                    ? nil
                    : latestFetchedAt(for: platform)
                group.addTask {
                    await self.fetchBackendPlatform(
                        platform,
                        since: platformSince,
                        days: platformSince == nil ? fetchDays : 30,
                        timeout: backendTimeout
                    )
                }
            }
            var platformAttempts: [FeedRefreshAttempt] = []
            for await attempt in group {
                if Task.isCancelled {
                    group.cancelAll()
                    break
                }
                platformAttempts.append(attempt)
            }
            if !platformAttempts.isEmpty {
                let addedCounts = db.mergeItemsBatched(
                    newItemsBatches: platformAttempts.map(\.items)
                )
                for (index, attempt) in platformAttempts.enumerated() {
                    report.append(FeedRefreshAttempt(
                        strategy: attempt.strategy,
                        status: attempt.status,
                        itemCount: attempt.itemCount,
                        addedCount: addedCounts[index],
                        detail: attempt.detail,
                        completedDevicePlatforms: attempt.completedDevicePlatforms
                    ))
                }
            }
        }
        guard !Task.isCancelled else { return report }

        // 4. Custom URL cards
        let customItems = db.currentCustomFeedItems(await NetworkManager.shared.scrapeCustomUrls(db.customUrls))
        if !customItems.isEmpty {
            let added = db.mergeItems(newItems: customItems)
            report.record(strategy: "custom_urls", status: "items", itemCount: customItems.count, addedCount: added)
        } else if !db.customUrls.isEmpty {
            report.record(strategy: "custom_urls", status: "empty")
        }

        return report
    }

    // Runs local RSS/scraper fallbacks for sources blocked from the backend network.
    // If the backend returned nothing, also kick its scheduler for the next pull.
    private func deepFallback(triggerBackendPoll: Bool, fallbackPlatforms: Set<String>) async -> FeedRefreshReport {
        var report = FeedRefreshReport()
        let activeTerms = db.terms.filter { $0.is_active }
        let fallbackTerms = BackgroundRefreshPolicy.termsEligibleForDeviceFallback(
            activeTerms,
            subscribedPlatforms: db.subscribedPlatforms
        )
        if triggerBackendPoll {
            if activeTerms.isEmpty {
                report.record(
                    strategy: "backend_poll",
                    status: "skipped",
                    detail: "no active terms"
                )
            } else if BackgroundRefreshPolicy.shouldTriggerBackendPoll() {
                do {
                    try await NetworkManager.shared.triggerBackgroundPoll(
                        timeout: BackgroundRefreshPolicy.foregroundBackendTimeout
                    )
                    BackgroundRefreshPolicy.recordBackendPollTriggered()
                    report.record(strategy: "backend_poll", status: "ok")
                } catch {
                    report.record(
                        strategy: "backend_poll",
                        status: "failed",
                        detail: "\(Self.refreshErrorKind(error)): \(error.localizedDescription)"
                    )
                }
            } else {
                report.record(
                    strategy: "backend_poll",
                    status: "throttled",
                    detail: "last app-triggered poll is still fresh"
                )
            }
            do {
                let days = daysFilter == 0 ? 0 : 90
                let items = try await NetworkManager.shared.fetchFeed(
                    limit: 200,
                    days: days,
                    timeout: BackgroundRefreshPolicy.foregroundBackendTimeout
                )
                let added = db.mergeItems(newItems: items)
                report.record(
                    strategy: "backend_feed_after_poll",
                    status: items.isEmpty ? "empty" : "items",
                    itemCount: items.count,
                    addedCount: added,
                    detail: "days=\(days)"
                )
            } catch {
                report.record(
                    strategy: "backend_feed_after_poll",
                    status: "failed",
                    detail: "\(Self.refreshErrorKind(error)): \(error.localizedDescription)"
                )
            }
        }
        guard !fallbackTerms.isEmpty else {
            report.record(strategy: "device_local_scrapers", status: "skipped", detail: "no eligible active terms")
            return report
        }
        let platformsToScrape = fallbackPlatforms
        guard !platformsToScrape.isEmpty else {
            report.record(strategy: "device_local_scrapers", status: "skipped", detail: "no subscribed fallback platforms")
            return report
        }
        await withTaskGroup(
            of: (termKeyword: String, searchTerm: String, result: LocalFallbackScrapeResult, expectedPlatforms: Set<String>).self
        ) { group in
            for term in fallbackTerms {
                let platformsForTerm = platformsToScrape.intersection(
                    BackgroundRefreshPolicy.deviceFallbackPlatforms(
                        for: term,
                        subscribedPlatforms: db.subscribedPlatforms
                    )
                )
                guard !platformsForTerm.isEmpty else { continue }
                let searchTerms = [term.keyword] + term.aliases
                for searchTerm in searchTerms {
                    group.addTask {
                        let scrapeResult = await NetworkManager.shared.scrapeLocalFallbacksWithCompletion(
                            keyword: searchTerm,
                            tagKeyword: term.keyword,
                            platformIds: platformsForTerm
                        )
                        return (term.keyword, searchTerm, scrapeResult, platformsForTerm)
                    }
                }
            }
            var scrapeResults = [(result: LocalFallbackScrapeResult, expectedPlatforms: Set<String>)]()
            var scrapeAttempts = [[FeedItem]]()
            for await scrape in group {
                if Task.isCancelled {
                    group.cancelAll()
                    break
                }
                guard let currentTerm = db.term(matchingKeyword: scrape.termKeyword) else { continue }
                let currentFallbackPlatforms = BackgroundRefreshPolicy.deviceFallbackPlatforms(
                    for: currentTerm,
                    subscribedPlatforms: db.subscribedPlatforms
                )
                guard !currentFallbackPlatforms.isEmpty,
                      scrape.searchTerm == currentTerm.keyword ||
                        currentTerm.aliases.contains(scrape.searchTerm) else {
                    continue
                }
                let mergeableItems = scrape.result.items.filter { item in
                    item.watch_term_keyword == currentTerm.keyword &&
                        currentFallbackPlatforms.contains(Platform.normalize(item.platform))
                }
                let expectedPlatforms = scrape.expectedPlatforms.intersection(currentFallbackPlatforms)
                scrapeResults.append((scrape.result, expectedPlatforms))
                scrapeAttempts.append(mergeableItems)
            }
            if !scrapeAttempts.isEmpty {
                let addedCounts = db.mergeItemsBatched(
                    newItemsBatches: scrapeAttempts
                )
                for (index, mergeableItems) in scrapeAttempts.enumerated() {
                    let added = addedCounts[index]
                    report.record(
                        strategy: "device_local_scrapers",
                        status: mergeableItems.isEmpty ? "empty" : "items",
                        itemCount: mergeableItems.count,
                        addedCount: added
                    )
                }
            }
            let completedPlatforms = BackgroundRefreshPolicy.completedDeviceFallbackPlatformsForEligibleSearches(
                scrapeResults
            )
            report.markCompletedDevicePlatforms(completedPlatforms)
            // Diff against platforms actually expected by this round's searches, not the
            // user's full subscribed set — a platform can be subscribed but excluded from
            // every active term this round (e.g. a media-only term), in which case it was
            // never attempted and shouldn't be reported as having failed to complete.
            let expectedPlatformsThisRound = scrapeResults.reduce(into: Set<String>()) { result, scrapeResult in
                result.formUnion(scrapeResult.expectedPlatforms.map { Platform.normalize($0) })
            }
            let missingPlatforms = expectedPlatformsThisRound.subtracting(completedPlatforms)
            if !missingPlatforms.isEmpty {
                // A platform can silently drop out of a request (network error, timeout,
                // bad response — see NetworkManager+Scraper.swift's httpGET logging) without
                // any single strategy attempt above reporting "failed", since a scrape that
                // never completes just contributes nothing to scrapeResults. Surface that gap
                // explicitly so it isn't indistinguishable from "this platform had no news".
                report.record(
                    strategy: "device_local_scrapers",
                    status: "failed",
                    detail: "did not complete: \(missingPlatforms.sorted().joined(separator: ","))"
                )
            }
        }
        if !report.attempts.contains(where: { $0.strategy == "device_local_scrapers" }) {
            report.record(strategy: "device_local_scrapers", status: "empty")
        }
        if !Task.isCancelled {
            Self.lastDeviceScrapeAt = Date()
        }
        return report
    }

    private func fetchBackendPlatform(
        _ platformId: String,
        since: String? = nil,
        days: Int = 30,
        timeout: TimeInterval = 30
    ) async -> FeedRefreshAttempt {
        do {
            let items: [FeedItem]
            if let since {
                items = try await NetworkManager.shared.fetchFeed(
                    platform: platformId,
                    limit: 60,
                    since: since,
                    timeout: timeout
                )
            } else {
                items = try await NetworkManager.shared.fetchFeed(
                    platform: platformId,
                    limit: 60,
                    days: days,
                    timeout: timeout
                )
            }
            return FeedRefreshAttempt(
                strategy: "backend_platform_\(platformId)",
                status: items.isEmpty ? "empty" : "items",
                itemCount: items.count,
                addedCount: 0,
                detail: since ?? "days=\(days)",
                items: items
            )
        } catch {
            AppLogger.network.warning("fetchBackendPlatform(\(platformId)) failed [\(Self.refreshErrorKind(error))]: \(error.localizedDescription)")
            return FeedRefreshAttempt(
                strategy: "backend_platform_\(platformId)",
                status: "failed",
                itemCount: 0,
                addedCount: 0,
                detail: "\(Self.refreshErrorKind(error)): \(error.localizedDescription)"
            )
        }
    }

    private func sendNoResultsDiagnosticIfNeeded(_ report: FeedRefreshReport) async {
        guard !report.backendConnectivityFailed else { return }
        let activeTermsCount = db.terms.filter { $0.is_active }.count
        guard activeTermsCount > 0 else { return }

        let feedIsEmpty = db.feedItems.isEmpty && report.addedCount == 0
        let devicePlatformsIncomplete = report.attempts.contains {
            $0.strategy == "device_local_scrapers" && $0.status == "failed"
        }
        guard feedIsEmpty || devicePlatformsIncomplete else { return }

        // The empty-feed case is rare and always worth reporting. A partial device-fallback
        // failure alongside an otherwise-populated feed is comparatively common (a single
        // slow/blocked site among ~25) and would spam a diagnostic on every such refresh
        // without a throttle.
        if !feedIsEmpty {
            let now = Date()
            if let last = Self.lastDeviceFallbackFailureDiagnosticAt,
               now.timeIntervalSince(last) < Self.deviceFallbackFailureDiagnosticThrottle {
                return
            }
            Self.lastDeviceFallbackFailureDiagnosticAt = now
        }

        let info = Bundle.main.infoDictionary
        let diagnostic = ClientDiagnosticReport(
            reason: feedIsEmpty ? "feed_refresh_no_results_after_fallbacks" : "device_fallback_partial_failure",
            environment: NetworkManager.shared.environmentName,
            api_base: NetworkManager.shared.apiBase,
            app_version: info?["CFBundleShortVersionString"] as? String,
            build: info?["CFBundleVersion"] as? String,
            active_terms_count: activeTermsCount,
            subscribed_platforms: db.subscribedPlatforms,
            cached_feed_count: db.feedItems.count,
            events: report.attempts.map(\.diagnosticEvent)
        )
        AppLogger.network.error("\(feedIsEmpty ? "No feed results" : "Device fallback incomplete") after \(report.attempts.count) strategies; sending diagnostic")
        await NetworkManager.shared.sendClientDiagnostic(diagnostic)
    }

    // Throttles the partial-device-fallback-failure diagnostic — shared across FeedView
    // instances for the session, same pattern as lastDeviceScrapeAt below.
    private static let lastDeviceFallbackFailureDiagnosticAtKey = "feed.last_device_fallback_failure_diagnostic_at"
    private static var lastDeviceFallbackFailureDiagnosticAt: Date? {
        get {
            let timestamp = UserDefaults.standard.double(forKey: lastDeviceFallbackFailureDiagnosticAtKey)
            return timestamp > 0 ? Date(timeIntervalSince1970: timestamp) : nil
        }
        set {
            if let newValue {
                UserDefaults.standard.set(newValue.timeIntervalSince1970, forKey: lastDeviceFallbackFailureDiagnosticAtKey)
            } else {
                UserDefaults.standard.removeObject(forKey: lastDeviceFallbackFailureDiagnosticAtKey)
            }
        }
    }
    private static let deviceFallbackFailureDiagnosticThrottle: TimeInterval = 60 * 60

    private func clearRefreshErrorIfLocalFeedIsUsable(_ report: FeedRefreshReport) {
        guard refreshErrorMessage != nil else { return }
        if report.addedCount > 0 || !cachedFilteredItems.isEmpty || !db.feedItems.isEmpty {
            refreshErrorMessage = nil
        }
    }

    // Returns a short machine-readable category string for log triage.
    private static func refreshErrorKind(_ error: Error) -> String {
        if error is DecodingError { return "decode" }
        if case APIClientError.httpStatus(let status, _) = error { return "http_\(status)" }
        guard let e = error as? URLError else { return "unknown" }
        switch e.code {
        case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed: return "offline"
        case .timedOut: return "timeout"
        case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed: return "unreachable"
        case .badServerResponse: return "server_error"
        default: return "url_\(e.code.rawValue)"
        }
    }

    // Returns an i18n key for the error banner (resolved via i18n.t() at display time).
    private static func refreshErrorLabel(_ error: Error) -> String {
        if error is DecodingError { return "errorDecode" }
        if case APIClientError.httpStatus(_, _) = error { return "errorUnavailable" }
        guard let e = error as? URLError else { return "errorRefreshFailed" }
        switch e.code {
        case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed:
            return "errorNoInternet"
        case .timedOut:
            return "errorTimeout"
        case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
            return "errorUnreachable"
        default:
            return "errorUnavailable"
        }
    }

    private func hasItems(for platformId: String) -> Bool {
        db.feedItems.contains { matchesPlatform($0, platformId: platformId) }
    }

    private func latestFetchedAt(for platformId: String) -> String? {
        BackgroundRefreshPolicy.incrementalSince(
            in: db.feedItems,
            platformId: platformId
        )
    }

    private func matchesPlatform(_ item: FeedItem, platformId: String) -> Bool {
        Self.matchesPlatform(item, platformId: platformId)
    }

    private static func matchesPlatform(_ item: FeedItem, platformId: String) -> Bool {
        Platform.normalize(item.platform) == platformId
    }

    @ViewBuilder
    private func saveToggleButton(for item: FeedItem) -> some View {
        Button {
            _ = db.toggleSaved(item: item)
        } label: {
            Label(savedItemIds.contains(item.id) ? i18n.t("unsave") : i18n.t("save"),
                  systemImage: savedItemIds.contains(item.id) ? "bookmark.slash" : "bookmark")
        }
        .tint(theme.colors.primary)
    }

    @ViewBuilder
    private func hidePostButton(for item: FeedItem) -> some View {
        Button(role: .destructive) {
            pendingHiddenFeedItem = item
        } label: {
            Label(i18n.t("hidePost"), systemImage: "eye.slash")
        }
        .tint(.red)
    }

    private func deleteFeedItem(_ item: FeedItem, clearSelection: Bool) {
        db.deleteFeedItem(id: item.id, watchTermKeyword: item.watch_term_keyword)
        if clearSelection, selectedItem?.id == item.id { selectedItem = nil }
        guard let watchTermID = backendWatchTermID(for: item) else { return }
        Task {
            do {
                try await NetworkManager.shared.muteFeedItem(
                    sourceItemID: item.id,
                    watchTermID: watchTermID
                )
            } catch {
                AppLogger.network.error("muteFeedItem(\(item.id), term=\(watchTermID)) failed: \(error.localizedDescription)")
            }
        }
    }

    private func backendWatchTermID(for item: FeedItem) -> Int? {
        if let watchTermID = item.watch_term_id {
            return watchTermID
        }
        guard let term = db.term(matchingKeyword: item.watch_term_keyword) else {
            return nil
        }
        return Int(term.id)
    }

    private func confirmHidePost() {
        guard let item = pendingHiddenFeedItem else { return }
        pendingHiddenFeedItem = nil
        deleteFeedItem(item, clearSelection: true)
    }
}

// MARK: - Subviews

struct PillView: View {
    let text: String
    var bgColor: Color? = nil
    var fgColor: Color? = nil
    var theme: ThemeManager? = nil
    @StateObject private var appearance = AppearanceManager.shared
    
    var body: some View {
        Text(text)
            .font(appearance.font(size: 11))
            .fontWeight(.medium)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(bgColor ?? theme?.colors.divider ?? Color.gray.opacity(0.2))
            .foregroundColor(fgColor ?? theme?.colors.textSub ?? Color.primary)
            .clipShape(Capsule())
    }
}

struct FeedCard: View {
    let item: FeedItem
    let isSaved: Bool
    let theme: ThemeManager
    @StateObject private var appearance = AppearanceManager.shared
    
    var body: some View {
        let meta = theme.metadata(for: item.platform)
        let badgeBg = theme.style == .standard ? theme.standardBadgeBg : meta.bg
        let badgeFg = theme.style == .standard ? theme.standardBadgeFg : meta.fg
        let titleColor = theme.style == .standard ? theme.colors.text : meta.fg
        
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center) {
                // Platform tag badge
                HStack(spacing: 3) {
                    Text(meta.icon)
                        .font(appearance.font(size: 12))
                    Text(meta.name)
                        .font(appearance.font(size: 11))
                        .fontWeight(.bold)
                }
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .background(badgeBg)
                .foregroundColor(badgeFg)
                .cornerRadius(6)
                
                if !item.watch_term_keyword.isEmpty {
                    Text(item.watch_term_keyword)
                        .font(appearance.font(size: 11))
                        .fontWeight(.semibold)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 3)
                        .background(theme.colors.divider)
                        .foregroundColor(theme.colors.textSub)
                        .cornerRadius(6)
                }
                
                Spacer()
                
                if isSaved {
                    Image(systemName: "bookmark.fill")
                        .foregroundColor(theme.colors.primary)
                        .font(.caption)
                }
                
                Text(relativeTime(from: item.published_at))
                    .font(appearance.font(size: 11))
                    .foregroundColor(theme.colors.textMuted)
            }
            
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    if let title = cleanDisplayText(item.title) {
                        Text(title)
                            .font(appearance.font(size: 15))
                            .fontWeight(.bold)
                            .foregroundColor(titleColor)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                    }
                    
                    if let author = cleanDisplayText(item.author) {
                        Text(author)
                            .font(appearance.font(size: 12))
                            .fontWeight(.medium)
                            .foregroundColor(theme.colors.textMuted)
                            .lineLimit(1)
                    }
                    
                    if let content = cleanDisplayText(item.content_text) {
                        Text(content)
                            .font(appearance.font(size: 12))
                            .foregroundColor(theme.colors.textSub)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                            .padding(.top, 2)
                    }
                }
                
                Spacer()
                
                // Optional Thumbnail URL
                if let thumb = item.thumbnail_url, let url = URL(string: thumb) {
                    FeedThumbnailView(url: url)
                }
            }
        }
        .padding(12)
        .background(theme.colors.card)
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(theme.mode == .dark ? 0.2 : 0.04), radius: 5, x: 0, y: 2)
        .accessibilityIdentifier("feed.card.\(item.id)")
    }
    
    private func relativeTime(from isoDate: String) -> String {
        guard let date = parseISO8601Date(isoDate) else { return "" }
        return relativeTimeString(from: date)
    }
}

// MARK: - Sheet Components

struct FilterPanel: View {
    @Binding var selectedKeyword: String?
    @Binding var mediaFilter: MediaFilter
    @Binding var daysFilter: Int
    let theme: ThemeManager
    let i18n: I18nManager
    let timeRanges: [(label: String, days: Int)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text(i18n.t("filter"))
                    .font(.headline)
                    .foregroundColor(theme.colors.text)
                    .padding(.top, 8)

                // All / Media Only
                VStack(alignment: .leading, spacing: 8) {
                    Text(i18n.t("allInfo") + " / " + i18n.t("mediaOnly"))
                        .font(.caption)
                        .fontWeight(.bold)
                        .foregroundColor(theme.colors.textMuted)
                        .textCase(.uppercase)

                    HStack(spacing: 8) {
                        FilterButton(
                            text: "📄 " + i18n.t("allInfo"),
                            isSelected: mediaFilter == .all,
                            theme: theme,
                            accessibilityId: "filter.allInfoButton"
                        ) {
                            mediaFilter = .all
                        }
                        FilterButton(
                            text: "📹 " + i18n.t("mediaOnly"),
                            isSelected: mediaFilter == .mediaOnly,
                            theme: theme,
                            accessibilityId: "filter.mediaOnlyButton"
                        ) {
                            mediaFilter = .mediaOnly
                        }
                    }
                }

                // Period
                VStack(alignment: .leading, spacing: 8) {
                    Text(i18n.t("period"))
                        .font(.caption)
                        .fontWeight(.bold)
                        .foregroundColor(theme.colors.textMuted)
                        .textCase(.uppercase)
                    
                    FlowLayout(spacing: 6) {
                        ForEach(timeRanges, id: \.days) { range in
                            FilterButton(text: i18n.t(range.label), isSelected: daysFilter == range.days, theme: theme) {
                                daysFilter = range.days
                            }
                        }
                    }
                }
                
                // Keywords (Watch terms)
                VStack(alignment: .leading, spacing: 8) {
                    Text(i18n.t("keyword"))
                        .font(.caption)
                        .fontWeight(.bold)
                        .foregroundColor(theme.colors.textMuted)
                        .textCase(.uppercase)
                    
                    FlowLayout(spacing: 6) {
                        FilterButton(text: i18n.t("all"), isSelected: selectedKeyword == nil, theme: theme) {
                            selectedKeyword = nil
                        }
                        ForEach(LocalDB.shared.terms) { term in
                            FilterButton(text: term.keyword, isSelected: selectedKeyword == term.keyword, theme: theme) {
                                selectedKeyword = term.keyword
                            }
                        }
                    }
                }
            }
            .padding(18)
        }
        .accessibilityIdentifier("filter.sheet")
        .background(theme.colors.bg)
    }
}

struct FilterButton: View {
    let text: String
    let isSelected: Bool
    let theme: ThemeManager
    var accessibilityId: String? = nil
    let action: () -> Void
    
    var body: some View {
        Button(action: action) {
            Text(text)
                .font(.system(size: 12, weight: .medium))
                .padding(.horizontal, 12)
                .padding(.vertical, 7)
                .background(isSelected ? theme.colors.primary : theme.colors.divider)
                .foregroundColor(isSelected ? .white : theme.colors.textSub)
                .cornerRadius(999)
        }
        .accessibilityIdentifier(accessibilityId ?? "filter.option.\(text)")
    }
}

// MARK: - FlowLayout helper for Wrapping Chips
struct FlowLayout: Layout {
    var spacing: CGFloat
    
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let sizes = subviews.map { $0.sizeThatFits(.unspecified) }
        var width: CGFloat = 0
        var height: CGFloat = 0
        var currentX: CGFloat = 0
        var currentY: CGFloat = 0
        var maxRowHeight: CGFloat = 0
        
        let maxW = proposal.width ?? 300
        
        for size in sizes {
            if currentX + size.width > maxW {
                currentX = 0
                currentY += maxRowHeight + spacing
                maxRowHeight = 0
            }
            currentX += size.width + spacing
            width = max(width, currentX)
            maxRowHeight = max(maxRowHeight, size.height)
            height = max(height, currentY + size.height)
        }
        return CGSize(width: width, height: height)
    }
    
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var currentX: CGFloat = bounds.minX
        var currentY: CGFloat = bounds.minY
        var maxRowHeight: CGFloat = 0
        
        let maxW = bounds.width
        
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if currentX + size.width > bounds.minX + maxW {
                currentX = bounds.minX
                currentY += maxRowHeight + spacing
                maxRowHeight = 0
            }
            subview.place(at: CGPoint(x: currentX, y: currentY), proposal: .unspecified)
            currentX += size.width + spacing
            maxRowHeight = max(maxRowHeight, size.height)
        }
    }
}

struct AddUrlSheet: View {
    @Binding var customUrlString: String
    @Binding var customUrlTitle: String
    let theme: ThemeManager
    let i18n: I18nManager
    let onSave: () -> Void
    
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(i18n.t("addCustomFeed"))
                .font(.headline)
                .foregroundColor(theme.colors.text)
                .padding(.top, 10)

            TextField(i18n.t("feedTitlePlaceholder"), text: $customUrlTitle)
                .padding()
                .background(theme.colors.card)
                .cornerRadius(8)
                .foregroundColor(theme.colors.text)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(theme.colors.border, lineWidth: 1))
                .accessibilityIdentifier("customUrl.titleField")
            
            TextField(i18n.t("urlPlaceholder"), text: $customUrlString)
                .padding()
                .background(theme.colors.card)
                .cornerRadius(8)
                .foregroundColor(theme.colors.text)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(theme.colors.border, lineWidth: 1))
                .keyboardType(.URL)
                .autocapitalization(.none)
                .accessibilityIdentifier("customUrl.urlField")
            
            Button(action: onSave) {
                Text(i18n.t("save"))
                    .bold()
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(theme.colors.primary)
                    .cornerRadius(10)
            }
            .accessibilityIdentifier("customUrl.saveButton")
            .disabled(customUrlString.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .opacity(customUrlString.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? 0.5 : 1.0)
            
            Spacer()
        }
        .padding(18)
        .background(theme.colors.bg)
    }
}

struct ReorderSourcesSheet: View {
    @StateObject private var db = LocalDB.shared
    let theme: ThemeManager
    let i18n: I18nManager
    
    @Environment(\.dismiss) private var dismiss
    @State private var platforms: [String] = []
    
    var body: some View {
        NavigationStack {
            VStack {
                List {
                    ForEach(platforms, id: \.self) { pId in
                        let meta = theme.metadata(for: pId)
                        HStack(spacing: 8) {
                            Text(meta.icon)
                                .font(.system(size: 16))
                            Text(meta.name)
                                .font(.subheadline)
                                .foregroundColor(theme.colors.text)
                            Spacer()
                            Image(systemName: "line.3.horizontal")
                                .font(.subheadline)
                                .foregroundColor(theme.colors.textMuted)
                        }
                        .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
                    }
                    .onMove { indices, newOffset in
                        platforms.move(fromOffsets: indices, toOffset: newOffset)
                    }
                }
                .listStyle(.plain)
                .environment(\.defaultMinListRowHeight, 36)
                
                Button(action: {
                    db.setSourcesOrder(order: platforms)
                    dismiss()
                }) {
                    Text(i18n.t("save"))
                        .bold()
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(theme.colors.primary)
                        .cornerRadius(10)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
            }
            .navigationTitle(i18n.t("reorderSources"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button(i18n.t("cancel")) { dismiss() }
                }
            }
            .onAppear {
                platforms = db.subscribedPlatforms
                if let order = db.sourcesOrder {
                    let orderSet = Set(order)
                    let ordered = order.filter { platforms.contains($0) }
                    let unordered = platforms.filter { !orderSet.contains($0) }
                    platforms = ordered + unordered
                }
            }
            .background(theme.colors.bg)
        }
    }
}

// Faint feed wallpaper. Loads the image off the main thread (file:// or remote) and
// caches it in state so the feed body never reads disk during a render pass.
private struct WallpaperBackgroundView: View {
    let urlString: String
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .opacity(0.20)
                    .ignoresSafeArea()
            }
        }
        .task(id: urlString) { await load() }
    }

    private func load() async {
        guard let url = URL(string: urlString) else { image = nil; return }
        if url.isFileURL {
            let path = url.path
            image = await Task.detached(priority: .utility) { UIImage(contentsOfFile: path) }.value
        } else if let (data, _) = try? await URLSession.shared.data(from: url) {
            image = UIImage(data: data)
        } else {
            image = nil
        }
    }
}
