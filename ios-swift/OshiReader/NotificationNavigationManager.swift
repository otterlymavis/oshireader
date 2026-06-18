import Foundation

@MainActor
final class NotificationNavigationManager: ObservableObject {
    static let shared = NotificationNavigationManager()

    @Published var selectedItem: FeedItem?

    private init() {}

    func openNotification(userInfo: [AnyHashable: Any]) {
        guard let notificationItem = feedItem(from: userInfo) else { return }
        if let existing = LocalDB.shared.feedItems.first(where: { $0.id == notificationItem.id }) {
            selectedItem = existing
        } else {
            selectedItem = notificationItem
        }
    }

    @discardableResult
    func saveNotificationItem(userInfo: [AnyHashable: Any]) -> Bool {
        guard let notificationItem = feedItem(from: userInfo) else { return false }
        guard !LocalDB.shared.savedPages.contains(where: { $0.id == notificationItem.id }) else {
            return false
        }
        return LocalDB.shared.toggleSaved(item: notificationItem)
    }

    private func feedItem(from userInfo: [AnyHashable: Any]) -> FeedItem? {
        let previewItem = dictionaryValue(userInfo["preview_item"])
        let itemID = stringValue(userInfo["item_id"]) ?? stringValue(previewItem?["id"])
        let itemURL = stringValue(userInfo["item_url"]) ?? stringValue(previewItem?["url"])
        guard let itemID, let itemURL else { return nil }

        let now = _ISO8601Cache.withoutFractional.string(from: Date())
        return FeedItem(
            id: itemID,
            platform: stringValue(userInfo["item_platform"]) ?? stringValue(previewItem?["platform"]) ?? "web",
            url: itemURL,
            title: stringValue(userInfo["item_title"]) ?? stringValue(previewItem?["title"]),
            content_text: stringValue(userInfo["item_content_text"]) ?? stringValue(previewItem?["content_text"]),
            author: stringValue(userInfo["item_author"]) ?? stringValue(previewItem?["author"]),
            thumbnail_url: stringValue(userInfo["thumbnail_url"]) ?? stringValue(previewItem?["thumbnail_url"]),
            media_type: stringValue(userInfo["item_media_type"]) ?? stringValue(previewItem?["media_type"]) ?? "article",
            published_at: stringValue(userInfo["item_published_at"]) ?? stringValue(previewItem?["published_at"]) ?? now,
            watch_term_keyword: stringValue(userInfo["watch_term_keyword"]) ?? "",
            fetched_at: now
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
