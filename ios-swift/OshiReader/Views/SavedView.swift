import SwiftUI

extension SavedPage {
    func toFeedItem() -> FeedItem {
        return FeedItem(
            id: id,
            platform: platform,
            url: url,
            title: title,
            content_text: nil,
            author: nil,
            thumbnail_url: nil,
            media_type: "article",
            published_at: saved_at,
            watch_term_keyword: "",
            fetched_at: saved_at
        )
    }
}

struct SavedView: View {
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selectedPage: SavedPage? = nil
    
    var body: some View {
        ZStack {
            theme.colors.bg.ignoresSafeArea()
            
            if horizontalSizeClass == .regular {
                HStack(spacing: 0) {
                    NavigationStack {
                        mainContentColumn
                    }
                    .frame(width: 380)
                    
                    Divider()
                        .background(theme.colors.divider)
                    
                    NavigationStack {
                        if let page = selectedPage {
                            ReaderView(
                                feedItem: page.toFeedItem(),
                                siblingItems: db.savedPages.map { $0.toFeedItem() },
                                onNavigate: { item in
                                    selectedPage = db.savedPages.first(where: { $0.id == item.id })
                                }
                            )
                            .id(page.id)
                        } else {
                            VStack(spacing: 16) {
                                Text("📂")
                                    .font(.system(size: 64))
                                Text(i18n.t("savedSelectArticle"))
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
        VStack(spacing: 0) {
            if db.savedPages.isEmpty {
                Spacer()
                VStack(spacing: 12) {
                    Text("📂")
                        .font(.system(size: 48))
                    Text(i18n.t("savedEmptyTitle"))
                        .font(.headline)
                        .foregroundColor(theme.colors.textSub)
                    Text(i18n.t("savedEmptyBody"))
                        .font(.caption)
                        .foregroundColor(theme.colors.textMuted)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 48)
                }
                Spacer()
            } else {
                List {
                    ForEach(db.savedPages) { page in
                        if horizontalSizeClass == .regular {
                            Button(action: {
                                selectedPage = page
                            }) {
                                SavedPageCard(page: page, theme: theme)
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 12)
                                            .stroke(theme.colors.primary, lineWidth: selectedPage?.id == page.id ? 2 : 0)
                                    )
                            }
                            .buttonStyle(PlainButtonStyle())
                            .listRowInsets(EdgeInsets(top: 4, leading: 14, bottom: 4, trailing: 14))
                            .listRowBackground(Color.clear)
                            .listRowSeparator(.hidden)
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                Button(role: .destructive) {
                                    db.removeSaved(id: page.id)
                                    if selectedPage?.id == page.id {
                                        selectedPage = nil
                                    }
                                } label: {
                                    Label(i18n.t("delete"), systemImage: "trash")
                                }
                            }
                        } else {
                            NavigationLink(destination: ReaderView(feedItem: page.toFeedItem(), siblingItems: db.savedPages.map { $0.toFeedItem() })) {
                                SavedPageCard(page: page, theme: theme)
                            }
                            .buttonStyle(PlainButtonStyle())
                            .listRowInsets(EdgeInsets(top: 4, leading: 14, bottom: 4, trailing: 14))
                            .listRowBackground(Color.clear)
                            .listRowSeparator(.hidden)
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                Button(role: .destructive) {
                                    db.removeSaved(id: page.id)
                                } label: {
                                    Label(i18n.t("delete"), systemImage: "trash")
                                }
                            }
                        }
                    }
                }
                .listStyle(.plain)
            }
        }
        .accessibilityIdentifier("saved.screen")
        .navigationTitle(i18n.t("tabSaved"))
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct SavedPageCard: View {
    let page: SavedPage
    let theme: ThemeManager
    
    var body: some View {
        let meta = theme.metadata(for: page.platform)
        
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                HStack(spacing: 3) {
                    Text(meta.icon)
                        .font(.caption)
                    Text(meta.name)
                        .font(.system(size: 11, weight: .bold))
                }
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .background(meta.bg)
                .foregroundColor(meta.fg)
                .cornerRadius(6)
                
                Spacer()
                
                Text(formattedDate(page.saved_at))
                    .font(.caption2)
                    .foregroundColor(theme.colors.textMuted)
            }
            
            Text(cleanDisplayText(page.title) ?? page.url)
                .font(.subheadline)
                .fontWeight(.bold)
                .foregroundColor(theme.colors.text)
                .lineLimit(2)
                .multilineTextAlignment(.leading)
        }
        .padding(12)
        .background(theme.colors.card)
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(theme.mode == .dark ? 0.2 : 0.04), radius: 5, x: 0, y: 2)
        .accessibilityIdentifier("saved.card.\(page.id)")
    }
    
    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .short
        f.timeStyle = .short
        return f
    }()

    private func formattedDate(_ isoString: String) -> String {
        guard let date = parseISO8601Date(isoString) else { return isoString }
        return Self.dateFormatter.string(from: date)
    }
}
