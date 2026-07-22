import Photos
import SafariServices
import SwiftUI
import UIKit
import WebKit

struct ReaderImageAction: Identifiable {
    let id = UUID()
    let url: URL
    let alt: String?
}

enum ReaderWebLoadState {
    case loading
    case loaded
    case failed
}

struct ReaderView: View {
    let feedItem: FeedItem

    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    @StateObject private var appearance = AppearanceManager.shared

    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    @State private var readerMode: Bool
    @State private var readerTheme: AppThemeMode = .light
    @State private var fontSize: CGFloat = 16.0
    @State private var isTranslated = false
    @State private var imageAction: ReaderImageAction?
    @State private var saveImageStatus = ""
    @State private var showingSaveImageStatus = false
    @State private var saveAllImagesCounter = 0
    @State private var isSavingAllImages = false
    @State private var webLoadState: ReaderWebLoadState = .loading
    @State private var showSignInBanner = false
    @State private var isSigningIntoX = false
    @State private var showOpenInBrowserBanner = false

    init(feedItem: FeedItem) {
        self.feedItem = feedItem
        _readerMode = State(initialValue: Self.initialReaderMode(for: feedItem))
        _isTranslated = State(initialValue: UserDefaults.standard.bool(forKey: "auto_translate_reader") && !Self.usesSystemSafari(for: feedItem))
    }

    static func initialReaderMode(for feedItem: FeedItem) -> Bool {
        if usesSystemSafari(for: feedItem) {
            return false
        }
        return !feedItem.id.hasPrefix("search:")
    }

    static func usesSystemSafari(for feedItem: FeedItem) -> Bool {
        feedItem.platform == "5ch"
    }

    var originalPageUrl: URL? {
        guard let normalized = normalizedReaderUrl(feedItem.url, platform: feedItem.platform) else { return nil }
        return URL(string: normalized)
    }

    var targetUrl: URL? {
        if isSigningIntoX {
            return URL(string: "https://x.com/login")
        }
        guard let originalUrl = originalPageUrl else { return nil }
        if isTranslated, !Self.usesSystemSafari(for: feedItem) {
            let targetLang: String
            switch i18n.lang {
            case "ja": targetLang = "ja"
            case "en": targetLang = "en"
            case "zh-CN": targetLang = "zh-CN"
            case "zh-TW": targetLang = "zh-TW"
            default: targetLang = "en"
            }
            if let escapedUrl = originalUrl.absoluteString.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
               let transUrl = URL(string: "https://translate.google.com/translate?sl=auto&tl=\(targetLang)&u=\(escapedUrl)") {
                return transUrl
            }
        }
        return originalUrl
    }

    var isSaved: Bool {
        db.savedPages.contains(where: { $0.id == feedItem.id })
    }

    var body: some View {
        VStack(spacing: 0) {
            if let url = targetUrl {
                ZStack {
                    if Self.usesSystemSafari(for: feedItem) {
                        SafariReaderView(url: url)
                            .background(bgColor)
                            .onAppear {
                                webLoadState = .loaded
                            }
                    } else {
                        WebViewHelper(
                            url: url,
                            cacheId: feedItem.id,
                            platform: feedItem.platform,
                            themeMode: readerTheme,
                            fontSize: fontSize,
                            fontFamilyCSS: appearance.readerFontFamilyCSS,
                            readerMode: readerMode,
                            saveAllImagesCounter: saveAllImagesCounter,
                            onLoadStateChange: { state in
                                webLoadState = state
                                if state == .loading {
                                    showSignInBanner = false
                                    showOpenInBrowserBanner = false
                                }
                            },
                            onImageAction: { imageAction = $0 },
                            onSaveAllImages: { urls in saveAllImages(urls) },
                            onContentBlocked: {
                                if feedItem.platform == "twitter" {
                                    if !isSigningIntoX { showSignInBanner = true }
                                } else {
                                    showOpenInBrowserBanner = true
                                }
                            }
                        )
                        .background(bgColor)
                    }

                    if !Self.usesSystemSafari(for: feedItem), webLoadState != .loaded {
                        readerLoadStateOverlay
                            .allowsHitTesting(webLoadState == .failed)
                    }

                    VStack {
                        if isSigningIntoX {
                            signInReturnBanner
                        } else if showSignInBanner {
                            signInPromptBanner
                        } else if showOpenInBrowserBanner {
                            openInBrowserBanner
                        }
                        Spacer()
                    }
                }
            } else {
                Text(i18n.t("invalidUrl"))
                    .foregroundColor(theme.colors.textMuted)
            }

            if !Self.usesSystemSafari(for: feedItem) {
                readerControlBar
            }
        }
        .background(bgColor)
        .navigationTitle(feedItem.title ?? i18n.t("readerTitle"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                if !Self.usesSystemSafari(for: feedItem) {
                    Button {
                        isTranslated.toggle()
                    } label: {
                        Image(systemName: "translate")
                            .foregroundColor(isTranslated ? theme.colors.primary : theme.colors.textMuted)
                    }
                    .accessibilityIdentifier("reader.translateButton")
                }
            }

            ToolbarItem(placement: .navigationBarTrailing) {
                Button {
                    _ = db.toggleSaved(item: feedItem)
                } label: {
                    Image(systemName: isSaved ? "bookmark.fill" : "bookmark")
                        .foregroundColor(theme.colors.primary)
                }
                .accessibilityIdentifier("reader.bookmarkButton")
            }

            ToolbarItem(placement: .navigationBarTrailing) {
                if !Self.usesSystemSafari(for: feedItem) {
                    if isSavingAllImages {
                        ProgressView().tint(theme.colors.primary)
                    } else {
                        Button {
                            isSavingAllImages = true
                            saveAllImagesCounter += 1
                        } label: {
                            Image(systemName: "photo.on.rectangle.angled")
                                .foregroundColor(theme.colors.primary)
                        }
                        .accessibilityIdentifier("reader.saveAllImagesButton")
                    }
                }
            }

            ToolbarItem(placement: .navigationBarTrailing) {
                if let url = targetUrl {
                    ShareLink(item: url) {
                        Image(systemName: "square.and.arrow.up")
                            .foregroundColor(theme.colors.primary)
                    }
                    .accessibilityIdentifier("reader.shareButton")
                }
            }
        }
        .confirmationDialog(i18n.t("imageActions"), isPresented: Binding(
            get: { imageAction != nil },
            set: { isPresented in
                if !isPresented {
                    imageAction = nil
                }
            }
        )) {
            if let action = imageAction {
                ShareLink(item: action.url) {
                    Label(i18n.t("shareImage"), systemImage: "square.and.arrow.up")
                }
                Button(i18n.t("saveImage")) {
                    saveImage(action.url)
                }
                Button(i18n.t("openImage")) {
                    UIApplication.shared.open(action.url)
                }
            }
        }
        .alert(i18n.t("imageActions"), isPresented: $showingSaveImageStatus) {
            Button(i18n.t("ok"), role: .cancel) {}
        } message: {
            Text(saveImageStatus)
        }
        .onAppear {
            readerTheme = theme.mode
            fontSize = appearance.readerFontSize
            if UserDefaults.standard.bool(forKey: "auto_translate_reader"), !Self.usesSystemSafari(for: feedItem) {
                isTranslated = true
            }
        }
        .onChange(of: appearance.fontSizeChoice) {
            fontSize = appearance.readerFontSize
        }
    }

    private var readerControlBar: some View {
        Group {
            if horizontalSizeClass == .compact {
                compactReaderControlBar
            } else {
                regularReaderControlBar
            }
        }
        .background(theme.colors.card)
        .overlay(Rectangle().frame(height: 0.5).foregroundColor(theme.colors.divider), alignment: .top)
    }

    private var signInPromptBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "lock.fill")
                .foregroundColor(theme.colors.textSub)
            Text(i18n.t("readerSignInRequired"))
                .font(.caption)
                .foregroundColor(theme.colors.textSub)
                .lineLimit(2)
            Spacer(minLength: 8)
            Button(i18n.t("readerSignInButton")) {
                showSignInBanner = false
                isSigningIntoX = true
            }
            .font(.caption)
            .fontWeight(.semibold)
            .foregroundColor(theme.colors.primary)
            .accessibilityIdentifier("reader.signInButton")
            Button {
                showSignInBanner = false
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(theme.colors.textMuted)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(theme.colors.card)
        .cornerRadius(10)
        .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
        .padding(10)
    }

    private var signInReturnBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "arrow.uturn.backward.circle.fill")
                .foregroundColor(theme.colors.textSub)
            Text(i18n.t("readerSignInReturnMessage"))
                .font(.caption)
                .foregroundColor(theme.colors.textSub)
                .lineLimit(2)
            Spacer(minLength: 8)
            Button(i18n.t("readerSignInReturnButton")) {
                isSigningIntoX = false
            }
            .font(.caption)
            .fontWeight(.semibold)
            .foregroundColor(theme.colors.primary)
            .accessibilityIdentifier("reader.signInReturnButton")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(theme.colors.card)
        .cornerRadius(10)
        .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
        .padding(10)
    }

    private var openInBrowserBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle")
                .foregroundColor(theme.colors.textSub)
            Text(i18n.t("readerCouldNotDisplay"))
                .font(.caption)
                .foregroundColor(theme.colors.textSub)
                .lineLimit(2)
            Spacer(minLength: 8)
            Button(i18n.t("readerOpenInBrowser")) {
                showOpenInBrowserBanner = false
                openInExternalBrowser()
            }
            .font(.caption)
            .fontWeight(.semibold)
            .foregroundColor(theme.colors.primary)
            .accessibilityIdentifier("reader.openInBrowserButton")
            Button {
                showOpenInBrowserBanner = false
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(theme.colors.textMuted)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(theme.colors.card)
        .cornerRadius(10)
        .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
        .padding(10)
    }

    private func openInExternalBrowser() {
        guard let url = targetUrl else { return }
        UIApplication.shared.open(url)
    }

    private var readerLoadStateOverlay: some View {
        VStack(spacing: 12) {
            if webLoadState == .loading {
                ProgressView()
                    .tint(theme.colors.primary)
                Text(i18n.t("readerLoadingPage"))
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(theme.colors.textSub)
            } else {
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundColor(theme.colors.textMuted)
                Text(i18n.t("readerLoadFailed"))
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(theme.colors.textSub)
                    .multilineTextAlignment(.center)
                Button(i18n.t("readerOpenInBrowser")) {
                    openInExternalBrowser()
                }
                .font(.system(size: 13, weight: .semibold))
                .padding(.horizontal, 14)
                .padding(.vertical, 7)
                .background(theme.colors.primary)
                .foregroundColor(.white)
                .cornerRadius(8)
                .accessibilityIdentifier("reader.failedOpenInBrowserButton")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(webLoadState == .loading ? bgColor.opacity(0.82) : bgColor)
        .accessibilityIdentifier(webLoadState == .loading ? "reader.loadingState" : "reader.failedState")
    }

    private var regularReaderControlBar: some View {
        HStack(spacing: 12) {
            readerModeButton(showTitle: true)
            Spacer()
            fontSizeControls
            Spacer()
            themePicker(width: 112)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    private var compactReaderControlBar: some View {
        HStack(spacing: 8) {
            readerModeButton(showTitle: false)
            Spacer(minLength: 4)
            fontSizeControls
                .layoutPriority(1)
            Spacer(minLength: 4)
            themePicker(width: 104)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }

    private func readerModeButton(showTitle: Bool) -> some View {
        Button(action: { readerMode.toggle() }) {
            if showTitle {
                Label(readerMode ? i18n.t("readerModeText") : i18n.t("readerModeWeb"),
                      systemImage: readerMode ? "doc.plaintext" : "globe")
            } else {
                Image(systemName: readerMode ? "doc.plaintext" : "globe")
            }
        }
        .font(.caption)
        .fontWeight(.bold)
        .frame(minWidth: showTitle ? nil : 38, minHeight: 34)
        .padding(.horizontal, showTitle ? 10 : 0)
        .padding(.vertical, showTitle ? 6 : 0)
        .background(theme.colors.divider)
        .foregroundColor(theme.colors.primary)
        .cornerRadius(8)
        .accessibilityLabel(readerMode ? i18n.t("readerModeText") : i18n.t("readerModeWeb"))
        .accessibilityValue(readerMode ? "reader" : "web")
        .accessibilityIdentifier("reader.modeToggleButton")
    }

    private var fontSizeControls: some View {
        HStack(spacing: 10) {
            Button(action: { fontSize = max(12.0, fontSize - 2.0) }) {
                Text("A-")
                    .font(.subheadline)
                    .foregroundColor(theme.colors.textSub)
            }

            Text("\(Int(fontSize))")
                .font(.caption)
                .foregroundColor(theme.colors.textMuted)
                .frame(width: 22)

            Button(action: { fontSize = min(28.0, fontSize + 2.0) }) {
                Text("A+")
                    .font(.subheadline)
                    .foregroundColor(theme.colors.textSub)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(theme.colors.divider)
        .cornerRadius(8)
        .opacity(readerMode ? 1 : 0.45)
        .disabled(!readerMode)
    }

    private func themePicker(width: CGFloat) -> some View {
        Picker("Theme", selection: $readerTheme) {
            Image(systemName: "sun.max.fill").tag(AppThemeMode.light)
            Image(systemName: "moon.fill").tag(AppThemeMode.dark)
            Image(systemName: "doc.text.magnifyingglass").tag(AppThemeMode.sepia)
        }
        .pickerStyle(.segmented)
        .frame(width: width)
    }

    private var bgColor: Color {
        switch readerTheme {
        case .light: return Color.white
        case .dark: return Color(red: 0.08, green: 0.08, blue: 0.1)
        case .sepia: return Color(red: 0.96, green: 0.93, blue: 0.86)
        }
    }

    private func saveImage(_ url: URL) {
        Task {
            do {
                let (data, response) = try await URLSession.shared.data(for: imageRequest(for: url))
                if let httpResponse = response as? HTTPURLResponse,
                   !(200...299).contains(httpResponse.statusCode) {
                    throw URLError(.badServerResponse)
                }
                guard let image = UIImage(data: data) else {
                    await MainActor.run {
                        saveImageStatus = i18n.t("imageLoadError")
                        showingSaveImageStatus = true
                    }
                    return
                }
                let auth = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
                guard auth == .authorized || auth == .limited else {
                    await MainActor.run {
                        saveImageStatus = i18n.t("photosAccessRequired")
                        showingSaveImageStatus = true
                    }
                    return
                }
                try await PHPhotoLibrary.shared().performChanges {
                    PHAssetChangeRequest.creationRequestForAsset(from: image)
                }
                await MainActor.run {
                    saveImageStatus = i18n.t("imageSavedToPhotos")
                    showingSaveImageStatus = true
                }
            } catch {
                await MainActor.run {
                    saveImageStatus = i18n.t("imageSaveError")
                    showingSaveImageStatus = true
                }
            }
        }
    }

    private func saveAllImages(_ urls: [URL]) {
        guard !urls.isEmpty else {
            isSavingAllImages = false
            saveImageStatus = i18n.t("imageNoLargeImages")
            showingSaveImageStatus = true
            return
        }
        Task {
            let auth = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
            guard auth == .authorized || auth == .limited else {
                await MainActor.run {
                    isSavingAllImages = false
                    saveImageStatus = i18n.t("photosAccessRequired")
                    showingSaveImageStatus = true
                }
                return
            }
            var saved = 0
            await withTaskGroup(of: Bool.self) { group in
                for url in urls {
                    group.addTask {
                        guard let (data, response) = try? await URLSession.shared.data(for: imageRequest(for: url)),
                              (response as? HTTPURLResponse).map({ (200...299).contains($0.statusCode) }) ?? true,
                              let image = UIImage(data: data) else { return false }
                        do {
                            try await PHPhotoLibrary.shared().performChanges {
                                PHAssetChangeRequest.creationRequestForAsset(from: image)
                            }
                            return true
                        } catch {
                            return false
                        }
                    }
                }
                for await ok in group where ok { saved += 1 }
            }
            await MainActor.run {
                isSavingAllImages = false
                saveImageStatus = saved > 0
                    ? i18n.tFormat("savedImagesToPhotos", saved)
                    : i18n.t("imageNoneSaved")
                showingSaveImageStatus = true
            }
        }
    }

    private func imageRequest(for imageURL: URL) -> URLRequest {
        var request = URLRequest(url: imageURL)
        request.setValue("image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8", forHTTPHeaderField: "Accept")
        request.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", forHTTPHeaderField: "User-Agent")
        if let pageURL = originalPageUrl {
            request.setValue(pageURL.absoluteString, forHTTPHeaderField: "Referer")
        }
        return request
    }
}

struct WebViewHelper: UIViewRepresentable {
    let url: URL
    let cacheId: String
    let platform: String
    let themeMode: AppThemeMode
    let fontSize: CGFloat
    let fontFamilyCSS: String
    let readerMode: Bool
    let saveAllImagesCounter: Int
    let onLoadStateChange: (ReaderWebLoadState) -> Void
    let onImageAction: (ReaderImageAction) -> Void
    let onSaveAllImages: (([URL]) -> Void)?
    let onContentBlocked: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(context.coordinator, name: "oshireader")
        configuration.userContentController.addUserScript(WKUserScript(source: readerInjectedJS, injectionTime: .atDocumentEnd, forMainFrameOnly: false))

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        if platform == "twitter" {
            // x.com serves a broken "open in app" redirect to user agents that look like an
            // embedded/in-app browser (i.e. WKWebView's default UA). A full, standard mobile
            // Safari UA avoids that redirect and gets the normal page.
            webView.customUserAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
        }
        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {
        context.coordinator.parent = self
        let requestURL = url.absoluteString
        if context.coordinator.currentRequestURL != requestURL {
            context.coordinator.currentRequestURL = requestURL
            context.coordinator.beginNewRequest()
            onLoadStateChange(.loading)
            uiView.load(URLRequest(url: url))
        } else {
            uiView.evaluateJavaScript(styleInjectionJS(), completionHandler: nil)
        }
        if saveAllImagesCounter != context.coordinator.lastSaveAllCounter {
            context.coordinator.lastSaveAllCounter = saveAllImagesCounter
            let callback = onSaveAllImages
            uiView.evaluateJavaScript("(function(){ if(!window.__oshiCollectImages) return false; window.__oshiCollectImages(); return true; })()") { result, _ in
                // If the function wasn't injected yet (page still loading), reset the spinner
                if let ran = result as? Bool, !ran {
                    DispatchQueue.main.async { callback?([]) }
                }
            }
        }
    }

    private func styleInjectionJS() -> String {
        let bgColorHex: String
        let textColorHex: String
        let linkHex: String

        switch themeMode {
        case .light:
            bgColorHex = "#ffffff"
            textColorHex = "#1a1a1a"
            linkHex = "#7C3AED"
        case .dark:
            bgColorHex = "#121215"
            textColorHex = "#e5e5e7"
            linkHex = "#A78BFA"
        case .sepia:
            bgColorHex = "#f5ebd6"
            textColorHex = "#38250f"
            linkHex = "#8f5a00"
        }

        let readerCSS: String
        if readerMode {
            readerCSS = """
            body {
                background-color: \(bgColorHex) !important;
                color: \(textColorHex) !important;
                font-size: \(fontSize)px !important;
                line-height: 1.75 !important;
                padding: 16px !important;
                max-width: 760px !important;
                margin: 0 auto !important;
                word-break: break-word !important;
            }
            body, body * {
                font-family: \(fontFamilyCSS) !important;
            }
            nav, header, footer, aside, iframe, [role=navigation], [role=banner], [role=contentinfo],
            .sidebar, .ad, .ads, .adbox, .ad_box, .ad_area, .adsbygoogle, .advert, .advertisement,
            .banner, .sponsor, .sponsored, .promotion, [data-ad], [data-ad-unit], [data-google-query-id],
            [id*="ad-"], [id^="ad_"], [id*="_ad_"], [id*="ads"], [class*=" ad-"], [class^="ad-"],
            [class*=" ads"], [class*="_ad_"], [class*="advert"], [class*="banner"], [class*="sponsor"] {
                display: none !important;
                visibility: hidden !important;
                max-height: 0 !important;
                overflow: hidden !important;
            }
            img, video { max-width: 100% !important; height: auto !important; border-radius: 8px !important; }
            pre, code { white-space: pre-wrap !important; word-break: break-word !important; }
            a { color: \(linkHex) !important; }
            [data-oshireader-reader-root="true"] {
                display: block !important;
                max-width: 720px !important;
                margin: 0 auto !important;
                padding: 2px 0 28px !important;
            }
            [data-oshireader-reader-root="true"] p,
            [data-oshireader-reader-root="true"] li,
            [data-oshireader-reader-root="true"] blockquote {
                font-size: \(fontSize)px !important;
                line-height: 1.82 !important;
                letter-spacing: 0 !important;
            }
            [data-oshireader-reader-root="true"] h1,
            [data-oshireader-reader-root="true"] h2,
            [data-oshireader-reader-root="true"] h3 {
                color: \(textColorHex) !important;
                line-height: 1.28 !important;
                letter-spacing: 0 !important;
                margin: 1.2em 0 0.55em !important;
            }
            [data-oshireader-reader-root="true"] p {
                margin: 0 0 1.05em !important;
            }
            [data-oshireader-reader-root="true"] figure {
                margin: 1.3em 0 !important;
            }
            [data-oshireader-reader-root="true"] figcaption,
            [data-oshireader-reader-root="true"] time,
            [data-oshireader-reader-root="true"] small {
                color: \(textColorHex) !important;
                opacity: 0.72 !important;
            }
            """
        } else if platform == "twitter" {
            // x.com is a heavily self-styled SPA with its own dark/light theming; forcing a flat
            // body background/text color clashes with it and makes buttons invisible/illegible.
            readerCSS = ""
        } else {
            readerCSS = """
            body {
                background-color: \(bgColorHex) !important;
                color: \(textColorHex) !important;
            }
            """
        }

        return """
        (function() {
            document.querySelectorAll('[data-oshireader-reader-root="true"]').forEach(function(el) {
                el.removeAttribute('data-oshireader-reader-root');
            });
            var style = document.getElementById('oshireader-injected-style');
            if (!style) {
                style = document.createElement('style');
                style.id = 'oshireader-injected-style';
                document.head.appendChild(style);
            }
            style.innerHTML = `\(readerCSS)`;
            if (\(readerMode ? "true" : "false")) {
                var selectors = [
                    'article',
                    'main',
                    '[role="main"]',
                    '.article',
                    '.post',
                    '.entry-content',
                    '.article-body',
                    '.story-body',
                    '.content',
                    '#content'
                ];
                var best = null;
                var bestScore = 0;
                selectors.forEach(function(selector) {
                    document.querySelectorAll(selector).forEach(function(el) {
                        var text = el.innerText ? el.innerText.replace(/\\s+/g, ' ').trim() : '';
                        var paragraphs = el.querySelectorAll('p, li, blockquote').length;
                        var rect = el.getBoundingClientRect();
                        var score = text.length + (paragraphs * 80) + Math.min(rect.height || 0, 1400);
                        if (text.length >= 240 && rect.width > 0 && rect.height > 0 && score > bestScore) {
                            best = el;
                            bestScore = score;
                        }
                    });
                });
                if (best) best.setAttribute('data-oshireader-reader-root', 'true');
            }
        })();
        """
    }

    class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        var parent: WebViewHelper
        var lastSaveAllCounter = 0
        var currentRequestURL: String?
        private var hasCommittedPage = false
        private var pendingFailure: DispatchWorkItem?

        init(_ parent: WebViewHelper) {
            self.parent = parent
        }

        func beginNewRequest() {
            hasCommittedPage = false
            pendingFailure?.cancel()
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            pendingFailure?.cancel()
            if !hasCommittedPage {
                DispatchQueue.main.async { self.parent.onLoadStateChange(.loading) }
            }
        }

        func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
            hasCommittedPage = true
            pendingFailure?.cancel()
            DispatchQueue.main.async { self.parent.onLoadStateChange(.loaded) }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            hasCommittedPage = true
            pendingFailure?.cancel()
            DispatchQueue.main.async { self.parent.onLoadStateChange(.loaded) }
            webView.evaluateJavaScript(parent.styleInjectionJS(), completionHandler: nil)
            webView.evaluateJavaScript("document.documentElement.outerHTML") { result, _ in
                guard let html = result as? String, !html.isEmpty else { return }
                LocalDB.shared.saveContentCache(id: self.parent.cacheId, html: html)
            }
            checkForBlockedContent(in: webView)
        }

        private func checkForBlockedContent(in webView: WKWebView) {
            // Some pages (Google News' redirect shell, x.com without a session, etc.) report a
            // successful load while a client-side script is still working out where to send the
            // user, or never manages to. Give that script a moment, then treat a still-empty
            // page as failed-to-display rather than silently leaving the user on a blank screen.
            let requestURLAtCheckTime = currentRequestURL
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self, weak webView] in
                guard let self, let webView, self.currentRequestURL == requestURLAtCheckTime else { return }
                webView.evaluateJavaScript(Self.contentDisplayabilityJS) { result, _ in
                    guard let metrics = result as? [String: Any],
                          ReaderContentDisplayability.shouldShowBlockedBanner(metrics: metrics) else { return }
                    DispatchQueue.main.async { self.parent.onContentBlocked() }
                }
            }
        }

        private static let contentDisplayabilityJS = """
        (function() {
          var body = document.body;
          var doc = document.documentElement;
          var text = body && body.innerText ? body.innerText.replace(/\\s+/g, ' ').trim() : '';
          var title = document.title ? document.title.trim() : '';
          var url = location.href || '';
          var selectors = 'article, main, [role="main"], .article, .post, .entry-content, .content, #content';
          var visibleTextNodes = 0;

          if (body) {
            var candidates = body.querySelectorAll('p, h1, h2, h3, li, blockquote, pre');
            for (var i = 0; i < candidates.length; i++) {
              var rect = candidates[i].getBoundingClientRect();
              var nodeText = candidates[i].innerText ? candidates[i].innerText.trim() : '';
              if (nodeText.length >= 12 && rect.width > 0 && rect.height > 0) visibleTextNodes++;
              if (visibleTextNodes >= 3) break;
            }
          }

          return {
            textLength: text.length,
            titleLength: title.length,
            hasReaderContainer: !!(body && body.querySelector(selectors)),
            linkCount: body ? body.querySelectorAll('a[href]').length : 0,
            imageCount: body ? body.querySelectorAll('img, picture, video, iframe').length : 0,
            visibleTextNodes: visibleTextNodes,
            height: Math.max(
              body ? body.scrollHeight : 0,
              doc ? doc.scrollHeight : 0,
              body ? body.offsetHeight : 0,
              doc ? doc.offsetHeight : 0
            ),
            blockedText: /sign in|log in|enable javascript|unsupported browser|cannot display|couldn't display|not available|access denied|forbidden|attention required|cloudflare/i.test(text + ' ' + title),
            urlLooksBlank: /about:blank|\\/sorry\\/|\\/signin|\\/login/i.test(url)
          };
        })();
        """

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            handleLoadFailure(error, in: webView)
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            handleLoadFailure(error, in: webView)
        }

        private func handleLoadFailure(_ error: Error, in webView: WKWebView) {
            let nsError = error as NSError
            AppLogger.network.warning("Reader load failed domain=\(nsError.domain, privacy: .public) code=\(nsError.code) url=\(webView.url?.absoluteString ?? self.parent.url.absoluteString, privacy: .public)")

            if isBenignNavigationFailure(error) || hasCommittedPage {
                if hasCommittedPage {
                    DispatchQueue.main.async { self.parent.onLoadStateChange(.loaded) }
                }
                return
            }

            if !loadCachedPage(in: webView) {
                if parent.platform == "twitter" {
                    DispatchQueue.main.async { self.parent.onContentBlocked() }
                }
                pendingFailure?.cancel()
                let failure = DispatchWorkItem { [weak self, weak webView] in
                    guard let self, let webView, !self.hasCommittedPage else { return }
                    webView.evaluateJavaScript("document.readyState") { result, _ in
                        if self.hasCommittedPage || result is String {
                            DispatchQueue.main.async { self.parent.onLoadStateChange(.loaded) }
                        } else {
                            DispatchQueue.main.async { self.parent.onLoadStateChange(.failed) }
                        }
                    }
                }
                pendingFailure = failure
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5, execute: failure)
            }
        }

        private func isBenignNavigationFailure(_ error: Error) -> Bool {
            let nsError = error as NSError
            if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled {
                return true
            }
            if nsError.domain == "WebKitErrorDomain" && nsError.code == 102 {
                return true
            }
            return false
        }

        private func loadCachedPage(in webView: WKWebView) -> Bool {
            guard let html = LocalDB.shared.getContentCache(id: parent.cacheId) else { return false }
            pendingFailure?.cancel()
            hasCommittedPage = false
            DispatchQueue.main.async { self.parent.onLoadStateChange(.loading) }
            webView.loadHTMLString(html, baseURL: parent.url)
            return true
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }
            let scheme = url.scheme?.lowercased() ?? ""
            if ["mailto", "tel", "sms", "facetime", "facetime-audio"].contains(scheme) {
                decisionHandler(.cancel)
                UIApplication.shared.open(url)
                return
            }
            if parent.platform != "5ch", shouldBlockReaderRequest(url.absoluteString) {
                decisionHandler(.cancel)
                return
            }
            // Rewrite 5ch.net navigations (including those reached via Google News redirects)
            // to itest.5ch.io so threads are actually readable in the in-app browser.
            let host = url.host ?? ""
            let hostRange = NSRange(host.startIndex..., in: host)
            if _ReaderRegex.fivechHost?.firstMatch(in: host, range: hostRange) != nil {
                let rewritten = normalize5chReaderUrl(url.absoluteString)
                if rewritten != url.absoluteString, let rewrittenUrl = URL(string: rewritten) {
                    decisionHandler(.cancel)
                    webView.load(URLRequest(url: rewrittenUrl))
                    return
                }
            }
            decisionHandler(.allow)
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "oshireader",
                  let body = message.body as? [String: Any],
                  let type = body["type"] as? String else { return }
            if type == "image-action",
               let rawUrl = body["url"] as? String,
               let url = URL(string: rawUrl) {
                parent.onImageAction(ReaderImageAction(url: url, alt: body["alt"] as? String))
            } else if type == "all-images",
                      let rawUrls = body["urls"] as? [String] {
                let urls = rawUrls.compactMap { URL(string: $0) }
                DispatchQueue.main.async { self.parent.onSaveAllImages?(urls) }
            }
        }
    }
}

struct SafariReaderView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let configuration = SFSafariViewController.Configuration()
        configuration.entersReaderIfAvailable = false
        let controller = SFSafariViewController(url: url, configuration: configuration)
        controller.dismissButtonStyle = .close
        return controller
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

enum ReaderContentDisplayability {
    static func shouldShowBlockedBanner(metrics: [String: Any]) -> Bool {
        let textLength = intMetric("textLength", in: metrics)
        let titleLength = intMetric("titleLength", in: metrics)
        let linkCount = intMetric("linkCount", in: metrics)
        let imageCount = intMetric("imageCount", in: metrics)
        let visibleTextNodes = intMetric("visibleTextNodes", in: metrics)
        let height = intMetric("height", in: metrics)
        let hasReaderContainer = metrics["hasReaderContainer"] as? Bool ?? false
        let blockedText = metrics["blockedText"] as? Bool ?? false
        let urlLooksBlank = metrics["urlLooksBlank"] as? Bool ?? false

        let hasMeaningfulStructure = hasReaderContainer
            || visibleTextNodes >= 2
            || linkCount >= 3
            || imageCount >= 2
            || height >= 900
            || titleLength >= 8

        if hasMeaningfulStructure && !blockedText && !urlLooksBlank {
            return false
        }
        return (textLength < 40 && !hasMeaningfulStructure) || (blockedText && textLength < 180) || urlLooksBlank
    }

    private static func intMetric(_ key: String, in metrics: [String: Any]) -> Int {
        if let value = metrics[key] as? Int { return value }
        if let value = metrics[key] as? Double { return Int(value) }
        if let value = metrics[key] as? NSNumber { return value.intValue }
        return 0
    }
}

private enum _ReaderRegex {
    static let schemeAllowlist = try? NSRegularExpression(
        pattern: #"^(about:|data:|blob:|file:|mailto:|tel:)"#,
        options: .caseInsensitive
    )
    static let adBlocklist = try? NSRegularExpression(
        pattern: #"(2mdn|doubleclick|googlesyndication|googleadservices|adservice\.google|googletagmanager|google-analytics|analytics\.yahoo|yjtag\.yahoo|yads\.c\.yimg|ad\.yahoo|ad-stir|ad-generation|admatrix|adingo|fam-ad|fluct|genieessp|gmossp|i-mobile|im-apps|impact-ad|microad|nend|popin|taboola|outbrain|/adserver[/.?_-]|/ads?[/.?_-]|/advert|/banner|/sponsor|/promoted)"#,
        options: .caseInsensitive
    )
    static let itestHost   = try? NSRegularExpression(pattern: #"^itest\.5ch\.(net|io)$"#)
    static let fivechHost  = try? NSRegularExpression(pattern: #"(^|\.)5ch\.(net|io)$"#)
    static let twochHost   = try? NSRegularExpression(pattern: #"(^|\.)2ch\.sc$"#)
    static let itestPath   = try? NSRegularExpression(pattern: #"^/([^/]+)/test/read\.cgi/([^/]+)/(\d{9,})"#)
    static let fivechPath  = try? NSRegularExpression(pattern: #"/test/read\.cgi/([^/]+)/(\d{9,})"#)
    static let oriconArticle = try? NSRegularExpression(pattern: #"/(?:news|article)/(\d+)"#)
}

private func normalizedReaderUrl(_ rawUrl: String, platform: String) -> String? {
    let stripped = stripTrackingParams(rawUrl)
    if platform == "5ch" {
        return normalize5chReaderUrl(stripped)
    }
    if platform == "oricon", let article = stripped.match(_ReaderRegex.oriconArticle) {
        return "https://www.oricon.co.jp/news/\(article)/full/"
    }
    return stripped
}

private func stripTrackingParams(_ rawUrl: String) -> String {
    guard var components = URLComponents(string: rawUrl) else { return rawUrl }
    let blockedPrefixes = ["utm_"]
    let blockedKeys = Set(["fbclid", "gclid", "yclid", "mc_cid", "mc_eid", "igshid", "ref"])
    components.queryItems = components.queryItems?.filter { item in
        let key = item.name.lowercased()
        return !blockedKeys.contains(key) && !blockedPrefixes.contains(where: { key.hasPrefix($0) })
    }
    if components.queryItems?.isEmpty == true {
        components.queryItems = nil
    }
    return components.url?.absoluteString ?? rawUrl
}

private func normalize5chReaderUrl(_ rawUrl: String) -> String {
    guard let url = URL(string: rawUrl), let host = url.host else { return rawUrl }
    let hostRange = NSRange(host.startIndex..., in: host)
    let isItest  = _ReaderRegex.itestHost?.firstMatch(in: host, range: hostRange) != nil
    let isFiveCh = _ReaderRegex.fivechHost?.firstMatch(in: host, range: hostRange) != nil
    let isTwoCh = _ReaderRegex.twochHost?.firstMatch(in: host, range: hostRange) != nil
    guard isItest || isFiveCh || isTwoCh else { return rawUrl }

    if isTwoCh {
        // 2ch.sc mirror hosts do not always match the real itest server.
        return rawUrl
    }

    if isItest, let match = url.path.match(_ReaderRegex.itestPath) {
        let parts = match.components(separatedBy: "|")
        if parts.count == 3 {
            return "https://itest.5ch.io/\(parts[0])/test/read.cgi/\(parts[1])/\(parts[2])/"
        }
    }

    guard let match = url.path.match(_ReaderRegex.fivechPath) else { return rawUrl }
    let parts = match.components(separatedBy: "|")
    guard parts.count == 2 else { return rawUrl }
    let server = host.components(separatedBy: ".").first ?? ""
    guard !server.isEmpty, !["www", "itest", "find", "dig"].contains(server.lowercased()) else { return rawUrl }
    return "https://itest.5ch.io/\(server)/test/read.cgi/\(parts[0])/\(parts[1])/"
}

private func shouldBlockReaderRequest(_ rawUrl: String) -> Bool {
    let range = NSRange(rawUrl.startIndex..., in: rawUrl)
    if _ReaderRegex.schemeAllowlist?.firstMatch(in: rawUrl, range: range) != nil { return false }
    return _ReaderRegex.adBlocklist?.firstMatch(in: rawUrl, range: range) != nil
}

private let readerInjectedJS = """
(function () {
  if (window.__OSHIREADER_IMAGE_ACTIONS__) return true;
  window.__OSHIREADER_IMAGE_ACTIONS__ = true;

  function absoluteUrl(value) {
    if (!value) return '';
    try { return new URL(value, document.baseURI).toString(); } catch (e) { return String(value); }
  }
  function srcFromSrcset(value) {
    if (!value) return '';
    var parts = String(value).split(',').map(function(part) { return part.trim().split(/\\s+/)[0]; }).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : '';
  }
  function imageCandidate(target) {
    var el = target;
    var depth = 0;
    while (el && el.nodeType === 1 && depth < 8) {
      var tag = (el.tagName || '').toUpperCase();
      if (tag === 'IMG') {
        return {
          url: el.currentSrc || el.src || el.getAttribute('src') || el.getAttribute('data-src') || el.getAttribute('data-original') || el.getAttribute('data-lazy-src') || srcFromSrcset(el.getAttribute('srcset') || el.getAttribute('data-srcset')),
          alt: el.getAttribute('alt') || el.getAttribute('title') || document.title || ''
        };
      }
      var bg = '';
      try {
        var style = window.getComputedStyle(el);
        var match = style && style.backgroundImage && style.backgroundImage.match(/url\\((["']?)(.*?)\\1\\)/);
        bg = match ? match[2] : '';
      } catch (e) {}
      if (bg && bg !== 'none') return { url: bg, alt: el.getAttribute('aria-label') || el.getAttribute('title') || document.title || '' };
      el = el.parentElement;
      depth++;
    }
    return null;
  }
  function postImage(target) {
    var found = imageCandidate(target);
    if (!found) return false;
    var imageUrl = absoluteUrl(found.url);
    if (!/^https?:\\/\\//i.test(imageUrl)) return false;
    window.webkit.messageHandlers.oshireader.postMessage({ type: 'image-action', url: imageUrl, alt: found.alt || '' });
    return true;
  }

  window.__oshiCollectImages = function() {
    var seen = new Set();
    var urls = [];
    var imgs = document.querySelectorAll('img');
    imgs.forEach(function(img) {
      var url = img.currentSrc || img.src || img.getAttribute('data-src') ||
                img.getAttribute('data-original') || img.getAttribute('data-lazy-src') ||
                srcFromSrcset(img.getAttribute('srcset') || img.getAttribute('data-srcset') || '');
      url = absoluteUrl(url);
      if (!url || !/^https?:\\/\\//i.test(url)) return;
      if (seen.has(url)) return;

      // Exclude images that are too small to be article photos
      var w = img.naturalWidth || img.width || 0;
      var h = img.naturalHeight || img.height || 0;
      if (w > 0 && h > 0 && (w < 300 || h < 200)) return;

      // Exclude by URL pattern: thumbnails, icons, avatars, logos, tracking pixels
      var lower = url.toLowerCase().replace(/\\?.*$/, '');
      if (/\\/(thumb(nail)?s?|icon|avatar|profile|logo|favicon|placeholder|sprite|emoji|badge|sticker|banner|ad)[_\\-./#]|[_\\-](thumb|icon|avatar|logo|small|xs|sm|tiny|mini)[._]|\\b1x1\\b|\\/1\\/1\\.|pixel|beacon/.test(lower)) return;

      seen.add(url);
      urls.push(url);
    });
    window.webkit.messageHandlers.oshireader.postMessage({ type: 'all-images', urls: urls });
  };

  document.addEventListener('click', function(event) {
    var el = event.target;
    var depth = 0;
    while (el && el.nodeType === 1 && depth < 6) {
      if ((el.tagName || '').toUpperCase() === 'IMG') {
        if (postImage(el)) {
          event.preventDefault();
          event.stopPropagation();
        }
        return;
      }
      el = el.parentElement;
      depth++;
    }
  }, true);

  document.addEventListener('contextmenu', function(event) {
    if (postImage(event.target)) event.preventDefault();
  }, true);
  return true;
})();
true;
"""

private extension String {
    func match(_ regex: NSRegularExpression?) -> String? {
        guard let regex,
              let match = regex.firstMatch(in: self, range: NSRange(startIndex..., in: self)) else {
            return nil
        }
        if match.numberOfRanges == 1 {
            return String(self[Range(match.range(at: 0), in: self)!])
        }
        var captures = [String]()
        for index in 1..<match.numberOfRanges {
            guard let range = Range(match.range(at: index), in: self) else { continue }
            captures.append(String(self[range]))
        }
        return captures.joined(separator: "|")
    }

}
