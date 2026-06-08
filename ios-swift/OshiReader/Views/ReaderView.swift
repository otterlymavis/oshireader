import Photos
import SwiftUI
import UIKit
import WebKit

struct ReaderImageAction: Identifiable {
    let id = UUID()
    let url: URL
    let alt: String?
}

struct ReaderView: View {
    let feedItem: FeedItem

    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    @StateObject private var appearance = AppearanceManager.shared

    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    @State private var readerMode = true
    @State private var readerTheme: AppThemeMode = .light
    @State private var fontSize: CGFloat = 16.0
    @State private var isTranslated = false
    @State private var imageAction: ReaderImageAction?
    @State private var saveImageStatus = ""
    @State private var showingSaveImageStatus = false
    @State private var saveAllImagesCounter = 0
    @State private var isSavingAllImages = false

    var targetUrl: URL? {
        guard let normalized = normalizedReaderUrl(feedItem.url, platform: feedItem.platform),
              let originalUrl = URL(string: normalized) else { return nil }
        if isTranslated {
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
                WebViewHelper(
                    url: url,
                    cacheId: feedItem.id,
                    themeMode: readerTheme,
                    fontSize: fontSize,
                    readerMode: readerMode,
                    saveAllImagesCounter: saveAllImagesCounter,
                    onImageAction: { imageAction = $0 },
                    onSaveAllImages: { urls in saveAllImages(urls) }
                )
                .background(bgColor)
            } else {
                Text(i18n.t("invalidUrl"))
                    .foregroundColor(theme.colors.textMuted)
            }

            readerControlBar
        }
        .background(bgColor)
        .navigationTitle(feedItem.title ?? i18n.t("readerTitle"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button {
                    isTranslated.toggle()
                } label: {
                    Image(systemName: "translate")
                        .foregroundColor(isTranslated ? theme.colors.primary : theme.colors.textMuted)
                }
                .accessibilityIdentifier("reader.translateButton")
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

            ToolbarItem(placement: .navigationBarTrailing) {
                if let url = URL(string: feedItem.url) {
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
            if UserDefaults.standard.bool(forKey: "auto_translate_reader") {
                isTranslated = true
            }
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
                let (data, _) = try await URLSession.shared.data(from: url)
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
                        guard let (data, _) = try? await URLSession.shared.data(from: url),
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
}

struct WebViewHelper: UIViewRepresentable {
    let url: URL
    let cacheId: String
    let themeMode: AppThemeMode
    let fontSize: CGFloat
    let readerMode: Bool
    let saveAllImagesCounter: Int
    let onImageAction: (ReaderImageAction) -> Void
    let onSaveAllImages: (([URL]) -> Void)?

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
        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {
        context.coordinator.parent = self
        if uiView.url == nil || uiView.url?.absoluteString != url.absoluteString {
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
                font-family: \(AppearanceManager.shared.readerFontFamilyCSS) !important;
                line-height: 1.75 !important;
                padding: 16px !important;
                max-width: 760px !important;
                margin: 0 auto !important;
                word-break: break-word !important;
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
            """
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
            var style = document.getElementById('oshireader-injected-style');
            if (!style) {
                style = document.createElement('style');
                style.id = 'oshireader-injected-style';
                document.head.appendChild(style);
            }
            style.innerHTML = `\(readerCSS)`;
        })();
        """
    }

    class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        var parent: WebViewHelper
        var lastSaveAllCounter = 0

        init(_ parent: WebViewHelper) {
            self.parent = parent
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            webView.evaluateJavaScript(parent.styleInjectionJS(), completionHandler: nil)
            webView.evaluateJavaScript("document.documentElement.outerHTML") { result, _ in
                guard let html = result as? String, !html.isEmpty else { return }
                LocalDB.shared.saveContentCache(id: self.parent.cacheId, html: html)
            }
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            loadCachedPage(in: webView)
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            loadCachedPage(in: webView)
        }

        private func loadCachedPage(in webView: WKWebView) {
            guard let html = LocalDB.shared.getContentCache(id: parent.cacheId) else { return }
            webView.loadHTMLString(html, baseURL: parent.url)
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
            if shouldBlockReaderRequest(url.absoluteString) {
                decisionHandler(.cancel)
                return
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

private func normalizedReaderUrl(_ rawUrl: String, platform: String) -> String? {
    let stripped = stripTrackingParams(rawUrl)
    if platform == "5ch" {
        return normalize5chReaderUrl(stripped)
    }
    if platform == "oricon", let article = stripped.match(#"/(?:news|article)/(\d+)"#) {
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
    return components.url?.absoluteString ?? rawUrl
}

private func normalize5chReaderUrl(_ rawUrl: String) -> String {
    guard let url = URL(string: rawUrl), let host = url.host else { return rawUrl }
    let isItest = host.range(of: #"^itest\.5ch\.(net|io)$"#, options: .regularExpression) != nil
    let isFiveCh = host.range(of: #"(^|\.)5ch\.(net|io)$"#, options: .regularExpression) != nil
    guard isItest || isFiveCh else { return rawUrl }

    if isItest, let match = url.path.match(#"^/([^/]+)/test/read\.cgi/([^/]+)/(\d{9,})"#) {
        let parts = match.components(separatedBy: "|")
        if parts.count == 3 {
            return "https://itest.5ch.io/\(parts[0])/test/read.cgi/\(parts[1])/\(parts[2])/"
        }
    }

    guard let match = url.path.match(#"/test/read\.cgi/([^/]+)/(\d{9,})"#) else { return rawUrl }
    let parts = match.components(separatedBy: "|")
    guard parts.count == 2 else { return rawUrl }
    let server = host.components(separatedBy: ".").first ?? ""
    guard !server.isEmpty, !["www", "itest", "find", "dig"].contains(server.lowercased()) else { return rawUrl }
    return "https://itest.5ch.io/\(server)/test/read.cgi/\(parts[0])/\(parts[1])/"
}

private func shouldBlockReaderRequest(_ rawUrl: String) -> Bool {
    if rawUrl.range(of: #"^(about:|data:|blob:|file:|mailto:|tel:)"#, options: [.regularExpression, .caseInsensitive]) != nil {
        return false
    }
    return rawUrl.range(
        of: #"(2mdn|doubleclick|googlesyndication|googleadservices|adservice\.google|googletagmanager|google-analytics|analytics\.yahoo|yjtag\.yahoo|yads\.c\.yimg|ad\.yahoo|ad-stir|ad-generation|admatrix|adingo|fam-ad|fluct|genieessp|gmossp|i-mobile|im-apps|impact-ad|microad|nend|popin|taboola|outbrain|/adserver[/.?_-]|/ads?[/.?_-]|/advert|/banner|/sponsor|/promoted)"#,
        options: [.regularExpression, .caseInsensitive]
    ) != nil
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
    func match(_ pattern: String) -> String? {
        guard let regex = try? NSRegularExpression(pattern: pattern),
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
