import SwiftUI

struct OshiView: View {
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    
    @State private var activePage = 0
    @State private var showEditorKeyword: String? = nil
    
    var sortedTerms: [WatchTerm] {
        return db.terms.sorted(by: { $0.created_at < $1.created_at })
    }
    
    var body: some View {
        NavigationStack {
            ZStack {
                theme.colors.bg.ignoresSafeArea()
                
                if sortedTerms.isEmpty {
                    VStack(spacing: 12) {
                        Text("(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧")
                            .font(.title)
                        Text(i18n.t("oshiEmpty"))
                            .font(.headline)
                            .foregroundColor(theme.colors.primary)
                        Text(i18n.t("oshiEmptyBody"))
                            .font(.subheadline)
                            .foregroundColor(theme.colors.textMuted)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 48)
                    }
                } else {
                    VStack(spacing: 0) {
                        // Static Header
                        HStack {
                            Text("推しリスト ✨")
                                .font(.title2)
                                .fontWeight(.bold)
                                .foregroundColor(theme.colors.text)
                            Spacer()
                            Text("\(sortedTerms.count)人の推しを追跡中")
                                .font(.caption)
                                .foregroundColor(theme.colors.textMuted)
                        }
                        .padding(.horizontal, 18)
                        .padding(.top, 14)
                        .padding(.bottom, 10)
                        .background(theme.colors.card)
                        .overlay(
                            Rectangle()
                                .frame(height: 0.5)
                                .foregroundColor(theme.colors.divider),
                            alignment: .bottom
                        )
                        
                        // TabView Pager for horizontal paging
                        TabView(selection: $activePage) {
                            ForEach(0..<sortedTerms.count, id: \.self) { idx in
                                let term = sortedTerms[idx]
                                let count = db.feedItems.filter({ $0.watch_term_keyword == term.keyword }).count
                                let layers = db.compositions[term.keyword] ?? []
                                
                                OshiPage(term: term, count: count, layers: layers, theme: theme, i18n: i18n) {
                                    showEditorKeyword = term.keyword
                                }
                                .tag(idx)
                            }
                        }
                        .tabViewStyle(.page(indexDisplayMode: .never))
                        
                        // Custom Page Dots
                        if sortedTerms.count > 1 {
                            HStack(spacing: 7) {
                                ForEach(0..<sortedTerms.count, id: \.self) { idx in
                                    Circle()
                                        .frame(width: idx == activePage ? 14 : 6, height: 6)
                                        .foregroundColor(idx == activePage ? theme.colors.primary : theme.colors.border)
                                        .animation(.spring(), value: activePage)
                                }
                            }
                            .padding(.vertical, 12)
                        }
                    }
                }
            }
            .navigationTitle("My Oshi")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(item: $showEditorKeyword) { keyword in
                AvatarEditorView(keyword: keyword)
            }
            .accessibilityIdentifier("oshi.screen")
        }
    }
}

struct OshiPage: View {
    let term: WatchTerm
    let count: Int
    let layers: [AvatarLayer]
    let theme: ThemeManager
    let i18n: I18nManager
    let onEdit: () -> Void
    
    var body: some View {
        GeometryReader { geometry in
            let W = geometry.size.width
            let H = W // Square avatar canvas
            let scaleFactor = W / 300.0 // 300 is our base canvas coordinate system
            
            VStack(spacing: 0) {
                // Composed Avatar Canvas
                Button(action: onEdit) {
                    ZStack {
                        // Canvas Background
                        Rectangle()
                            .fill(theme.mode == .dark ? Color(white: 0.1) : Color(white: 0.94))
                            .frame(width: W, height: H)
                        
                        if layers.isEmpty {
                            VStack(spacing: 8) {
                                Text("🎨")
                                    .font(.system(size: 56))
                                Text("タップしてアバターを作成")
                                    .font(.system(size: 13, weight: .bold))
                                    .foregroundColor(theme.colors.primary)
                            }
                        } else {
                            // Stack layers by zIndex
                            let sortedLayers = layers.sorted(by: { $0.zIndex < $1.zIndex })
                            ForEach(sortedLayers) { layer in
                                let size = 90.0 * layer.scale * scaleFactor
                                let cropX = (layer.cropX ?? 0.0) * scaleFactor
                                let cropY = (layer.cropY ?? 0.0) * scaleFactor
                                let cropScale = layer.cropScale ?? 1.0
                                
                                if let url = URL(string: layer.imageUrl) {
                                    AsyncImage(url: url) { image in
                                        image
                                            .resizable()
                                            .aspectRatio(contentMode: .fit)
                                            .scaleEffect(cropScale)
                                            .offset(x: cropX, y: cropY)
                                            .frame(width: size, height: size)
                                            .clipped()
                                            .rotationEffect(Angle(degrees: layer.rotation ?? 0.0))
                                    } placeholder: {
                                        ProgressView()
                                            .frame(width: size, height: size)
                                    }
                                    .position(x: (layer.x + 45.0 * layer.scale) * scaleFactor,
                                              y: (layer.y + 45.0 * layer.scale) * scaleFactor)
                                }
                            }
                        }
                    }
                }
                .buttonStyle(PlainButtonStyle())
                .accessibilityIdentifier("oshi.avatarCanvas.\(term.keyword)")
                .frame(width: W, height: H)
                
                // Info Panel
                HStack(alignment: .center) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(term.keyword)
                            .font(.title3)
                            .bold()
                            .foregroundColor(theme.colors.text)
                            .lineLimit(1)
                        
                        HStack(spacing: 6) {
                            Text("📰 \(count)")
                                .font(.system(size: 12, weight: .bold))
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(theme.colors.primaryBg)
                                .foregroundColor(theme.colors.primary)
                                .cornerRadius(99)
                            
                            Text(term.collection_mode == "media_only" ? "📹 メディア" : "📄 全情報")
                                .font(.system(size: 12, weight: .medium))
                                .padding(.horizontal, 7)
                                .padding(.vertical, 3)
                                .background(theme.colors.divider)
                                .foregroundColor(theme.colors.textMuted)
                                .cornerRadius(99)
                            
                            Circle()
                                .frame(width: 8, height: 8)
                                .foregroundColor(term.is_active ? theme.colors.accentGreen : theme.colors.border)
                        }
                    }
                    
                    Spacer()
                    
                    Button(action: onEdit) {
                        HStack(spacing: 4) {
                            Text("✏️")
                            Text("Edit")
                        }
                        .font(.system(size: 13, weight: .bold))
                        .padding(.horizontal, 16)
                        .padding(.vertical, 9)
                        .background(theme.colors.primaryBg)
                        .foregroundColor(theme.colors.primary)
                        .cornerRadius(12)
                    }
                    .accessibilityIdentifier("oshi.editButton.\(term.keyword)")
                }
                .padding(18)
                .background(theme.colors.card)
                
                Spacer()
            }
        }
    }
}
