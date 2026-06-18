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
        // Hide source/author labels while preserving the title, message preview,
        // link metadata, and any downloadable thumbnail attachment.
        content.subtitle = ""

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
                content.attachments = [
                    try UNNotificationAttachment(identifier: "preview", url: destination)
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
