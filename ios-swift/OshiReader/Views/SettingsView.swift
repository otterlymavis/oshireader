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
    @State private var settingsErrorMessage: String? = nil
    @State private var notificationUpdateIDs = Set<String>()
    @State private var avatarEditorKeyword: String? = nil
    @AppStorage("settings.appearanceSectionExpanded") private var appearanceSectionExpanded = false
    @AppStorage("auto_translate_reader") private var autoTranslateReader = false
    
    var allPlatforms: [(String, String)] {
        Platform.all.map { ($0.id, "\($0.icon) \($0.name)") }
    }
    
    var body: some View {
        NavigationStack {
            Form {
                // Section: Keywords management
                Section(header: Text(i18n.t("watchTerms"))) {
                    ForEach(db.terms) { term in
                        keywordRow(for: term)
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

                    if notifications.authorizationStatus == .notDetermined {
                        Button {
                            Task { _ = await notifications.requestAuthorization() }
                        } label: {
                            Label(i18n.t("enableNotifications"), systemImage: "bell.badge.fill")
                                .foregroundColor(theme.colors.primary)
                        }
                        .accessibilityIdentifier("settings.enableNotificationsButton")
                    } else if notifications.authorizationStatus == .denied {
                        Button {
                            if let url = URL(string: UIApplication.openSettingsURLString) {
                                UIApplication.shared.open(url)
                            }
                        } label: {
                            Label(i18n.t("openIOSSettings"), systemImage: "gear")
                                .foregroundColor(theme.colors.primary)
                        }
                        .accessibilityIdentifier("settings.openSettingsButton")
                    }

                    if let registrationError = notifications.lastRemoteRegistrationError {
                        Text(registrationError)
                            .font(.caption2)
                            .foregroundColor(.orange)
                            .textSelection(.enabled)
                            .accessibilityIdentifier("settings.notificationRegistrationError")
                    }
                }

                Section(header: Text(i18n.t("readerSection"))) {
                    Toggle(i18n.t("autoTranslate"), isOn: $autoTranslateReader)
                        .tint(theme.colors.primary)
                        .accessibilityIdentifier("settings.autoTranslateToggle")
                }

                Section {
                    DisclosureGroup(
                        isExpanded: $appearanceSectionExpanded,
                        content: {
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

                            if db.wallpaper != nil {
                                Button(action: { db.setWallpaper(url: nil) }) {
                                    Text(i18n.t("clearWallpaper"))
                                        .foregroundColor(.red)
                                }
                            }
                        },
                        label: {
                            Label(i18n.t("appearanceSection"), systemImage: "paintbrush")
                                .foregroundColor(theme.colors.text)
                        }
                    )
                }


                Section(header: Text(i18n.t("privacySection"))) {
                    NavigationLink(destination: PrivacyPolicyView(theme: theme)) {
                        Label(i18n.t("privacyPolicy"), systemImage: "hand.raised")
                    }
                    .accessibilityIdentifier("settings.privacyPolicyLink")
                }

                Section(header: Text(i18n.t("dataSection"))) {
                    NavigationLink(destination: SourceStatusView(theme: theme)) {
                        Label(i18n.t("sourceStatus"), systemImage: "antenna.radiowaves.left.and.right")
                    }
                    .accessibilityIdentifier("settings.sourceStatusLink")

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
                                do {
                                    let serverTerm = try await NetworkManager.shared.createWatchTerm(
                                        keyword: savedTerm.keyword,
                                        collectionMode: savedTerm.collection_mode,
                                        sourceMode: savedTerm.source_mode,
                                        selectedPlatforms: savedTerm.selected_platforms,
                                        notifyOnNew: savedTerm.notify_on_new,
                                        isActive: savedTerm.is_active,
                                        aliases: savedTerm.aliases,
                                        forceAPNSRefresh: savedTerm.notify_on_new
                                    )
                                    _ = db.replaceTerm(
                                        localId: savedTerm.id,
                                        with: serverTerm,
                                        ifUnchangedFrom: savedTerm
                                    )
                                } catch {
                                    await MainActor.run {
                                        settingsErrorMessage = i18n.t("settingsSyncFailed")
                                    }
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
            .navigationDestination(item: $avatarEditorKeyword) { keyword in
                AvatarEditorView(keyword: keyword)
            }
        }
    }

    private func keywordRow(for term: WatchTerm) -> some View {
        HStack(alignment: .center, spacing: 12) {
            keywordAvatarButton(for: term)

            VStack(alignment: .leading, spacing: 6) {
                Text(term.keyword)
                    .font(.subheadline)
                    .fontWeight(.semibold)
                    .lineLimit(1)
                    .foregroundColor(theme.colors.text)

                keywordAliasControls(for: term)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: 10) {
                keywordCollectionModeButton(for: term)
                keywordSourceMenu(for: term)
                keywordStatusPill(for: term)
            }
            .fixedSize(horizontal: true, vertical: false)
        }
        .padding(.vertical, 8)
    }

    private func keywordAvatarButton(for term: WatchTerm) -> some View {
        Button {
            avatarEditorKeyword = term.keyword
        } label: {
            ZStack {
                Circle()
                    .fill(theme.colors.divider)
                    .frame(width: 44, height: 44)
                if let avatar = db.oshiAvatars[term.keyword], let url = URL(string: avatar) {
                    AsyncImage(url: url) { image in
                        image
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                    } placeholder: {
                        Text("🎨")
                    }
                    .frame(width: 44, height: 44)
                    .clipShape(Circle())
                } else {
                    Text("🎨")
                        .font(.body)
                }
            }
            .contentShape(Circle())
        }
        .buttonStyle(PlainButtonStyle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Edit avatar")
        .accessibilityIdentifier("settings.keywordAvatar.\(term.keyword)")
    }

    @ViewBuilder
    private func keywordAliasControls(for term: WatchTerm) -> some View {
        if !term.aliases.isEmpty || addingAliasForId == term.id {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 5) {
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

    private func keywordCollectionModeButton(for term: WatchTerm) -> some View {
        Button {
            let next: CollectionMode = term.collection_mode == .allInfo ? .mediaOnly : .allInfo
            db.updateTerm(id: term.id, collectionMode: next)
            Task {
                _ = try? await NetworkManager.shared.updateWatchTerm(id: term.id, collectionMode: next)
            }
        } label: {
            Text(term.collection_mode == .mediaOnly ? "📹" : "📄")
                .font(.caption)
                .frame(width: 34, height: 34)
                .background(theme.colors.divider)
                .foregroundColor(theme.colors.textSub)
                .clipShape(Circle())
        }
        .buttonStyle(PlainButtonStyle())
        .accessibilityIdentifier("settings.keywordMode.\(term.keyword)")
    }

    private func keywordSourceMenu(for term: WatchTerm) -> some View {
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
                .frame(width: 34, height: 34)
                .background(theme.colors.divider)
                .foregroundColor(theme.colors.textSub)
                .clipShape(Circle())
        }
        .accessibilityIdentifier("settings.keywordSources.\(term.keyword)")
    }

    private func keywordStatusPill(for term: WatchTerm) -> some View {
        HStack(spacing: 6) {
            keywordNotificationButton(for: term)

            Toggle("", isOn: Binding(
                get: { term.is_active },
                set: { next in
                    db.updateTerm(id: term.id, isActive: next)
                    Task {
                        _ = try? await NetworkManager.shared.updateWatchTerm(id: term.id, isActive: next)
                    }
                }
            ))
            .labelsHidden()
            .tint(theme.colors.primary)
            .accessibilityIdentifier("settings.keywordToggle.\(term.keyword)")
        }
        .padding(.leading, 5)
        .padding(.trailing, 6)
        .padding(.vertical, 5)
        .background(term.notify_on_new ? theme.colors.primaryBg : theme.colors.divider)
        .overlay {
            Capsule()
                .stroke(term.notify_on_new ? theme.colors.primary.opacity(0.45) : theme.colors.border, lineWidth: 1)
        }
        .clipShape(Capsule())
    }

    private func keywordNotificationButton(for term: WatchTerm) -> some View {
        let isEnabled = term.notify_on_new
        let isUpdating = notificationUpdateIDs.contains(term.id)

        return Button {
            let next = !isEnabled
            updateNotificationPreference(for: term, enabled: next)
        } label: {
            Group {
                if isUpdating {
                    ProgressView()
                        .tint(isEnabled ? theme.colors.primary : theme.colors.textMuted)
                } else {
                    Image(systemName: isEnabled ? "bell.fill" : "bell.slash")
                        .font(.system(size: 20, weight: .semibold))
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(isEnabled ? theme.colors.primary : theme.colors.textMuted)
                }
            }
            .frame(width: 34, height: 34)
            .contentShape(Circle())
            .opacity(isUpdating ? 0.78 : 1)
        }
        .buttonStyle(PlainButtonStyle())
        .disabled(isUpdating)
        .accessibilityIdentifier("settings.keywordBell.\(term.keyword)")
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(isEnabled ? "Notifications on" : "Notifications off")
        .contextMenu {
            if isEnabled {
                Button {
                    triggerNotificationNow(for: term)
                } label: {
                    Label(i18n.t("notifyNow"), systemImage: "bell.and.waves.left.and.right")
                }
                Button(role: .destructive) {
                    clearNotificationNow(for: term)
                } label: {
                    Label(i18n.t("clearNotification"), systemImage: "bell.slash")
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

    private func updateNotificationPreference(for term: WatchTerm, enabled: Bool) {
        guard !notificationUpdateIDs.contains(term.id) else { return }
        notificationUpdateIDs.insert(term.id)

        Task {
            do {
                let updatedTerm = try await NetworkManager.shared.setWatchTermNotificationPreference(
                    for: term,
                    enabled: enabled
                )
                await MainActor.run {
                    notificationUpdateIDs.remove(term.id)
                    _ = db.replaceTerm(localId: term.id, with: updatedTerm, ifUnchangedFrom: term)
                }
            } catch {
                await MainActor.run {
                    notificationUpdateIDs.remove(term.id)
                    settingsErrorMessage = Self.notificationSyncErrorMessage(
                        for: error,
                        enabling: enabled,
                        i18n: i18n
                    )
                }
            }
        }
    }

    private func triggerNotificationNow(for term: WatchTerm) {
        guard !notificationUpdateIDs.contains(term.id) else { return }
        notificationUpdateIDs.insert(term.id)
        Task {
            do {
                try await NetworkManager.shared.triggerWatchTermNotification(id: term.id)
            } catch {
                await MainActor.run {
                    if let apiError = error as? APIClientError, apiError.hasNoPendingNotificationContent {
                        settingsErrorMessage = i18n.t("notifyNowNothingPending")
                    } else {
                        settingsErrorMessage = i18n.t("notificationUpdateFailed")
                    }
                }
            }
            await MainActor.run { _ = notificationUpdateIDs.remove(term.id) }
        }
    }

    private func clearNotificationNow(for term: WatchTerm) {
        guard !notificationUpdateIDs.contains(term.id) else { return }
        notificationUpdateIDs.insert(term.id)
        Task {
            do {
                try await NetworkManager.shared.clearWatchTermNotification(id: term.id)
            } catch {
                await MainActor.run {
                    settingsErrorMessage = i18n.t("notificationUpdateFailed")
                }
            }
            await MainActor.run { _ = notificationUpdateIDs.remove(term.id) }
        }
    }

    static func notificationSyncErrorMessage(
        for error: Error,
        enabling: Bool,
        i18n: I18nManager = .shared
    ) -> String {
        if enabling, let apiError = error as? APIClientError,
           apiError.requiresVerifiedNotificationDevice {
            return i18n.t("notificationEnableFailed")
        }
        return i18n.t("notificationUpdateFailed")
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

}

struct SourceStatusView: View {
    let theme: ThemeManager
    @StateObject private var i18n = I18nManager.shared
    @State private var entries: [SourceHealthEntry] = []
    @State private var isLoading = true

    var body: some View {
        List {
            if isLoading && entries.isEmpty {
                HStack {
                    Spacer()
                    ProgressView()
                    Spacer()
                }
            } else if entries.isEmpty {
                Text(i18n.t("sourceStatusEmpty"))
                    .foregroundColor(theme.colors.textSub)
            } else {
                ForEach(entries) { entry in
                    row(for: entry)
                }
            }
        }
        .accessibilityIdentifier("settings.sourceStatus.screen")
        .background(theme.colors.bg)
        .navigationTitle(i18n.t("sourceStatus"))
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .refreshable { await load() }
    }

    private func row(for entry: SourceHealthEntry) -> some View {
        let platform = Platform.find(Platform.normalize(entry.platform))
        return VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(platform?.name ?? entry.platform)
                    .foregroundColor(theme.colors.text)
                Spacer()
                statusBadge(for: entry)
            }
            if let checkedAt = entry.last_checked_at, let date = parseISO8601Date(checkedAt) {
                Text(relativeTimeString(from: date))
                    .font(.caption)
                    .foregroundColor(theme.colors.textSub)
            }
            if entry.status == "failure", let error = entry.last_error {
                Text(error)
                    .font(.caption)
                    .foregroundColor(theme.colors.textSub)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 2)
        .accessibilityIdentifier("settings.sourceStatus.row.\(entry.platform)")
    }

    private func statusBadge(for entry: SourceHealthEntry) -> some View {
        let (symbol, color): (String, Color) = {
            switch entry.status {
            case "success": return ("checkmark.circle.fill", .green)
            case "empty": return ("circle.dashed", theme.colors.textSub)
            case "filtered": return ("line.diagonal", theme.colors.textSub)
            case "failure": return ("exclamationmark.triangle.fill", .red)
            default: return ("questionmark.circle", theme.colors.textSub)
            }
        }()
        return Image(systemName: symbol).foregroundColor(color)
    }

    private func load() async {
        isLoading = true
        // A failed request returns nil — keep showing whatever was already
        // loaded rather than replacing it with an empty "nothing polled yet"
        // state, which would misrepresent a network failure as fresh data.
        if let fetched = await NetworkManager.shared.fetchSourceHealth() {
            entries = fetched
        }
        isLoading = false
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
                    body: "Notifications: requested when you enable keyword alerts.\n\nPhoto Library: requested when you save an image from the article reader to your Photos.\n\nThis app does not request access to location, contacts, camera, microphone, Bluetooth, health data, or motion sensors."
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
