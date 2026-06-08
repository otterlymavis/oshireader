import SwiftUI

private struct SearchLink: Identifiable {
    let id: String
    let group: String
    let label: String
    let domain: String
    let platform: String
    let makeUrl: (String) -> String
}

private struct SearchGroupMeta {
    let symbol: String
    let color: Color
}

struct SearchView: View {
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared

    @State private var keyword = ""
    @State private var selectedGroup = "News"
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selectedItem: FeedItem? = nil
    @FocusState private var fieldFocused: Bool

    private var activeTerms: [WatchTerm] {
        db.terms.filter(\.is_active)
    }

    private var trimmedKeyword: String {
        keyword.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var groupedLinks: [(String, [SearchLink])] {
        var map = [String: [SearchLink]]()
        var order = [String]()
        for link in staticSearchLinks + customSearchLinks {
            if map[link.group] == nil {
                order.append(link.group)
                map[link.group] = []
            }
            map[link.group]?.append(link)
        }
        return order.map { ($0, map[$0] ?? []) }
    }

    private var selectedLinks: [SearchLink] {
        groupedLinks.first(where: { $0.0 == selectedGroup })?.1 ?? groupedLinks.first?.1 ?? []
    }

    private var customSearchLinks: [SearchLink] {
        db.customUrls.map { entry in
            SearchLink(
                id: entry.id,
                group: "Custom",
                label: entry.title.flatMap { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0 } ?? entry.url,
                domain: URL(string: entry.url)?.host ?? entry.url,
                platform: "custom",
                makeUrl: { _ in entry.url }
            )
        }
    }

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
                            emptyReaderPrompt
                        }
                    }
                }
            } else {
                NavigationStack {
                    mainContentColumn
                }
            }
        }
        .onAppear {
            if keyword.isEmpty, let first = activeTerms.first?.keyword {
                keyword = first
            }
        }
        .onChange(of: db.customUrls) { _ in
            if selectedGroup == "Custom", customSearchLinks.isEmpty {
                selectedGroup = "News"
            }
        }
    }

    private var mainContentColumn: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                searchBox
                categoryStrip
                selectedGroupSection
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
        }
        .scrollDismissesKeyboard(.interactively)
        .accessibilityIdentifier("search.screen")
        .background(theme.colors.bg)
        .navigationTitle(i18n.t("tabSearch"))
        .navigationBarTitleDisplayMode(.inline)
    }

    private var searchBox: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(theme.colors.textMuted)

                TextField(i18n.t("keyword"), text: $keyword)
                    .foregroundColor(theme.colors.text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.search)
                    .focused($fieldFocused)
                    .onSubmit { fieldFocused = false }
                    .accessibilityIdentifier("search.keywordField")

                if !keyword.isEmpty {
                    Button {
                        keyword = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(theme.colors.textMuted)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("search.clearButton")
                }
            }
            .frame(minHeight: 42)
            .padding(.horizontal, 12)
            .background(theme.colors.divider)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(theme.colors.border, lineWidth: 1))
            .cornerRadius(8)

            if activeTerms.isEmpty {
                Text(i18n.t("searchAddKeywordHint"))
                    .font(.system(size: 13))
                    .foregroundColor(theme.colors.textMuted)
            } else {
                FlowLayout(spacing: 8) {
                    ForEach(activeTerms) { term in
                        SearchChip(
                            text: term.keyword,
                            isSelected: term.keyword == trimmedKeyword,
                            theme: theme
                        ) {
                            keyword = term.keyword
                            fieldFocused = false
                        }
                    }
                }
            }
        }
        .padding(12)
        .background(theme.colors.card)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(theme.colors.border, lineWidth: 1))
        .cornerRadius(8)
    }

    private var categoryStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(groupedLinks, id: \.0) { group, links in
                    let meta = groupMeta[group] ?? groupMeta["News"]!
                    let active = group == selectedGroup
                    Button {
                        selectedGroup = group
                    } label: {
                        HStack(spacing: 7) {
                            Image(systemName: meta.symbol)
                                .font(.system(size: 14, weight: .semibold))
                            Text(i18n.tSearchGroup(group))
                                .font(.system(size: 13, weight: active ? .bold : .semibold))
                            Text("\(links.count)")
                                .font(.system(size: 12, weight: .bold))
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .foregroundColor(active ? theme.colors.primary : theme.colors.textSub)
                        .background(active ? theme.colors.primaryBg : theme.colors.card)
                        .overlay(RoundedRectangle(cornerRadius: 999).stroke(active ? theme.colors.primary : theme.colors.border, lineWidth: 1))
                        .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("search.category.\(group)")
                }
            }
            .padding(.trailing, 16)
        }
        .padding(.horizontal, -16)
        .padding(.leading, 16)
    }

    private var selectedGroupSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(i18n.tSearchGroup(selectedGroup))
                    .font(.system(size: 15, weight: .bold))
                    .foregroundColor(theme.colors.text)
                Spacer()
                Text(selectedGroup == "Custom" ? i18n.t("searchSavedUrls") : (trimmedKeyword.isEmpty ? i18n.t("searchEnterKeyword") : trimmedKeyword))
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(theme.colors.textMuted)
            }

            if selectedLinks.isEmpty {
                Text(i18n.t("noCustomUrlsAdded"))
                    .font(.system(size: 13))
                    .foregroundColor(theme.colors.textMuted)
                    .padding(.vertical, 10)
            } else {
                ForEach(selectedLinks) { link in
                    if horizontalSizeClass == .regular {
                        searchLinkButton(link)
                    } else {
                        searchLinkNavigationRow(link)
                    }
                }
            }
        }
    }

    private func searchLinkNavigationRow(_ link: SearchLink) -> some View {
        let disabled = linkRequiresKeyword(link) && trimmedKeyword.isEmpty

        return NavigationLink(destination: ReaderView(feedItem: feedItem(for: link))) {
            searchLinkRowContent(link)
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .simultaneousGesture(TapGesture().onEnded { fieldFocused = false })
        .accessibilityIdentifier("search.link.\(link.id)")
    }

    private func searchLinkButton(_ link: SearchLink) -> some View {
        let disabled = linkRequiresKeyword(link) && trimmedKeyword.isEmpty

        return Button {
            fieldFocused = false
            open(link)
        } label: {
            searchLinkRowContent(link)
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .accessibilityIdentifier("search.link.\(link.id)")
    }

    private func searchLinkRowContent(_ link: SearchLink) -> some View {
        let meta = groupMeta[link.group] ?? groupMeta["News"]!
        let disabled = linkRequiresKeyword(link) && trimmedKeyword.isEmpty

        return HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(meta.color.opacity(0.12))
                Image(systemName: meta.symbol)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundColor(meta.color)
            }
            .frame(width: 34, height: 34)

            VStack(alignment: .leading, spacing: 2) {
                Text(link.label)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(theme.colors.text)
                    .lineLimit(1)
                Text(link.domain)
                    .font(.system(size: 12))
                    .foregroundColor(theme.colors.textMuted)
                    .lineLimit(1)
            }

            Spacer()

            Image(systemName: link.group == "Custom" ? "link" : "arrow.up.forward.square")
                .font(.system(size: 17, weight: .medium))
                .foregroundColor(theme.colors.textMuted)
        }
        .frame(minHeight: 58)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(theme.colors.card)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(theme.colors.border, lineWidth: 1))
        .cornerRadius(8)
        .opacity(disabled ? 0.45 : 1)
    }

    private var emptyReaderPrompt: some View {
        VStack(spacing: 16) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 58, weight: .light))
                .foregroundColor(theme.colors.textMuted)
            Text(i18n.t("searchSelectArticle"))
                .font(.headline)
                .foregroundColor(theme.colors.textSub)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.colors.bg)
    }

    private func open(_ link: SearchLink) {
        selectedItem = feedItem(for: link)
    }

    private func linkRequiresKeyword(_ link: SearchLink) -> Bool {
        !db.customUrls.contains(where: { $0.id == link.id })
    }

    private func feedItem(for link: SearchLink) -> FeedItem {
        let query = trimmedKeyword
        let url = link.makeUrl(query)
        let title = link.group == "Custom" ? link.label : "\(link.label): \(query)"
        let now = ISO8601DateFormatter().string(from: Date())
        return FeedItem(
            id: link.group == "Custom" ? link.id : "search:\(link.id):\(query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? query)",
            platform: link.platform,
            url: url,
            title: title,
            content_text: nil,
            author: link.domain,
            thumbnail_url: nil,
            media_type: "article",
            published_at: now,
            watch_term_keyword: link.group == "Custom" ? "" : query,
            fetched_at: now
        )
    }
}

private struct SearchChip: View {
    let text: String
    let isSelected: Bool
    let theme: ThemeManager
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(text)
                .font(.system(size: 12, weight: .medium))
                .lineLimit(1)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(isSelected ? theme.colors.primary : theme.colors.divider)
                .foregroundColor(isSelected ? .white : theme.colors.textSub)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

private let groupMeta: [String: SearchGroupMeta] = [
    "News": SearchGroupMeta(symbol: "newspaper", color: Color(red: 0.85, green: 0.14, blue: 0.14)),
    "Entertainment": SearchGroupMeta(symbol: "sparkles", color: Color(red: 0.48, green: 0.25, blue: 0.95)),
    "Magazines": SearchGroupMeta(symbol: "book", color: Color(red: 0.75, green: 0.09, blue: 0.36)),
    "Video": SearchGroupMeta(symbol: "play.circle", color: Color(red: 0.0, green: 0.47, blue: 0.8)),
    "Writing": SearchGroupMeta(symbol: "doc.text", color: Color(red: 0.08, green: 0.5, blue: 0.24)),
    "Social": SearchGroupMeta(symbol: "bubble.left.and.bubble.right", color: Color(red: 0.06, green: 0.46, blue: 0.43)),
    "Community": SearchGroupMeta(symbol: "person.2", color: Color(red: 0.76, green: 0.25, blue: 0.05)),
    "Web": SearchGroupMeta(symbol: "globe", color: Color(red: 0.15, green: 0.39, blue: 0.92)),
    "Shopping": SearchGroupMeta(symbol: "bag", color: Color(red: 0.86, green: 0.15, blue: 0.47)),
    "Custom": SearchGroupMeta(symbol: "link", color: Color(red: 0.32, green: 0.32, blue: 0.36))
]

private let staticSearchLinks: [SearchLink] = [
    SearchLink(id: "yahoo-news", group: "News", label: "Yahoo! News Japan", domain: "news.yahoo.co.jp", platform: "yahoonews") {
        "https://news.yahoo.co.jp/search?p=\($0.urlQueryEscaped)&ei=utf-8"
    },
    SearchLink(id: "google-news", group: "News", label: "Google News Japan", domain: "news.google.com", platform: "news") {
        "https://news.google.com/search?q=\($0.urlQueryEscaped)&hl=ja&gl=JP&ceid=JP%3Aja"
    },
    SearchLink(id: "bing-news", group: "News", label: "Bing News Japan", domain: "bing.com/news", platform: "news") {
        "https://www.bing.com/news/search?q=\($0.urlQueryEscaped)&cc=JP&setlang=ja"
    },
    SearchLink(id: "nhk", group: "News", label: "NHK News", domain: "www3.nhk.or.jp", platform: "news") {
        "https://www.google.com/search?q=\("\($0) site:www3.nhk.or.jp/news".urlQueryEscaped)"
    },
    SearchLink(id: "livedoor", group: "News", label: "Livedoor News", domain: "news.livedoor.com", platform: "news") {
        "https://news.livedoor.com/search/article/?keyword=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "line-news", group: "News", label: "LINE NEWS", domain: "news.line.me", platform: "news") {
        "https://news.line.me/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "msn-news", group: "News", label: "MSN Japan", domain: "msn.com/ja-jp", platform: "news") {
        "https://www.msn.com/ja-jp/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "modelpress", group: "Entertainment", label: "Modelpress", domain: "mdpr.jp", platform: "mdpr") {
        "https://mdpr.jp/search?keyword=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "natalie", group: "Entertainment", label: "Natalie", domain: "natalie.mu", platform: "news") {
        "https://natalie.mu/search?query=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "oricon", group: "Entertainment", label: "Oricon News", domain: "oricon.co.jp", platform: "news") {
        "https://www.oricon.co.jp/search/?qs=\($0.urlQueryEscaped)&cat=all"
    },
    SearchLink(id: "cinematoday", group: "Entertainment", label: "Cinema Today", domain: "cinematoday.jp", platform: "news") {
        "https://www.cinematoday.jp/search?keyword=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "realsound", group: "Entertainment", label: "Real Sound", domain: "realsound.jp", platform: "news") {
        "https://realsound.jp/?s=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "sponichi", group: "Entertainment", label: "Sponichi", domain: "sponichi.co.jp", platform: "news") {
        "https://www.sponichi.co.jp/search/index.html?Keywords=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "daily", group: "Entertainment", label: "Daily Sports", domain: "daily.co.jp", platform: "news") {
        "https://www.daily.co.jp/search/?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "hochi", group: "Entertainment", label: "Hochi Sports", domain: "hochi.news", platform: "news") {
        "https://hochi.news/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "bunshun", group: "Magazines", label: "文春オンライン", domain: "bunshun.jp", platform: "news") {
        "https://bunshun.jp/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "joseiseven", group: "Magazines", label: "女性セブン", domain: "josei7.com", platform: "news") {
        "https://josei7.com/?s=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "friday", group: "Magazines", label: "FRIDAY Digital", domain: "friday.kodansha.co.jp", platform: "news") {
        "https://friday.kodansha.co.jp/search/articles?query=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "jprime", group: "Magazines", label: "週刊女性PRIME", domain: "jprime.jp", platform: "news") {
        "https://www.jprime.jp/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "flash", group: "Magazines", label: "FLASH", domain: "smart-flash.jp", platform: "news") {
        "https://smart-flash.jp/?s=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "josei-jishin", group: "Magazines", label: "女性自身", domain: "jisin.jp", platform: "news") {
        "https://jisin.jp/search/?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "cyzo", group: "Magazines", label: "サイゾーウーマン", domain: "cyzowoman.com", platform: "news") {
        "https://www.cyzowoman.com/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "tver", group: "Video", label: "TVer", domain: "tver.jp", platform: "tver") {
        "https://tver.jp/search/\($0.urlQueryEscaped)"
    },
    SearchLink(id: "youtube", group: "Video", label: "YouTube", domain: "youtube.com", platform: "youtube") {
        "https://www.youtube.com/results?search_query=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "niconico", group: "Video", label: "Niconico", domain: "nicovideo.jp", platform: "niconico") {
        "https://www.nicovideo.jp/search/\($0.urlQueryEscaped)"
    },
    SearchLink(id: "dailymotion", group: "Video", label: "Dailymotion", domain: "dailymotion.com", platform: "custom") {
        "https://www.dailymotion.com/search/\($0.urlQueryEscaped)/videos"
    },
    SearchLink(id: "bilibili", group: "Video", label: "Bilibili", domain: "bilibili.com", platform: "custom") {
        "https://search.bilibili.com/all?keyword=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "note", group: "Writing", label: "note", domain: "note.com", platform: "note") {
        "https://note.com/search?q=\($0.urlQueryEscaped)&context=note"
    },
    SearchLink(id: "ameblo", group: "Writing", label: "Ameba Blog", domain: "ameblo.jp", platform: "news") {
        "https://search.ameba.jp/search.html?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "x", group: "Social", label: "X", domain: "x.com", platform: "twitter") {
        "https://x.com/search?q=\($0.urlQueryEscaped)&src=typed_query&f=live"
    },
    SearchLink(id: "threads", group: "Social", label: "Threads", domain: "threads.net", platform: "custom") {
        "https://www.threads.net/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "instagram", group: "Social", label: "Instagram", domain: "instagram.com", platform: "custom") {
        "https://www.instagram.com/explore/search/keyword/?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "twitterviewer", group: "Social", label: "Twitter Viewer", domain: "twitterviewer.net", platform: "twitter") {
        "https://twitterviewer.net/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "tiktok", group: "Social", label: "TikTok", domain: "tiktok.com", platform: "custom") {
        "https://www.tiktok.com/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "weibo", group: "Social", label: "Weibo", domain: "s.weibo.com", platform: "custom") {
        "https://s.weibo.com/weibo?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "5ch", group: "Community", label: "5ch", domain: "find.5ch.net", platform: "5ch") {
        "https://find.5ch.net/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "girlschannel", group: "Community", label: "GirlsChannel", domain: "girlschannel.net", platform: "girlschannel") {
        "https://girlschannel.net/topics/search/?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "togetter", group: "Community", label: "Togetter", domain: "togetter.com", platform: "togetter") {
        "https://togetter.com/search?q=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "google-web", group: "Web", label: "Google Japan", domain: "google.co.jp", platform: "custom") {
        "https://www.google.co.jp/search?q=\($0.urlQueryEscaped)&hl=ja&gl=JP"
    },
    SearchLink(id: "yahoo-web", group: "Web", label: "Yahoo! Japan Search", domain: "search.yahoo.co.jp", platform: "custom") {
        "https://search.yahoo.co.jp/search?p=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "yahoo-realtime", group: "Web", label: "Yahoo! Realtime", domain: "search.yahoo.co.jp/realtime", platform: "custom") {
        "https://search.yahoo.co.jp/realtime/search?p=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "mercari", group: "Shopping", label: "Mercari", domain: "jp.mercari.com", platform: "custom") {
        "https://jp.mercari.com/search?keyword=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "rakuten", group: "Shopping", label: "Rakuten Ichiba", domain: "rakuten.co.jp", platform: "custom") {
        "https://search.rakuten.co.jp/search/mall/\($0.urlQueryEscaped)/"
    },
    SearchLink(id: "yahoo-auctions", group: "Shopping", label: "Yahoo! Auctions", domain: "auctions.yahoo.co.jp", platform: "custom") {
        "https://auctions.yahoo.co.jp/search/search?p=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "surugaya", group: "Shopping", label: "Surugaya", domain: "suruga-ya.jp", platform: "custom") {
        "https://www.suruga-ya.jp/search?search_word=\($0.urlQueryEscaped)"
    },
    SearchLink(id: "animate", group: "Shopping", label: "Animate Online", domain: "animate-onlineshop.jp", platform: "custom") {
        "https://www.animate-onlineshop.jp/products/list.php?mode=search&smt=\($0.urlQueryEscaped)"
    },
]

private extension String {
    var urlQueryEscaped: String {
        addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? self
    }
}
