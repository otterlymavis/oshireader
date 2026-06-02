import SwiftUI
import WebKit

struct ReaderView: View {
    let feedItem: FeedItem
    
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    
    @State private var readerMode = true
    @State private var readerTheme: AppThemeMode = .light
    @State private var fontSize: CGFloat = 16.0
    @State private var isTranslated = false
    
    @Environment(\.dismiss) private var dismiss
    
    var targetUrl: URL? {
        guard let originalUrl = URL(string: feedItem.url) else { return nil }
        if isTranslated {
            let targetLang: String
            switch i18n.lang {
            case "ja": targetLang = "ja"
            case "en": targetLang = "en"
            case "zh-CN": targetLang = "zh"
            case "zh-TW": targetLang = "zh-Hant"
            default: targetLang = "en"
            }
            if let escapedUrl = feedItem.url.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
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
            // Custom article reader or webview
            if let url = targetUrl {
                WebViewHelper(
                    url: url,
                    themeMode: readerTheme,
                    fontSize: fontSize,
                    readerMode: readerMode
                )
                .background(bgColor)
            } else {
                Text("Invalid URL")
                    .foregroundColor(theme.colors.textMuted)
            }
            
            // Bottom control bar (visible in ReaderMode)
            HStack {
                // Reader mode toggle
                Button(action: { readerMode.toggle() }) {
                    Label(readerMode ? i18n.t("readerModeText") : i18n.t("readerModeWeb"),
                          systemImage: readerMode ? "doc.plaintext" : "globe")
                        .font(.caption)
                        .fontWeight(.bold)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(theme.colors.divider)
                        .foregroundColor(theme.colors.primary)
                        .cornerRadius(8)
                }
                
                Spacer()
                
                if readerMode {
                    // Font Size adjustments
                    HStack(spacing: 12) {
                        Button(action: { fontSize = max(12.0, fontSize - 2.0) }) {
                            Text("A-")
                                .font(.subheadline)
                                .foregroundColor(theme.colors.textSub)
                        }
                        
                        Text("\(Int(fontSize))")
                            .font(.caption)
                            .foregroundColor(theme.colors.textMuted)
                        
                        Button(action: { fontSize = min(28.0, fontSize + 2.0) }) {
                            Text("A+")
                                .font(.subheadline)
                                .foregroundColor(theme.colors.textSub)
                        }
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(theme.colors.divider)
                    .cornerRadius(8)
                }
                
                Spacer()
                
                // Theme picker
                Picker("Theme", selection: $readerTheme) {
                    Image(systemName: "sun.max.fill").tag(AppThemeMode.light)
                    Image(systemName: "moon.fill").tag(AppThemeMode.dark)
                    Image(systemName: "doc.text.magnifyingglass").tag(AppThemeMode.sepia)
                }
                .pickerStyle(.segmented)
                .frame(width: 100)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(theme.colors.card)
            .overlay(
                Rectangle()
                    .frame(height: 0.5)
                    .foregroundColor(theme.colors.divider),
                alignment: .top
            )
        }
        .background(bgColor)
        .navigationTitle(feedItem.title ?? "Reader")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            // Translation Toggle
            ToolbarItem(placement: .navigationBarTrailing) {
                Button(action: {
                    isTranslated.toggle()
                }) {
                    Image(systemName: "translate")
                        .foregroundColor(isTranslated ? theme.colors.primary : theme.colors.textMuted)
                }
            }
            
            // Bookmark Toggle
            ToolbarItem(placement: .navigationBarTrailing) {
                Button(action: {
                    _ = db.toggleSaved(item: feedItem)
                }) {
                    Image(systemName: isSaved ? "bookmark.fill" : "bookmark")
                        .foregroundColor(theme.colors.primary)
                }
            }
            
            // Safari Share Link
            ToolbarItem(placement: .navigationBarTrailing) {
                if let url = URL(string: feedItem.url) {
                    ShareLink(item: url) {
                        Image(systemName: "square.and.arrow.up")
                            .foregroundColor(theme.colors.primary)
                    }
                }
            }
        }
        .onAppear {
            // Initialize readerTheme with app theme mode
            self.readerTheme = theme.mode
        }
    }
    
    private var bgColor: Color {
        switch readerTheme {
        case .light: return Color.white
        case .dark:  return Color(red: 0.08, green: 0.08, blue: 0.1)
        case .sepia: return Color(red: 0.96, green: 0.93, blue: 0.86)
        }
    }
}

// MARK: - WebView Helper Wrapper
struct WebViewHelper: UIViewRepresentable {
    let url: URL
    let themeMode: AppThemeMode
    let fontSize: CGFloat
    let readerMode: Bool
    
    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView()
        webView.navigationDelegate = context.coordinator
        webView.isOpaque = false
        webView.backgroundColor = .clear
        return webView
    }
    
    func updateUIView(_ uiView: WKWebView, context: Context) {
        if uiView.url == nil || (uiView.url?.absoluteString != url.absoluteString && !uiView.isLoading) {
            let request = URLRequest(url: url)
            uiView.load(request)
        } else {
            let js = styleInjectionJS()
            uiView.evaluateJavaScript(js, completionHandler: nil)
        }
    }
    
    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }
    
    private func styleInjectionJS() -> String {
        let bgColorHex: String
        let textColorHex: String
        
        switch themeMode {
        case .light:
            bgColorHex = "#ffffff"
            textColorHex = "#1a1a1a"
        case .dark:
            bgColorHex = "#121215"
            textColorHex = "#e5e5e7"
        case .sepia:
            bgColorHex = "#f5ebd6"
            textColorHex = "#38250f"
        }
        
        var js = """
        var style = document.getElementById('oshireader-injected-style');
        if (!style) {
            style = document.createElement('style');
            style.id = 'oshireader-injected-style';
            document.head.appendChild(style);
        }
        """
        
        if readerMode {
            js += """
            style.innerHTML = `
                body {
                    background-color: \(bgColorHex) !important;
                    color: \(textColorHex) !important;
                    font-size: \(fontSize)px !important;
                    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif !important;
                    line-height: 1.6 !important;
                    padding: 16px !important;
                    max-width: 680px !important;
                    margin: 0 auto !important;
                }
                header, footer, nav, aside, iframe, .sidebar, .ads, .comment, #header, #footer, #sidebar, .header, .footer, .ad, .adsense, .header-container, .footer-container {
                    display: none !important;
                }
                img {
                    max-width: 100% !important;
                    height: auto !important;
                    border-radius: 8px !important;
                    margin: 8px 0 !important;
                }
                a {
                    color: #7C3AED !important;
                }
            `;
            """
        } else {
            // Native web view: just background inject override
            js += """
            style.innerHTML = `
                body {
                    background-color: \(bgColorHex) !important;
                    color: \(textColorHex) !important;
                }
            `;
            """
        }
        return js
    }
    
    class Coordinator: NSObject, WKNavigationDelegate {
        var parent: WebViewHelper
        
        init(_ parent: WebViewHelper) {
            self.parent = parent
        }
        
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            let js = parent.styleInjectionJS()
            webView.evaluateJavaScript(js, completionHandler: nil)
        }
    }
}
