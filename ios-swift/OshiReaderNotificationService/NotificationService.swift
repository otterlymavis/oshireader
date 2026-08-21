import Foundation
import UserNotifications

final class NotificationService: UNNotificationServiceExtension {
    private let maximumAttachmentBytes: Int64 = 10 * 1024 * 1024
    private var contentHandler: ((UNNotificationContent) -> Void)?
    private var bestAttemptContent: UNMutableNotificationContent?
    private var downloadTask: URLSessionDownloadTask?

    override func didReceive(
        _ request: UNNotificationRequest,
        withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void
    ) {
        self.contentHandler = contentHandler
        guard let content = request.content.mutableCopy() as? UNMutableNotificationContent else {
            finish(with: request.content)
            return
        }
        bestAttemptContent = content
        sendReceiptDiagnostic(userInfo: content.userInfo)

        guard let url = thumbnailURL(from: content.userInfo) else {
            finish(with: content)
            return
        }

        var urlRequest = URLRequest(url: url)
        urlRequest.timeoutInterval = 8
        downloadTask = URLSession.shared.downloadTask(with: urlRequest) { [weak self] tempURL, response, _ in
            guard let self else { return }
            defer { self.finish(with: content) }

            guard let tempURL,
                  let http = response as? HTTPURLResponse,
                  (200...299).contains(http.statusCode),
                  http.mimeType?.lowercased().hasPrefix("image/") == true,
                  http.expectedContentLength <= 0 || http.expectedContentLength <= self.maximumAttachmentBytes,
                  self.fileSize(at: tempURL) <= self.maximumAttachmentBytes
            else { return }

            let destination = FileManager.default.temporaryDirectory
                .appendingPathComponent(
                    "oshireader-remote-preview-\(UUID().uuidString).\(self.fileExtension(for: http, url: url))"
                )
            do {
                try FileManager.default.moveItem(at: tempURL, to: destination)
                // Keep the thumbnail out of the collapsed banner/lock screen; it only
                // appears in the custom expanded view when the user long-presses.
                let options = [UNNotificationAttachmentOptionsThumbnailHiddenKey: true]
                content.attachments = [
                    try UNNotificationAttachment(identifier: "preview", url: destination, options: options)
                ]
            } catch {
                return
            }
        }
        downloadTask?.resume()
    }

    override func serviceExtensionTimeWillExpire() {
        downloadTask?.cancel()
        if let bestAttemptContent {
            finish(with: bestAttemptContent)
        }
    }

    private func finish(with content: UNNotificationContent) {
        guard let contentHandler else { return }
        self.contentHandler = nil
        contentHandler(content)
    }

    private func thumbnailURL(from userInfo: [AnyHashable: Any]) -> URL? {
        if let value = userInfo["thumbnail_url"] as? String,
           let url = URL(string: value),
           isSupported(url) {
            return url
        }
        if let preview = userInfo["preview_item"] as? [String: Any],
           let value = preview["thumbnail_url"] as? String,
           let url = URL(string: value),
           isSupported(url) {
            return url
        }
        return nil
    }

    private func isSupported(_ url: URL) -> Bool {
        ["http", "https"].contains(url.scheme?.lowercased())
    }

    private func sendReceiptDiagnostic(userInfo: [AnyHashable: Any]) {
        guard let url = diagnosticsURL(from: userInfo) else { return }

        let detail = diagnosticDetail(userInfo: userInfo)
        let event: [String: Any] = [
            "strategy": "notification_service_extension",
            "status": "received",
            "item_count": intValue(userInfo["new_count"]),
            "added_count": 0,
            "detail": detail,
        ]
        let payload: [String: Any] = [
            "reason": "remote_notification_received",
            "environment": "NotificationService",
            "api_base": apiBase(from: url),
            "app_version": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "",
            "build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "",
            "active_terms_count": 0,
            "subscribed_platforms": [],
            "cached_feed_count": 0,
            "events": [event],
        ]
        guard JSONSerialization.isValidJSONObject(payload),
              let body = try? JSONSerialization.data(withJSONObject: payload)
        else { return }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 4
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        URLSession.shared.dataTask(with: request).resume()
    }

    private func diagnosticsURL(from userInfo: [AnyHashable: Any]) -> URL? {
        if let value = userInfo["diagnostics_url"] as? String,
           let url = URL(string: value),
           isSupported(url) {
            return url
        }
        if let thumbnail = thumbnailURL(from: userInfo),
           let host = thumbnail.host,
           let scheme = thumbnail.scheme,
           isSupported(thumbnail) {
            var components = URLComponents()
            components.scheme = scheme
            components.host = host
            components.port = thumbnail.port
            components.path = "/api/client-diagnostics"
            return components.url
        }
        return nil
    }

    private func apiBase(from url: URL) -> String {
        var components = URLComponents()
        components.scheme = url.scheme
        components.host = url.host
        components.port = url.port
        return components.url?.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) ?? ""
    }

    private func diagnosticDetail(userInfo: [AnyHashable: Any]) -> String {
        let keys = [
            "notification_id",
            "apns_token_suffix",
            "watch_term_id",
            "watch_term_keyword",
            "new_count",
            "item_id",
            "match_id",
        ]
        let parts = keys.compactMap { key -> String? in
            guard let value = userInfo[key] else { return nil }
            return "\(key)=\(value)"
        }
        return String(parts.joined(separator: " ").prefix(500))
    }

    private func intValue(_ value: Any?) -> Int {
        if let value = value as? Int { return value }
        if let value = value as? NSNumber { return value.intValue }
        if let value = value as? String, let parsed = Int(value) { return parsed }
        return 0
    }

    private func fileSize(at url: URL) -> Int64 {
        let values = try? url.resourceValues(forKeys: [.fileSizeKey])
        return Int64(values?.fileSize ?? Int.max)
    }

    private func fileExtension(for response: HTTPURLResponse, url: URL) -> String {
        switch response.mimeType?.lowercased() {
        case "image/png":
            return "png"
        case "image/gif":
            return "gif"
        case "image/webp":
            return "webp"
        case "image/heic", "image/heif":
            return "heic"
        default:
            let ext = url.pathExtension.lowercased()
            return ["jpg", "jpeg"].contains(ext) ? ext : "jpg"
        }
    }
}
