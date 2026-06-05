import SwiftUI

struct SettingsView: View {
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    @StateObject private var appearance = AppearanceManager.shared
    @StateObject private var notifications = NotificationManager.shared
    
    @State private var showingAddKeywordAlert = false
    @State private var showingClearAllAlert = false
    @State private var newKeyword = ""
    @State private var newCollectionMode = "all_info"
    @AppStorage("youtube_api_key") private var youtubeApiKey = ""
    @AppStorage("twitter_bearer_token") private var twitterBearerToken = ""
    @AppStorage("auto_translate_reader") private var autoTranslateReader = false
    
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
        ("mdpr", "💅 ModelPress"),
        ("oricon", "🎤 Oricon"),
        ("twitter", "𝕏 X"),
        ("custom", "🌐 Custom Feeds")
    ]
    
    var body: some View {
        NavigationStack {
            Form {
                // Section: Keywords management
                Section(header: Text(i18n.t("watchTerms"))) {
                    ForEach(db.terms) { term in
                        HStack {
                            NavigationLink(destination: AvatarEditorView(keyword: term.keyword)) {
                                ZStack {
                                    Circle()
                                        .fill(theme.colors.divider)
                                        .frame(width: 38, height: 38)
                                    if let avatar = db.oshiAvatars[term.keyword], let url = URL(string: avatar) {
                                        AsyncImage(url: url) { image in
                                            image
                                                .resizable()
                                                .aspectRatio(contentMode: .fill)
                                        } placeholder: {
                                            Text("🎨")
                                        }
                                        .frame(width: 38, height: 38)
                                        .clipShape(Circle())
                                    } else {
                                        Text("🎨")
                                            .font(.body)
                                    }
                                }
                            }
                            .buttonStyle(PlainButtonStyle())
                            .accessibilityIdentifier("settings.keywordAvatar.\(term.keyword)")

                            VStack(alignment: .leading, spacing: 4) {
                                Text(term.keyword)
                                    .font(.subheadline)
                                    .fontWeight(.semibold)
                                    .foregroundColor(theme.colors.text)
                            }
                            Spacer()

                            Button {
                                let next = term.collection_mode == "all_info" ? "media_only" : "all_info"
                                db.updateTerm(id: term.id, collectionMode: next)
                                Task {
                                    _ = try? await NetworkManager.shared.updateWatchTerm(id: term.id, collectionMode: next)
                                }
                            } label: {
                                Text(term.collection_mode == "media_only" ? "📹" : "📄")
                                    .font(.caption)
                                    .fontWeight(.bold)
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 5)
                                    .background(theme.colors.divider)
                                    .foregroundColor(theme.colors.textSub)
                                    .clipShape(Capsule())
                            }
                            .buttonStyle(PlainButtonStyle())
                            .accessibilityIdentifier("settings.keywordMode.\(term.keyword)")
                            
                            // Push notifications bell button
                            Button(action: {
                                let next = !term.notify_on_new
                                db.updateTerm(id: term.id, notifyOnNew: next)
                                Task {
                                    _ = try? await NetworkManager.shared.updateWatchTerm(id: term.id, notifyOnNew: next)
                                }
                            }) {
                                Image(systemName: term.notify_on_new ? "bell.fill" : "bell.slash")
                                    .foregroundColor(term.notify_on_new ? theme.colors.primary : theme.colors.textMuted)
                                    .font(.body)
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
                Section(header: Text(i18n.t("platformSettings"))) {
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

                Section(header: Text(i18n.t("notificationsSection"))) {
                    HStack {
                        Label("Push Notifications", systemImage: "bell.badge")
                        Spacer()
                        Text(notifications.statusText)
                            .foregroundColor(theme.colors.textMuted)
                    }
                    .accessibilityIdentifier("settings.notificationStatus")

                    Button {
                        Task {
                            _ = await notifications.requestAuthorization()
                        }
                    } label: {
                        Label("Enable Notifications", systemImage: "bell.badge.fill")
                            .foregroundColor(theme.colors.primary)
                    }
                    .accessibilityIdentifier("settings.enableNotificationsButton")

                    Button {
                        Task {
                            try? await notifications.sendTestNotification()
                        }
                    } label: {
                        Label("Send Test Notification", systemImage: "paperplane.fill")
                            .foregroundColor(theme.colors.primary)
                    }
                    .accessibilityIdentifier("settings.testNotificationButton")
                }

                Section(header: Text(i18n.t("readerSection"))) {
                    Toggle(i18n.t("autoTranslate"), isOn: $autoTranslateReader)
                        .tint(theme.colors.primary)
                        .accessibilityIdentifier("settings.autoTranslateToggle")
                }

                Section(header: Text(i18n.t("credentialsSection"))) {
                    SecureField("YouTube API Key", text: $youtubeApiKey)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .onSubmit {
                            Task { try? await NetworkManager.shared.updateCredential(platform: "youtube", apiKey: youtubeApiKey.trimmingCharacters(in: .whitespacesAndNewlines)) }
                        }
                        .onDisappear {
                            Task { try? await NetworkManager.shared.updateCredential(platform: "youtube", apiKey: youtubeApiKey.trimmingCharacters(in: .whitespacesAndNewlines)) }
                        }
                        .accessibilityIdentifier("settings.youtubeApiKeyField")

                    SecureField("X Bearer Token", text: $twitterBearerToken)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .onSubmit {
                            Task { try? await NetworkManager.shared.updateCredential(platform: "twitter", bearerToken: twitterBearerToken.trimmingCharacters(in: .whitespacesAndNewlines)) }
                        }
                        .onDisappear {
                            Task { try? await NetworkManager.shared.updateCredential(platform: "twitter", bearerToken: twitterBearerToken.trimmingCharacters(in: .whitespacesAndNewlines)) }
                        }
                        .accessibilityIdentifier("settings.twitterBearerTokenField")
                }
                
                // Section: Customizations
                Section(header: Text(i18n.t("appearanceSection"))) {
                    Picker(i18n.t("appTheme"), selection: $theme.mode) {
                        Text(i18n.t("themeLight")).tag(AppThemeMode.light)
                        Text(i18n.t("themeDark")).tag(AppThemeMode.dark)
                        Text(i18n.t("themeSepia")).tag(AppThemeMode.sepia)
                    }
                    .pickerStyle(.segmented)

                    Picker("Style", selection: $theme.style) {
                        ForEach(AppColorStyle.allCases) { style in
                            Text(style.displayName).tag(style)
                        }
                    }
                    .pickerStyle(.segmented)
                    .accessibilityIdentifier("settings.colorStylePicker")
                    
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


                Section(header: Text(i18n.t("privacySection"))) {
                    NavigationLink(destination: PrivacyPolicyView(theme: theme)) {
                        Label("Privacy Policy", systemImage: "hand.raised")
                    }
                    .accessibilityIdentifier("settings.privacyPolicyLink")
                }

                Section(header: Text(i18n.t("dataSection"))) {
                    Button(role: .destructive) {
                        showingClearAllAlert = true
                    } label: {
                        Label("Clear All Data", systemImage: "trash")
                    }
                    .accessibilityIdentifier("settings.clearAllDataButton")
                }
                
            }
            .font(.system(size: 13))
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
                    
                    Picker(i18n.t("collectionMode"), selection: $newCollectionMode) {
                        Text("📄 " + i18n.t("allInfo")).tag("all_info")
                        Text("📹 " + i18n.t("mediaOnly")).tag("media_only")
                    }
                    .pickerStyle(.segmented)
                    
                    HStack(spacing: 10) {
                        Button(i18n.t("cancel")) {
                            newKeyword = ""
                            showingAddKeywordAlert = false
                        }
                        .accessibilityIdentifier("settings.cancelAddKeywordButton")
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(theme.colors.divider)
                        .cornerRadius(10)
                        
                        Button(i18n.t("add")) {
                            let trimmed = newKeyword.trimmingCharacters(in: .whitespacesAndNewlines)
                            guard !trimmed.isEmpty else { return }
                            guard !db.terms.contains(where: { $0.keyword == trimmed }) else {
                                newKeyword = ""
                                showingAddKeywordAlert = false
                                return
                            }
                            let savedTerm = db.saveTerm(keyword: trimmed, collectionMode: newCollectionMode)

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
            .alert("Clear All Data?", isPresented: $showingClearAllAlert) {
                Button(i18n.t("cancel"), role: .cancel) {}
                Button(i18n.t("delete"), role: .destructive) {
                    db.clearAllData()
                }
            } message: {
                Text("This removes keywords, feed items, saved pages, custom URLs, avatars, hidden items, wallpaper, and source order from this device.")
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
                    body: "Oshi Reader stores watch keywords, feed items, saved pages, custom URLs, display settings, wallpaper choices, avatar compositions, and cached article HTML for offline reading — all locally on this device."
                )

                policySection(
                    title: "Data Sent for App Functionality",
                    body: "When you add watch keywords, refresh feeds, search stickers, translate sticker queries, or open articles, related keywords, search text, URLs, and article requests may be sent to the configured Oshi Reader backend, Google services, Irasutoya/Blogger feeds, news feeds, and the websites you choose to open."
                )

                policySection(
                    title: "Tracking and Advertising",
                    body: "This app does not use advertising identifiers, App Tracking Transparency, third-party ad SDKs, or data broker tracking. Data is used solely to provide feed, reader, search, translation, and customization features."
                )

                policySection(
                    title: "Permissions",
                    body: "Notifications: requested when you enable keyword alerts or send a test notification.\n\nPhoto Library: requested when you save an image from the article reader to your Photos.\n\nThis app does not request access to location, contacts, camera, microphone, Bluetooth, health data, or motion sensors."
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
