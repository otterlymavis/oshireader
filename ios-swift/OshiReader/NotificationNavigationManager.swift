import Foundation

@MainActor
final class NotificationNavigationManager: ObservableObject {
    static let shared = NotificationNavigationManager()

    @Published var selectedItem: FeedItem?

    private struct NotificationPayload {
        let item: FeedItem
        let hasPlatform: Bool
        let hasMediaType: Bool
        let hasPublishedAt: Bool
        let hasWatchTermKeyword: Bool
    }

    private init() {}

    func openNotification(userInfo: [AnyHashable: Any]) {
        guard let payload = notificationPayload(from: userInfo) else { return }
        selectedItem = Self.preferredNotificationItem(
            payload.item,
            cachedItems: LocalDB.shared.feedItems,
            hasPlatform: payload.hasPlatform,
            hasMediaType: payload.hasMediaType,
            hasPublishedAt: payload.hasPublishedAt,
            hasWatchTermKeyword: payload.hasWatchTermKeyword
        )
    }

    @discardableResult
    func saveNotificationItem(userInfo: [AnyHashable: Any]) -> Bool {
        guard let payload = notificationPayload(from: userInfo) else { return false }
        let notificationItem = Self.preferredNotificationItem(
            payload.item,
            cachedItems: LocalDB.shared.feedItems,
            hasPlatform: payload.hasPlatform,
            hasMediaType: payload.hasMediaType,
            hasPublishedAt: payload.hasPublishedAt,
            hasWatchTermKeyword: payload.hasWatchTermKeyword
        )
        guard !LocalDB.shared.savedPages.contains(where: { $0.id == notificationItem.id }) else {
            return false
        }
        return LocalDB.shared.toggleSaved(item: notificationItem)
    }

    static func preferredNotificationItem(
        _ notificationItem: FeedItem,
        cachedItems: [FeedItem],
        hasPlatform: Bool = true,
        hasMediaType: Bool = true,
        hasPublishedAt: Bool = true,
        hasWatchTermKeyword: Bool = true
    ) -> FeedItem {
        let existing = cachedItems.first {
            $0.id == notificationItem.id &&
                (
                    notificationItem.watch_term_keyword.isEmpty ||
                    $0.watch_term_keyword == notificationItem.watch_term_keyword
                )
        }
        guard let existing else { return notificationItem }

        // The push identifies the item the user actually tapped, so its URL, title,
        // publication date, and watch term take precedence over stale cached values.
        // Cached fields only fill payload fields that APNs omitted for size.
        return FeedItem(
            id: notificationItem.id,
            platform: hasPlatform && notificationItem.platform != "web"
                ? notificationItem.platform
                : existing.platform,
            url: notificationItem.url,
            title: notificationItem.title ?? existing.title,
            content_text: notificationItem.content_text ?? existing.content_text,
            author: notificationItem.author ?? existing.author,
            thumbnail_url: notificationItem.thumbnail_url ?? existing.thumbnail_url,
            media_type: hasMediaType ? notificationItem.media_type : existing.media_type,
            published_at: hasPublishedAt ? notificationItem.published_at : existing.published_at,
            watch_term_keyword: hasWatchTermKeyword && !notificationItem.watch_term_keyword.isEmpty
                ? notificationItem.watch_term_keyword
                : existing.watch_term_keyword,
            fetched_at: existing.fetched_at
        )
    }

    private func notificationPayload(from userInfo: [AnyHashable: Any]) -> NotificationPayload? {
        let previewItem = dictionaryValue(userInfo["preview_item"])
        let itemID = stringValue(userInfo["item_id"]) ?? stringValue(previewItem?["id"])
        let itemURL = stringValue(userInfo["item_url"]) ?? stringValue(previewItem?["url"])
        guard let itemID, let itemURL else { return nil }

        let now = _ISO8601Cache.withoutFractional.string(from: Date())
        let platform = stringValue(userInfo["item_platform"]) ?? stringValue(previewItem?["platform"])
        let mediaType = stringValue(userInfo["item_media_type"]) ?? stringValue(previewItem?["media_type"])
        let publishedAt = stringValue(userInfo["item_published_at"]) ?? stringValue(previewItem?["published_at"])
        let watchTermKeyword = stringValue(userInfo["watch_term_keyword"])
        let item = FeedItem(
            id: itemID,
            platform: platform ?? "web",
            url: itemURL,
            title: stringValue(userInfo["item_title"]) ?? stringValue(previewItem?["title"]),
            content_text: stringValue(userInfo["item_content_text"]) ?? stringValue(previewItem?["content_text"]),
            author: stringValue(userInfo["item_author"]) ?? stringValue(previewItem?["author"]),
            thumbnail_url: stringValue(userInfo["thumbnail_url"]) ?? stringValue(previewItem?["thumbnail_url"]),
            media_type: mediaType ?? "article",
            published_at: publishedAt ?? now,
            watch_term_keyword: watchTermKeyword ?? "",
            fetched_at: now
        )
        return NotificationPayload(
            item: item,
            hasPlatform: platform != nil,
            hasMediaType: mediaType != nil,
            hasPublishedAt: publishedAt != nil,
            hasWatchTermKeyword: watchTermKeyword != nil
        )
    }

    private func stringValue(_ value: Any?) -> String? {
        guard let value else { return nil }
        if let text = value as? String {
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
        if let number = value as? NSNumber {
            return number.stringValue
        }
        return nil
    }

    private func dictionaryValue(_ value: Any?) -> [String: Any]? {
        if let dictionary = value as? [String: Any] {
            return dictionary
        }
        if let dictionary = value as? [AnyHashable: Any] {
            var converted: [String: Any] = [:]
            for (key, value) in dictionary {
                if let key = key as? String {
                    converted[key] = value
                }
            }
            return converted
        }
        return nil
    }
}
