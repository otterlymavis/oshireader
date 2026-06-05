import SwiftUI

struct FeedView: View {
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    
    @State private var selectedKeyword: String? = nil
    @State private var selectedPlatform: String? = nil
    @State private var mediaFilter: String = "all" // "all" | "media_only"
    @State private var daysFilter: Int = 30
    
    @State private var isRefreshing = false
    @State private var hasLoadedOnce = false
    @State private var displayedCount: Int = 20
    @State private var showFilterSheet = false
    @State private var showAddUrlSheet = false
    @State private var showReorderSheet = false
    
    @State private var customUrlString = ""
    @State private var customUrlTitle = ""
    
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selectedItem: FeedItem? = nil
    
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

    // Full filtered list (all matching items)
    var filteredItems: [FeedItem] {
        var result = db.queryFeed(keyword: selectedKeyword, days: daysFilter)
        if let platform = selectedPlatform {
            result = result.filter { matchesPlatform($0, platformId: platform) }
        }
        if mediaFilter == "media_only" {
            let mediaPlatforms: Set<String> = ["youtube", "niconico", "tver"]
            result = result.filter { $0.media_type == "video" || mediaPlatforms.contains($0.platform) }
        }
        return result
    }

    // Page-limited slice shown in the list
    var visibleItems: [FeedItem] {
        Array(filteredItems.prefix(displayedCount))
    }

    private var canLoadMore: Bool {
        displayedCount < min(filteredItems.count, 100)
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
            
            // Custom Wallpaper (from localDB)
            if let wallpaperUrl = db.wallpaper, let url = URL(string: wallpaperUrl) {
                AsyncImage(url: url) { image in
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .opacity(0.12)
                        .ignoresSafeArea()
                } placeholder: {
                    EmptyView()
                }
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
                        if mediaFilter == "media_only" {
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
                                Button(action: { selectedPlatform = nil }) {
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
                                        if !isSelected && !hasItems(for: platformId) {
                                            Task {
                                                await fetchBackendPlatform(platformId)
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
                
                // Main Feed List
                if isRefreshing && filteredItems.isEmpty {
                    Spacer()
                    ProgressView()
                        .tint(theme.colors.primary)
                    Spacer()
                } else if filteredItems.isEmpty {
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
                    List {
                        ForEach(visibleItems) { item in
                            if horizontalSizeClass == .regular {
                                Button(action: { selectedItem = item }) {
                                    FeedCard(item: item, isSaved: savedItemIds.contains(item.id), theme: theme)
                                        .overlay(
                                            RoundedRectangle(cornerRadius: 12)
                                                .stroke(theme.colors.primary, lineWidth: selectedItem?.id == item.id ? 2 : 0)
                                        )
                                }
                                .buttonStyle(PlainButtonStyle())
                                .listRowInsets(EdgeInsets(top: 4, leading: 14, bottom: 4, trailing: 14))
                                .listRowBackground(Color.clear)
                                .listRowSeparator(.hidden)
                                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                    Button(role: .destructive) {
                                        db.deleteFeedItem(id: item.id, watchTermKeyword: item.watch_term_keyword)
                                        if selectedItem?.id == item.id { selectedItem = nil }
                                    } label: {
                                        Label(i18n.t("delete"), systemImage: "trash")
                                    }
                                }
                                .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                    Button {
                                        _ = db.toggleSaved(item: item)
                                    } label: {
                                        Label(savedItemIds.contains(item.id) ? "Unsave" : "Save",
                                              systemImage: savedItemIds.contains(item.id) ? "bookmark.slash" : "bookmark")
                                    }
                                    .tint(theme.colors.primary)
                                }
                            } else {
                                NavigationLink(destination: ReaderView(feedItem: item)) {
                                    FeedCard(item: item, isSaved: savedItemIds.contains(item.id), theme: theme)
                                }
                                .buttonStyle(PlainButtonStyle())
                                .listRowInsets(EdgeInsets(top: 4, leading: 14, bottom: 4, trailing: 14))
                                .listRowBackground(Color.clear)
                                .listRowSeparator(.hidden)
                                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                    Button(role: .destructive) {
                                        db.deleteFeedItem(id: item.id, watchTermKeyword: item.watch_term_keyword)
                                    } label: {
                                        Label(i18n.t("delete"), systemImage: "trash")
                                    }
                                }
                                .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                    Button {
                                        _ = db.toggleSaved(item: item)
                                    } label: {
                                        Label(savedItemIds.contains(item.id) ? "Unsave" : "Save",
                                              systemImage: savedItemIds.contains(item.id) ? "bookmark.slash" : "bookmark")
                                    }
                                    .tint(theme.colors.primary)
                                }
                            }
                        }

                        if canLoadMore {
                            Button {
                                displayedCount = min(displayedCount + 20, 100)
                            } label: {
                                HStack {
                                    Spacer()
                                    Text("Load more (\(min(filteredItems.count, 100) - displayedCount) remaining)")
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
                    Button(action: {
                        Task {
                            await refreshFeed()
                        }
                    }) {
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
                    if !customItems.isEmpty {
                        _ = await db.mergeItems(newItems: customItems)
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
        .accessibilityIdentifier("feed.screen")
        .onChange(of: selectedKeyword) { _ in displayedCount = 20 }
        .onChange(of: selectedPlatform) { _ in displayedCount = 20 }
        .onChange(of: daysFilter) { newDays in
            displayedCount = 20
            if newDays == 0 {
                Task { await refreshFeed() }
            }
        }
        .onChange(of: mediaFilter) { _ in displayedCount = 20 }
        .onAppear {
            guard !hasLoadedOnce else { return }
            hasLoadedOnce = true
            Task {
                // Push local terms to backend (handles post-DB-reset state)
                await NetworkManager.shared.syncWatchTermsToBackend(localTerms: db.terms)
                // Pull backend terms that aren't local yet (fresh install / multi-device)
                let pulledNew = await NetworkManager.shared.syncTermsFromBackend()
                // Refresh feed when: no cached items and we have terms (either pre-existing
                // or just pulled from the backend)
                if db.feedItems.isEmpty, (!db.terms.isEmpty || pulledNew) {
                    await refreshFeed()
                }
            }
        }
    }
    
    private var filterCount: Int {
        var count = 0
        if selectedKeyword != nil { count += 1 }
        if selectedPlatform != nil { count += 1 }
        if mediaFilter == "media_only" { count += 1 }
        return count
    }
    
    private func refreshFeed() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        // 0. Bidirectional term sync
        await NetworkManager.shared.syncWatchTermsToBackend(localTerms: db.terms)
        await NetworkManager.shared.syncTermsFromBackend()

        // 1. Determine what to fetch.
        //    First load (empty cache): fetch 90 days of history.
        //    Subsequent refreshes: ask only for items newer than the latest we have,
        //    so the backend never re-sends articles we already cached.
        // When user has "All Time" selected, bypass the since-optimisation and
        // fetch the full history so they actually see old items.
        let wantsFullHistory = daysFilter == 0
        let latestSince: String? = {
            guard !wantsFullHistory else { return nil }
            guard !db.feedItems.isEmpty else { return nil }
            // Use fetched_at (reliable grab time) not published_at — bad-date items
            // with published_at=now() would otherwise block older legit content.
            guard let maxDate = db.feedItems.compactMap({ parseISO8601Date($0.fetched_at) }).max() else { return nil }
            let fmt = ISO8601DateFormatter()
            fmt.formatOptions = [.withInternetDateTime]
            return fmt.string(from: maxDate)
        }()
        let fetchDays = wantsFullHistory ? 0 : (db.feedItems.isEmpty ? 90 : 30)

        let freshItems: [FeedItem]
        if let since = latestSince {
            freshItems = (try? await NetworkManager.shared.fetchFeed(limit: 200, since: since)) ?? []
        } else {
            freshItems = (try? await NetworkManager.shared.fetchFeed(limit: 120, days: fetchDays)) ?? []
        }
        if !freshItems.isEmpty {
            _ = await db.mergeItems(newItems: freshItems)
        }

        // 2. Fetch each subscribed platform in parallel (also uses since when available)
        let platformsToFetch = Array(Set(db.subscribedPlatforms.filter { $0 != "custom" }))
        await withTaskGroup(of: Void.self) { group in
            for platform in platformsToFetch {
                group.addTask { await self.fetchBackendPlatform(platform, since: latestSince) }
            }
        }

        // 3. Local fallback scrapers — only when backend returned nothing (offline/spin-down)
        if freshItems.isEmpty {
            // Kick the backend scheduler so fresh data is ready on the next pull.
            Task { try? await NetworkManager.shared.triggerPoll() }
            let activeTerms = db.terms.filter { $0.is_active }
            await withTaskGroup(of: [FeedItem].self) { group in
                for term in activeTerms {
                    let searchTerms = [term.keyword] + term.aliases
                    for searchTerm in searchTerms {
                        group.addTask {
                            await NetworkManager.shared.scrapeLocalFallbacks(keyword: searchTerm, tagKeyword: term.keyword)
                        }
                    }
                }
                for await items in group where !items.isEmpty {
                    _ = await db.mergeItems(newItems: items)
                }
            }
        }

        // 4. Refresh custom URL cards
        let customItems = await NetworkManager.shared.scrapeCustomUrls(db.customUrls)
        if !customItems.isEmpty {
            _ = await db.mergeItems(newItems: customItems)
        }

        isRefreshing = false
    }

    private func fetchBackendPlatform(_ platformId: String, since: String? = nil) async {
        let backendPlatforms = backendPlatformKeys(for: platformId)
        for backendPlatform in backendPlatforms {
            if let items = try? await NetworkManager.shared.fetchFeed(platform: backendPlatform, limit: 60, since: since),
               !items.isEmpty {
                _ = await db.mergeItems(newItems: items)
            }
        }
    }

    private func backendPlatformKeys(for platformId: String) -> [String] {
        return [platformId]
    }

    private func hasItems(for platformId: String) -> Bool {
        db.feedItems.contains { matchesPlatform($0, platformId: platformId) }
    }

    private func matchesPlatform(_ item: FeedItem, platformId: String) -> Bool {
        if platformId == "mdpr" {
            return item.platform == "mdpr" || item.platform == "news:mdpr"
        }
        if platformId == "yahoonews" {
            return item.platform == "yahoonews" || item.platform == "news:yahoo_ent"
        }
        if platformId == "news" {
            return item.platform == "news" || item.platform.hasPrefix("news:")
        }
        return item.platform == platformId
    }
}

// MARK: - Subviews

struct PillView: View {
    let text: String
    var bgColor: Color? = nil
    var fgColor: Color? = nil
    var theme: ThemeManager? = nil
    
    var body: some View {
        Text(text)
            .font(.caption2)
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
                        .font(.caption)
                    Text(meta.name)
                        .font(.system(size: 11, weight: .bold))
                }
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .background(badgeBg)
                .foregroundColor(badgeFg)
                .cornerRadius(6)
                
                if !item.watch_term_keyword.isEmpty {
                    Text(item.watch_term_keyword)
                        .font(.system(size: 11, weight: .semibold))
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
                    .font(.caption2)
                    .foregroundColor(theme.colors.textMuted)
            }
            
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    if let title = cleanDisplayText(item.title) {
                        Text(title)
                            .font(.subheadline)
                            .fontWeight(.bold)
                            .foregroundColor(titleColor)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                    }
                    
                    if let author = cleanDisplayText(item.author) {
                        Text(author)
                            .font(.caption)
                            .fontWeight(.medium)
                            .foregroundColor(theme.colors.textMuted)
                            .lineLimit(1)
                    }
                    
                    if let content = cleanDisplayText(item.content_text) {
                        Text(content)
                            .font(.caption)
                            .foregroundColor(theme.colors.textSub)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                            .padding(.top, 2)
                    }
                }
                
                Spacer()
                
                // Optional Thumbnail URL
                if let thumb = item.thumbnail_url, let url = URL(string: thumb) {
                    AsyncImage(url: url) { phase in
                        switch phase {
                        case .success(let img):
                            img
                                .resizable()
                                .aspectRatio(contentMode: .fill)
                                .frame(width: 72, height: 72)
                                .clipped()
                                .cornerRadius(8)
                        default:
                            Color.gray.opacity(0.1)
                                .frame(width: 72, height: 72)
                                .cornerRadius(8)
                        }
                    }
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
        
        let formatter2 = RelativeDateTimeFormatter()
        formatter2.unitsStyle = .abbreviated
        return formatter2.localizedString(for: date, relativeTo: Date())
    }
}

// MARK: - Sheet Components

struct FilterPanel: View {
    @Binding var selectedKeyword: String?
    @Binding var mediaFilter: String
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
                            isSelected: mediaFilter == "all",
                            theme: theme,
                            accessibilityId: "filter.allInfoButton"
                        ) {
                            mediaFilter = "all"
                        }
                        FilterButton(
                            text: "📹 " + i18n.t("mediaOnly"),
                            isSelected: mediaFilter == "media_only",
                            theme: theme,
                            accessibilityId: "filter.mediaOnlyButton"
                        ) {
                            mediaFilter = "media_only"
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
            Text("Add Custom RSS/Web Feed")
                .font(.headline)
                .foregroundColor(theme.colors.text)
                .padding(.top, 10)
            
            TextField("Feed/Webpage Title...", text: $customUrlTitle)
                .padding()
                .background(theme.colors.card)
                .cornerRadius(8)
                .foregroundColor(theme.colors.text)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(theme.colors.border, lineWidth: 1))
                .accessibilityIdentifier("customUrl.titleField")
            
            TextField("https://...", text: $customUrlString)
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
            .navigationTitle("Reorder Sources")
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
