import SwiftUI

struct SettingsView: View {
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    @StateObject private var appearance = AppearanceManager.shared
    
    @State private var showingAddKeywordAlert = false
    @State private var newKeyword = ""
    @State private var newCollectionMode = "all_info"
    
    let allPlatforms = [
        ("youtube", "📹 YouTube"),
        ("niconico", "💬 NicoNico"),
        ("tver", "📺 TVer"),
        ("note", "📝 Note"),
        ("girlschannel", "👭 GirlsChannel"),
        ("5ch", "💬 5ch"),
        ("togetter", "🐧 Togetter"),
        ("news", "📰 General News"),
        ("yahoonews", "🇯🇵 YahooNews"),
        ("mdpr", "💅 ModelPress")
    ]
    
    var body: some View {
        NavigationStack {
            Form {
                // Section: Keywords management
                Section(header: Text(i18n.t("watchTerms"))) {
                    ForEach(db.terms) { term in
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(term.keyword)
                                    .font(.headline)
                                    .foregroundColor(theme.colors.text)
                                Text(term.collection_mode == "media_only" ? "📹 メディアのみ" : "📄 全情報")
                                    .font(.caption)
                                    .foregroundColor(theme.colors.textMuted)
                            }
                            Spacer()
                            
                            // Push notifications bell button
                            Button(action: {
                                db.updateTerm(id: term.id, notifyOnNew: !term.notify_on_new)
                            }) {
                                Image(systemName: term.notify_on_new ? "bell.fill" : "bell.slash")
                                    .foregroundColor(term.notify_on_new ? theme.colors.primary : theme.colors.textMuted)
                                    .font(.title3)
                                    .padding(.horizontal, 8)
                            }
                            .buttonStyle(PlainButtonStyle())
                            .accessibilityIdentifier("settings.keywordBell.\(term.keyword)")
                            
                            Toggle("", isOn: Binding(
                                get: { term.is_active },
                                set: { next in
                                    db.updateTerm(id: term.id, isActive: next)
                                    // Async updates backend too
                                    Task {
                                        _ = try? await NetworkManager.shared.updateWatchTerm(id: term.id, isActive: next)
                                    }
                                }
                            ))
                            .tint(theme.colors.primary)
                            .accessibilityIdentifier("settings.keywordToggle.\(term.keyword)")
                        }
                        .accessibilityIdentifier("settings.keywordRow.\(term.keyword)")
                    }
                    .onDelete { offsets in
                        for index in offsets {
                            let term = db.terms[index]
                            db.deleteTerm(id: term.id)
                            Task {
                                _ = try? await NetworkManager.shared.deleteWatchTerm(id: term.id)
                            }
                        }
                    }
                    
                    Button(action: { showingAddKeywordAlert.toggle() }) {
                        HStack {
                            Image(systemName: "plus.circle.fill")
                            Text(i18n.t("addKeyword"))
                        }
                        .foregroundColor(theme.colors.primary)
                    }
                    .accessibilityIdentifier("settings.addKeywordButton")
                }
                
                // Section: Subscribed Platforms
                Section(header: Text("配信プラットフォーム設定")) {
                    ForEach(allPlatforms, id: \.0) { key, label in
                        let isSubscribed = db.subscribedPlatforms.contains(key)
                        Toggle(label, isOn: Binding(
                            get: { isSubscribed },
                            set: { value in
                                var list = db.subscribedPlatforms
                                if value {
                                    if !list.contains(key) { list.append(key) }
                                } else {
                                    list.removeAll(where: { $0 == key })
                                }
                                db.setSubscribedPlatforms(platforms: list)
                            }
                        ))
                        .tint(theme.colors.primary)
                        .accessibilityIdentifier("settings.platformToggle.\(key)")
                    }
                }
                
                // Section: Customizations
                Section(header: Text("テーマとカスタマイズ")) {
                    // Theme picker
                    Picker("アプリテーマ", selection: $theme.mode) {
                        Text("ライト").tag(AppThemeMode.light)
                        Text("ダーク").tag(AppThemeMode.dark)
                    }
                    .pickerStyle(.segmented)
                    
                    // Language selection
                    Picker(i18n.t("language"), selection: Binding(
                        get: { i18n.lang },
                        set: { i18n.setLanguage($0) }
                    )) {
                        Text("日本語").tag("ja")
                        Text("English").tag("en")
                        Text("繁體中文").tag("zh-TW")
                        Text("简体中文").tag("zh-CN")
                    }

                    Picker("Font", selection: $appearance.fontChoice) {
                        ForEach(AppFontChoice.allCases) { choice in
                            Text(choice.displayName).tag(choice)
                        }
                    }
                    .pickerStyle(.segmented)
                    .accessibilityIdentifier("settings.fontPicker")

                    Picker("Font Size", selection: $appearance.fontSizeChoice) {
                        ForEach(AppFontSizeChoice.allCases) { choice in
                            Text(choice.displayName).tag(choice)
                        }
                    }
                    .pickerStyle(.segmented)
                    .accessibilityIdentifier("settings.fontSizePicker")
                    
                    // Wallpaper reset
                    if db.wallpaper != nil {
                        Button(action: { db.setWallpaper(url: nil) }) {
                            Text(i18n.t("clearWallpaper"))
                                .foregroundColor(.red)
                        }
                    }
                }


                Section(header: Text("プライバシー")) {
                    NavigationLink(destination: PrivacyPolicyView(theme: theme)) {
                        Label("Privacy Policy", systemImage: "hand.raised")
                    }
                    .accessibilityIdentifier("settings.privacyPolicyLink")
                }
                
                // Section: Statistics
                let stats = db.getStats()
                Section(header: Text(i18n.t("stats"))) {
                    HStack {
                        Text("記事総数")
                        Spacer()
                        Text("\(stats.total) 件")
                            .bold()
                            .foregroundColor(theme.colors.textSub)
                    }
                    
                    ForEach(stats.byPlatform.sorted(by: { $0.key < $1.key }), id: \.key) { key, count in
                        let meta = theme.metadata(for: key)
                        HStack {
                            Text("\(meta.icon) \(meta.name)")
                            Spacer()
                            Text("\(count) 件")
                                .foregroundColor(theme.colors.textMuted)
                        }
                    }
                }
            }
            .accessibilityIdentifier("settings.screen")
            .navigationTitle(i18n.t("settingsTitle"))
            .navigationBarTitleDisplayMode(.inline)
            .background(theme.colors.bg)
            .sheet(isPresented: $showingAddKeywordAlert) {
                VStack(spacing: 16) {
                    Text(i18n.t("addKeyword"))
                        .font(.headline)
                        .padding(.top, 14)
                    
                    TextField(i18n.t("inputKeyword"), text: $newKeyword)
                        .padding()
                        .background(theme.colors.divider)
                        .cornerRadius(8)
                        .accessibilityIdentifier("settings.keywordField")
                    
                    Picker("収集モード", selection: $newCollectionMode) {
                        Text("📄 全情報").tag("all_info")
                        Text("📹 メディアのみ").tag("media_only")
                    }
                    .pickerStyle(.segmented)
                    
                    HStack(spacing: 10) {
                        Button("キャンセル") {
                            newKeyword = ""
                            showingAddKeywordAlert = false
                        }
                        .accessibilityIdentifier("settings.cancelAddKeywordButton")
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(theme.colors.divider)
                        .cornerRadius(10)
                        
                        Button("追加") {
                            guard !newKeyword.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
                            let savedTerm = db.saveTerm(keyword: newKeyword, collectionMode: newCollectionMode)
                            
                            // Send to backend in background
                            Task {
                                if let serverTerm = try? await NetworkManager.shared.createWatchTerm(keyword: savedTerm.keyword, collectionMode: savedTerm.collection_mode) {
                                    db.replaceTerm(localId: savedTerm.id, with: serverTerm)
                                }
                            }
                            
                            newKeyword = ""
                            showingAddKeywordAlert = false
                        }
                        .accessibilityIdentifier("settings.confirmAddKeywordButton")
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(theme.colors.primary)
                        .foregroundColor(.white)
                        .cornerRadius(10)
                    }
                    .padding(.top, 10)
                    
                    Spacer()
                }
                .accessibilityIdentifier("settings.addKeywordSheet")
                .padding()
                .background(theme.colors.bg)
                .presentationDetents([.medium])
            }
        }
    }
}

struct PrivacyPolicyView: View {
    let theme: ThemeManager

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                policySection(
                    title: "Data Stored on This Device",
                    body: "Oshi Reader stores watch keywords, feed items, saved pages, custom URLs, display settings, wallpaper choices, and avatar compositions locally on this device."
                )

                policySection(
                    title: "Data Sent for App Functionality",
                    body: "When you add watch keywords, refresh feeds, search stickers, translate sticker queries, or open articles, related keywords, search text, URLs, and article requests may be sent to the configured Oshi Reader backend, Google services, Irasutoya/Blogger feeds, news feeds, and the websites you choose to open."
                )

                policySection(
                    title: "Tracking and Advertising",
                    body: "This app does not use advertising identifiers, App Tracking Transparency, third-party ad SDKs, or data broker tracking. Data is used to provide feed, reader, search, translation, and customization features."
                )

                policySection(
                    title: "Permissions",
                    body: "This version does not request access to location, contacts, photos, camera, microphone, Bluetooth, health data, or motion sensors."
                )
            }
            .padding(18)
        }
        .accessibilityIdentifier("privacy.screen")
        .background(theme.colors.bg)
        .navigationTitle("Privacy Policy")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func policySection(title: String, body: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.headline)
                .foregroundColor(theme.colors.text)
            Text(body)
                .font(.body)
                .foregroundColor(theme.colors.textSub)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
