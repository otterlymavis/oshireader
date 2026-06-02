import SwiftUI

struct SearchView: View {
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    
    @State private var searchQuery = ""
    
    var filteredResults: [FeedItem] {
        guard !searchQuery.isEmpty else { return [] }
        let query = searchQuery.lowercased()
        
        return db.feedItems.filter { item in
            let titleMatch = item.title?.lowercased().contains(query) ?? false
            let contentMatch = item.content_text?.lowercased().contains(query) ?? false
            let authorMatch = item.author?.lowercased().contains(query) ?? false
            let keywordMatch = item.watch_term_keyword.lowercased().contains(query)
            return titleMatch || contentMatch || authorMatch || keywordMatch
        }
    }
    
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selectedItem: FeedItem? = nil
    
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
                        if let item = selectedItem {
                            ReaderView(feedItem: item)
                                .id(item.id)
                        } else {
                            VStack(spacing: 16) {
                                Text("🔍")
                                    .font(.system(size: 64))
                                Text("検索結果から記事を選択してください")
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
            // Search bar
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundColor(theme.colors.textMuted)
                TextField(i18n.t("searchPlaceholder"), text: $searchQuery)
                    .foregroundColor(theme.colors.text)
                    .autocapitalization(.none)
                    .accessibilityIdentifier("search.field")
                
                if !searchQuery.isEmpty {
                    Button(action: { searchQuery = "" }) {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(theme.colors.textMuted)
                    }
                    .accessibilityIdentifier("search.clearButton")
                }
            }
            .padding()
            .background(theme.colors.card)
            .cornerRadius(12)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(theme.colors.border, lineWidth: 1))
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            
            if searchQuery.isEmpty {
                // Display custom tracked URLs if search is empty
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text("カスタム追跡URL 🌐")
                            .font(.headline)
                            .foregroundColor(theme.colors.text)
                        Spacer()
                    }
                    .padding(.horizontal, 18)
                    .padding(.top, 14)
                    
                    if db.customUrls.isEmpty {
                        Spacer()
                        VStack {
                            Text("📭")
                                .font(.system(size: 44))
                            Text("カスタムURLが登録されていません")
                                .font(.caption)
                                .foregroundColor(theme.colors.textMuted)
                        }
                        .frame(maxWidth: .infinity)
                        Spacer()
                    } else {
                        List {
                            ForEach(db.customUrls) { customUrl in
                                HStack {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(customUrl.title ?? "No Title")
                                            .font(.subheadline)
                                            .bold()
                                            .foregroundColor(theme.colors.text)
                                        Text(customUrl.url)
                                            .font(.caption)
                                            .foregroundColor(theme.colors.textMuted)
                                            .lineLimit(1)
                                    }
                                    Spacer()
                                    
                                    Button(action: {
                                        db.removeCustomUrl(id: customUrl.id)
                                    }) {
                                        Image(systemName: "trash")
                                            .foregroundColor(.red)
                                    }
                                    .buttonStyle(PlainButtonStyle())
                                    .accessibilityIdentifier("search.customUrlDelete.\(customUrl.id)")
                                }
                                .padding(.vertical, 4)
                                .listRowBackground(theme.colors.card)
                                .listRowSeparatorTint(theme.colors.divider)
                            }
                        }
                        .listStyle(.plain)
                    }
                }
            } else {
                // Display search results
                if filteredResults.isEmpty {
                    Spacer()
                    Text("(´• ω •`)")
                        .font(.system(size: 32))
                        .padding(.bottom, 6)
                    Text("検索結果がありません")
                        .font(.subheadline)
                        .foregroundColor(theme.colors.textMuted)
                    Spacer()
                } else {
                    List(filteredResults) { item in
                        if horizontalSizeClass == .regular {
                            Button(action: {
                                selectedItem = item
                            }) {
                                FeedCard(item: item, isSaved: db.savedPages.contains(where: { $0.id == item.id }), theme: theme)
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 12)
                                            .stroke(theme.colors.primary, lineWidth: selectedItem?.id == item.id ? 2 : 0)
                                    )
                            }
                            .buttonStyle(PlainButtonStyle())
                            .listRowInsets(EdgeInsets(top: 4, leading: 14, bottom: 4, trailing: 14))
                            .listRowBackground(Color.clear)
                            .listRowSeparator(.hidden)
                        } else {
                            NavigationLink(destination: ReaderView(feedItem: item)) {
                                FeedCard(item: item, isSaved: db.savedPages.contains(where: { $0.id == item.id }), theme: theme)
                            }
                            .buttonStyle(PlainButtonStyle())
                            .listRowInsets(EdgeInsets(top: 4, leading: 14, bottom: 4, trailing: 14))
                            .listRowBackground(Color.clear)
                            .listRowSeparator(.hidden)
                        }
                    }
                    .listStyle(.plain)
                }
            }
        }
        .accessibilityIdentifier("search.screen")
        .navigationTitle(i18n.t("tabSearch"))
        .navigationBarTitleDisplayMode(.inline)
    }
}
