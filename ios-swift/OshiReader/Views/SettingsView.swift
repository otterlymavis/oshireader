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
    @State private var newCollectionMode = CollectionMode.allInfo
    @State private var newSourceMode = SourceMode.all
    @State private var newSelectedPlatforms: Set<String> = []
    @State private var addingAliasForId: String? = nil
    @State private var newAliasText = ""
    @State private var notificationTestMessage: String? = nil
    @State private var notificationTestSucceeded = false
    @State private var isSendingNotificationTest = false
    @State private var settingsErrorMessage: String? = nil
    @State private var notificationUpdateIDs = Set<String>()
    @AppStorage("auto_translate_reader") private var autoTranslateReader = false
    @AppStorage(BackgroundRefreshLiveTestProbe.resultKey) private var liveBackgroundRefreshResult = ""
    
    var allPlatforms: [(String, String)] {
        Platform.all.map { ($0.id, "\($0.icon) \($0.name)") }
    }
    
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
                                // Alias chips
                                if !term.aliases.isEmpty || addingAliasForId == term.id {
                                    ScrollView(.horizontal, showsIndicators: false) {
                                        HStack(spacing: 4) {
                                            ForEach(term.aliases, id: \.self) { alias in
                                                HStack(spacing: 2) {
                                                    Text(alias)
                                                        .font(.caption2)
                                                        .foregroundColor(theme.colors.textSub)
                                                    Button {
                                                        let updated = term.aliases.filter { $0 != alias }
                                                        db.updateTerm(id: term.id, aliases: updated)
                                                        Task { _ = try? await NetworkManager.shared.updateWatchTerm(id: term.id, aliases: updated) }
                                                    } label: {
                                                        Image(systemName: "xmark")
                                                            .font(.system(size: 8, weight: .bold))
                                                            .foregroundColor(theme.colors.textMuted)
                                                    }
                                                    .buttonStyle(.plain)
                                                }
                                                .padding(.horizontal, 6)
                                                .padding(.vertical, 3)
                                                .background(theme.colors.divider)
                                                .cornerRadius(99)
                                            }
                                            if addingAliasForId == term.id {
                                                TextField(i18n.t("keyword"), text: $newAliasText)
                                                    .font(.caption2)
                                                    .frame(width: 80)
                                                    .autocorrectionDisabled()
                                                    .textInputAutocapitalization(.never)
                                                    .submitLabel(.done)
                                                    .onSubmit {
                                                        let trimmed = newAliasText.trimmingCharacters(in: .whitespacesAndNewlines)
                                                        if !trimmed.isEmpty && !term.aliases.contains(trimmed) {
                                                            let updated = term.aliases + [trimmed]
                                                            db.updateTerm(id: term.id, aliases: updated)
                                                            Task { _ = try? await NetworkManager.shared.updateWatchTerm(id: term.id, aliases: updated) }
                                                        }
                                                        newAliasText = ""
                                                        addingAliasForId = nil
                                                    }
                                            }
                                            Button {
                                                newAliasText = ""
                                                addingAliasForId = addingAliasForId == term.id ? nil : term.id
                                            } label: {
                                                Image(systemName: addingAliasForId == term.id ? "checkmark" : "plus")
                                                    .font(.system(size: 9, weight: .bold))
                                                    .foregroundColor(theme.colors.primary)
                                                    .frame(width: 20, height: 18)
                                                    .background(theme.colors.primaryBg)
                                                    .cornerRadius(99)
                                            }
                                            .buttonStyle(.plain)
                                        }
                                    }
                                } else {
                                    Button {
                                        newAliasText = ""
                                        addingAliasForId = term.id
                                    } label: {
                                        Label(i18n.t("addAlias"), systemImage: "plus")
                                            .font(.caption2)
                                            .foregroundColor(theme.colors.textMuted)
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                            Spacer()

                            Button {
                                let next: CollectionMode = term.collection_mode == .allInfo ? .mediaOnly : .allInfo
                                db.updateTerm(id: term.id, collectionMode: next)
                                Task {
                                    _ = try? await NetworkManager.shared.updateWatchTerm(id: term.id, collectionMode: next)
                                }
                            } label: {
                                Text(term.collection_mode == .mediaOnly ? "📹" : "📄")
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

                            Menu {
                                Button {
                                    updateSourceSelection(for: term, mode: .all, platforms: [])
                                } label: {
                                    Label("All sources", systemImage: term.source_mode == .all ? "checkmark" : "globe")
                                }
                                ForEach(allPlatforms, id: \.0) { key, label in
                                    Button {
                                        var selected = term.source_mode == .selected ? Set(term.selected_platforms) : []
                                        if term.source_mode == .all {
                                            selected = [key]
                                        } else if selected.contains(key) {
                                            selected.remove(key)
                                        } else {
                                            selected.insert(key)
                                        }
                                        updateSourceSelection(for: term, mode: .selected, platforms: Array(selected).sorted())
                                    } label: {
                                        Label(label, systemImage: term.source_mode == .selected && term.selected_platforms.contains(key) ? "checkmark.square" : "square")
                                    }
                                }
                            } label: {
                                Text(term.source_mode == .all ? "🌐" : "🔎")
                                    .font(.caption)
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 5)
                                    .background(theme.colors.divider)
                                    .clipShape(Capsule())
                            }
                            .accessibilityIdentifier("settings.keywordSources.\(term.keyword)")
                            
                            // Push notifications bell button
                            Button {
                                let next = !term.notify_on_new
                                updateNotificationPreference(for: term, enabled: next)
                            } label: {
                                Image(systemName: term.notify_on_new ? "bell.fill" : "bell.slash")
                                    .foregroundColor(term.notify_on_new ? theme.colors.primary : theme.colors.textMuted)
                                    .font(.body)
                                    .padding(.horizontal, 8)
                            }
                            .buttonStyle(PlainButtonStyle())
                            .disabled(notificationUpdateIDs.contains(term.id))
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
                        let termsToDelete = offsets.compactMap { index in
                            db.terms.indices.contains(index) ? db.terms[index] : nil
                        }
                        for term in termsToDelete {
                            deleteTermFromSettings(term)
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
                Section {
                    Menu {
                        ForEach(allPlatforms, id: \.0) { key, label in
                            Toggle(
                                label,
                                isOn: Binding(
                                    get: { db.subscribedPlatforms.contains(key) },
                                    set: { isSubscribed in
                                        setPlatformSubscription(key, isSubscribed: isSubscribed)
                                    }
                                )
                            )
                            .accessibilityIdentifier("settings.platformToggle.\(key)")
                        }
                    } label: {
                        HStack {
                            Label(i18n.t("platformSettings"), systemImage: "dot.radiowaves.left.and.right")
                                .foregroundColor(theme.colors.text)
                            Spacer()
                            Text("\(db.subscribedPlatforms.count)/\(allPlatforms.count)")
                                .font(.subheadline)
                                .foregroundColor(theme.colors.textMuted)
                            Image(systemName: "chevron.up.chevron.down")
                                .font(.caption2)
                                .foregroundColor(theme.colors.textMuted)
                        }
                    }
                    .accessibilityIdentifier("settings.platformMenu")
                }

                Section(header: Text(i18n.t("notificationsSection"))) {
                    HStack {
                        Label(i18n.t("pushNotifications"), systemImage: "bell.badge")
                        Spacer()
                        Text(notifications.statusText)
                            .foregroundColor(notifications.canScheduleNotifications ? theme.colors.primary : theme.colors.textMuted)
                    }
                    .accessibilityIdentifier("settings.notificationStatus")

                    Label(i18n.t("notificationSetupHint"), systemImage: "info.circle")
                        .font(.caption)
                        .foregroundColor(theme.colors.textMuted)
                        .accessibilityIdentifier("settings.notificationSetupHint")

                    switch notifications.authorizationStatus {
                    case .notDetermined:
                        Button {
                            Task { _ = await notifications.requestAuthorization() }
                        } label: {
                            Label(i18n.t("enableNotifications"), systemImage: "bell.badge.fill")
                                .foregroundColor(theme.colors.primary)
                        }
                        .accessibilityIdentifier("settings.enableNotificationsButton")
                    case .denied:
                        Button {
                            if let url = URL(string: UIApplication.openSettingsURLString) {
                                UIApplication.shared.open(url)
                            }
                        } label: {
                            Label(i18n.t("openIOSSettings"), systemImage: "gear")
                                .foregroundColor(theme.colors.primary)
                        }
                        .accessibilityIdentifier("settings.openSettingsButton")
                    default:
                        Button {
                            Task { await sendTestNotification() }
                        } label: {
                            Label(
                                isSendingNotificationTest ? i18n.t("notifRemoteChecking") : i18n.t("sendTestNotification"),
                                systemImage: isSendingNotificationTest ? "antenna.radiowaves.left.and.right" : "paperplane.fill"
                            )
                                .foregroundColor(theme.colors.primary)
                        }
                        .disabled(isSendingNotificationTest)
                        .accessibilityIdentifier("settings.testNotificationButton")
                    }

                    if let notificationTestMessage {
                        Label(
                            notificationTestMessage,
                            systemImage: notificationTestSucceeded ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
                        )
                        .font(.caption)
                        .foregroundColor(notificationTestSucceeded ? theme.colors.primary : .orange)
                        .accessibilityIdentifier("settings.notificationTestResult")
                    }

                    if let registrationError = notifications.lastRemoteRegistrationError {
                        Text(registrationError)
                            .font(.caption2)
                            .foregroundColor(.orange)
                            .textSelection(.enabled)
                            .accessibilityIdentifier("settings.notificationRegistrationError")
                    }

                    if NetworkManager.shared.isLiveBackgroundPushTesting,
                       !liveBackgroundRefreshResult.isEmpty {
                        Text(liveBackgroundRefreshResult)
                            .font(.caption)
                            .accessibilityIdentifier("settings.liveBackgroundRefreshResult")
                    }
                }

                Section(header: Text(i18n.t("readerSection"))) {
                    Toggle(i18n.t("autoTranslate"), isOn: $autoTranslateReader)
                        .tint(theme.colors.primary)
                        .accessibilityIdentifier("settings.autoTranslateToggle")
                }

                // Section: Customizations
                Section(header: Text(i18n.t("appearanceSection"))) {
                    Picker(i18n.t("appTheme"), selection: $theme.mode) {
                        Text(i18n.t("themeLight")).tag(AppThemeMode.light)
                        Text(i18n.t("themeDark")).tag(AppThemeMode.dark)
                        Text(i18n.t("themeSepia")).tag(AppThemeMode.sepia)
                    }
                    .pickerStyle(.segmented)

                    Picker(i18n.t("themeStyle"), selection: $theme.style) {
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

                    Picker(i18n.t("font"), selection: $appearance.fontChoice) {
                        ForEach(AppFontChoice.allCases) { choice in
                            Text(choice.displayName).tag(choice)
                        }
                    }
                    .pickerStyle(.segmented)
                    .accessibilityIdentifier("settings.fontPicker")

                    Picker(i18n.t("fontSize"), selection: $appearance.fontSizeChoice) {
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
                        Label(i18n.t("privacyPolicy"), systemImage: "hand.raised")
                    }
                    .accessibilityIdentifier("settings.privacyPolicyLink")
                }

                Section(header: Text(i18n.t("dataSection"))) {
                    Button(role: .destructive) {
                        showingClearAllAlert = true
                    } label: {
                        Label(i18n.t("clearAllData"), systemImage: "trash")
                    }
                    .accessibilityIdentifier("settings.clearAllDataButton")
                }
                
            }
            .font(appearance.font(size: 13))
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
                        Text("📄 " + i18n.t("allInfo")).tag(CollectionMode.allInfo)
                        Text("📹 " + i18n.t("mediaOnly")).tag(CollectionMode.mediaOnly)
                    }
                    .pickerStyle(.segmented)

                    Picker("Sources", selection: $newSourceMode) {
                        Text("All sources").tag(SourceMode.all)
                        Text("Selected").tag(SourceMode.selected)
                    }
                    .pickerStyle(.segmented)
                    if newSourceMode == .selected {
                        ScrollView {
                            LazyVGrid(columns: [GridItem(.adaptive(minimum: 125), spacing: 8)], spacing: 8) {
                                ForEach(allPlatforms, id: \.0) { key, label in
                                    Button {
                                        if newSelectedPlatforms.contains(key) {
                                            newSelectedPlatforms.remove(key)
                                        } else {
                                            newSelectedPlatforms.insert(key)
                                        }
                                    } label: {
                                        HStack {
                                            Image(systemName: newSelectedPlatforms.contains(key) ? "checkmark.square.fill" : "square")
                                            Text(label).lineLimit(1)
                                        }
                                        .font(.caption)
                                        .foregroundColor(theme.colors.text)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                    }
                                }
                            }
                        }
                        .frame(maxHeight: 90)
                    }
                    
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
                            let savedTerm = db.saveTerm(keyword: trimmed, collectionMode: newCollectionMode, sourceMode: newSourceMode, selectedPlatforms: Array(newSelectedPlatforms).sorted())

                            Task {
                                await refreshLocalFallbacks(for: savedTerm)
                            }
                            Task {
                                if let serverTerm = try? await NetworkManager.shared.createWatchTerm(
                                    keyword: savedTerm.keyword,
                                    collectionMode: savedTerm.collection_mode,
                                    sourceMode: savedTerm.source_mode,
                                    selectedPlatforms: savedTerm.selected_platforms,
                                    notifyOnNew: savedTerm.notify_on_new,
                                    isActive: savedTerm.is_active,
                                    aliases: savedTerm.aliases
                                ) {
                                    _ = db.replaceTerm(
                                        localId: savedTerm.id,
                                        with: serverTerm,
                                        ifUnchangedFrom: savedTerm
                                    )
                                }
                            }
                            
                            newKeyword = ""
                            newSourceMode = .all
                            newSelectedPlatforms = []
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
            .alert(i18n.t("clearAllDataAlert"), isPresented: $showingClearAllAlert) {
                Button(i18n.t("cancel"), role: .cancel) {}
                Button(i18n.t("delete"), role: .destructive) {
                    db.clearAllData()
                }
            } message: {
                Text(i18n.t("clearAllDataMessage"))
            }
            .alert(
                settingsErrorMessage ?? "",
                isPresented: Binding(
                    get: { settingsErrorMessage != nil },
                    set: { if !$0 { settingsErrorMessage = nil } }
                )
            ) {
                Button(i18n.t("ok"), role: .cancel) {
                    settingsErrorMessage = nil
                }
            }
        }
    }

    private func updateSourceSelection(for term: WatchTerm, mode: SourceMode, platforms: [String]) {
        guard mode != .selected || !platforms.isEmpty else {
            return
        }
        db.updateTerm(id: term.id, sourceMode: mode, selectedPlatforms: platforms)
        Task {
            _ = try? await NetworkManager.shared.updateWatchTerm(
                id: term.id,
                sourceMode: mode,
                selectedPlatforms: platforms
            )
        }
    }

    private func refreshLocalFallbacks(for term: WatchTerm) async {
        guard !NetworkManager.shared.isUITesting,
              let currentTerm = currentDeviceFallbackTerm(for: term) else { return }
        let fallbackPlatforms = BackgroundRefreshPolicy.deviceFallbackPlatforms(
            for: currentTerm,
            subscribedPlatforms: db.subscribedPlatforms
        )
        guard !fallbackPlatforms.isEmpty else { return }
        let searchTerms = [currentTerm.keyword] + currentTerm.aliases
        var mergedAnyItems = false

        await withTaskGroup(of: (searchTerm: String, items: [FeedItem]).self) { group in
            for searchTerm in searchTerms {
                group.addTask {
                    let items = await NetworkManager.shared.scrapeLocalFallbacks(
                        keyword: searchTerm,
                        tagKeyword: currentTerm.keyword,
                        platformIds: fallbackPlatforms
                    )
                    return (searchTerm, items)
                }
            }
            var scrapeAttempts = [[FeedItem]]()
            for await scrape in group {
                guard let latestTerm = currentDeviceFallbackTerm(for: currentTerm) else {
                    group.cancelAll()
                    break
                }
                guard scrape.searchTerm == latestTerm.keyword ||
                        latestTerm.aliases.contains(scrape.searchTerm) else {
                    continue
                }
                let currentFallbackPlatforms = BackgroundRefreshPolicy.deviceFallbackPlatforms(
                    for: latestTerm,
                    subscribedPlatforms: db.subscribedPlatforms
                )
                let mergeableItems = scrape.items.filter { item in
                    item.watch_term_keyword == latestTerm.keyword &&
                        currentFallbackPlatforms.contains(Platform.normalize(item.platform))
                }
                if !mergeableItems.isEmpty {
                    scrapeAttempts.append(mergeableItems)
                }
            }
            if !scrapeAttempts.isEmpty {
                _ = db.mergeItemsBatched(
                    newItemsBatches: scrapeAttempts,
                    notifyOnNew: false
                )
                mergedAnyItems = true
            }
        }
        if mergedAnyItems {
            db.flushPendingFeedItemsSave()
        }
    }

    private func currentDeviceFallbackTerm(for term: WatchTerm) -> WatchTerm? {
        guard let currentTerm = db.term(matchingKeyword: term.keyword),
              !BackgroundRefreshPolicy.deviceFallbackPlatforms(
                for: currentTerm,
                subscribedPlatforms: db.subscribedPlatforms
              ).isEmpty else {
            return nil
        }
        return currentTerm
    }

    private func deleteTermFromSettings(_ term: WatchTerm) {
        if addingAliasForId == term.id { addingAliasForId = nil }
        db.markTermDeleteConfirmed(term)
        db.deleteTerm(id: term.id)
        Task {
            do {
                try await NetworkManager.shared.deleteWatchTermIfSynced(term)
            } catch {
                AppLogger.network.warning("deleteWatchTerm(\(term.id)) failed after local Settings delete: \(error.localizedDescription)")
            }
        }
    }

    private func sendTestNotification() async {
        guard !isSendingNotificationTest else { return }
        isSendingNotificationTest = true
        defer { isSendingNotificationTest = false }

        notificationTestMessage = i18n.t("notifRemoteChecking")
        notificationTestSucceeded = false

        if !notifications.canScheduleNotifications {
            _ = await notifications.requestAuthorization()
        }

        if !NetworkManager.shared.isLiveBackgroundPushTesting {
            do {
                try await notifications.sendTestNotification()
                notificationTestSucceeded = true
            } catch {
                AppLogger.notifications.warning("Local notification test failed: \(error.localizedDescription)")
                notificationTestMessage = i18n.t("notifLocalTestFailed")
                return
            }
        }

        var remoteRegistrationReady = await notifications.ensureRemoteNotificationsRegisteredIfAllowed(timeout: 4)
        if !remoteRegistrationReady {
            // A freshly installed build can receive the APNs callback just after
            // the first wait expires. Retry once, but do not block the UI for
            // too long: the backend can also test a device-scoped stored token.
            remoteRegistrationReady = await notifications.ensureRemoteNotificationsRegisteredIfAllowed(timeout: 8)
        }

        do {
            let report = try await NetworkManager.shared.sendRemoteTestPush()
            let result = notificationTestResult(for: report)
            notificationTestMessage = result.message
            notificationTestSucceeded = result.succeeded
            if result.succeeded {
                return
            }
        } catch {
            if case APIClientError.httpStatus(404, _) = error {
                if remoteRegistrationReady, await retryRemoteTestAfterTokenRefresh() {
                    return
                }
                notificationTestMessage = i18n.t("notifRemoteNoDevices")
                return
            }
            AppLogger.notifications.warning("Remote APNs test failed: \(error.localizedDescription)")
            if NetworkManager.shared.isLiveBackgroundPushTesting {
                notificationTestMessage = "live remote error: \(remoteTestDiagnostic(error))"
                return
            }
            notificationTestMessage = remoteRegistrationReady
                ? i18n.t("notifRemoteTestRequestFailed")
                : i18n.t("notifRemoteTokenMissing")
        }
    }

    private func updateNotificationPreference(for term: WatchTerm, enabled: Bool) {
        guard !notificationUpdateIDs.contains(term.id) else { return }
        notificationUpdateIDs.insert(term.id)

        Task {
            do {
                let updatedTerm = try await NetworkManager.shared.updateWatchTerm(
                    id: term.id,
                    notifyOnNew: enabled
                )
                await MainActor.run {
                    notificationUpdateIDs.remove(term.id)
                    _ = db.replaceTerm(localId: term.id, with: updatedTerm, ifUnchangedFrom: term)
                }
            } catch {
                await MainActor.run {
                    notificationUpdateIDs.remove(term.id)
                    if enabled, let apiError = error as? APIClientError,
                       apiError.requiresVerifiedNotificationDevice {
                        settingsErrorMessage = i18n.t("notificationEnableFailed")
                    } else {
                        settingsErrorMessage = i18n.t("notificationUpdateFailed")
                    }
                }
            }
        }
    }

    private func remoteTestDiagnostic(_ error: Error) -> String {
        if let apiError = error as? APIClientError {
            return apiError.localizedDescription
        }
        return String(describing: error)
    }

    private func retryRemoteTestAfterTokenRefresh() async -> Bool {
        notifications.resetRemoteNotificationRegistrationCache()
        guard await notifications.ensureRemoteNotificationsRegisteredIfAllowed() else {
            return false
        }
        do {
            let report = try await NetworkManager.shared.sendRemoteTestPush()
            let result = notificationTestResult(for: report)
            notificationTestMessage = result.message
            notificationTestSucceeded = result.succeeded
            return result.succeeded
        } catch {
            AppLogger.notifications.warning("Remote APNs retry failed: \(error.localizedDescription)")
            return false
        }
    }

    private func setPlatformSubscription(_ key: String, isSubscribed: Bool) {
        var platforms = db.subscribedPlatforms
        if isSubscribed {
            if !platforms.contains(key) {
                platforms.append(key)
            }
        } else {
            platforms.removeAll(where: { $0 == key })
        }
        db.setSubscribedPlatforms(platforms: platforms)
    }

    private func notificationTestResult(for report: APNSTestPushReport) -> (succeeded: Bool, message: String) {
        guard report.configured else {
            return (false, i18n.t("notifRemoteNotConfigured"))
        }
        guard !report.results.isEmpty else {
            return (false, i18n.t("notifRemoteNoDevices"))
        }
        if report.results.contains(where: { $0.status == 200 || $0.status == 201 || $0.status == 202 }) {
            return (true, i18n.t("notifRemoteTestSent"))
        }

        let firstFailure = report.results.first { ($0.status ?? 200) >= 300 || $0.reason != nil || $0.error != nil }
        if let reason = firstFailure?.reason, !reason.isEmpty {
            if reason == "BadEnvironmentKeyInToken" {
                return (false, i18n.t("notifRemoteEnvironmentKeyMismatch"))
            }
            return (false, i18n.tFormat("notifRemoteRejectedFmt", reason))
        }
        if let error = firstFailure?.error, !error.isEmpty {
            return (false, i18n.tFormat("notifRemoteErrorFmt", error))
        }
        if let status = firstFailure?.status {
            return (false, i18n.tFormat("notifRemoteStatusFmt", status))
        }
        return (false, i18n.t("notifRemoteUnknownFailure"))
    }
}

struct PrivacyPolicyView: View {
    let theme: ThemeManager
    @StateObject private var i18n = I18nManager.shared

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
        .navigationTitle(i18n.t("privacyPolicy"))
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
