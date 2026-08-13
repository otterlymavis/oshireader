import Foundation
import UIKit
import UserNotifications

protocol NotificationCenterClient {
    func authorizationStatus() async -> UNAuthorizationStatus
    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool
    func add(_ request: UNNotificationRequest) async throws
    func setNotificationCategories(_ categories: Set<UNNotificationCategory>)
}

extension UNUserNotificationCenter: NotificationCenterClient {
    func authorizationStatus() async -> UNAuthorizationStatus {
        await notificationSettings().authorizationStatus
    }
}

@MainActor
final class NotificationManager: ObservableObject {
    static let shared = NotificationManager()
    nonisolated static let resultPreviewCategoryIdentifier = "OSHI_RESULT_PREVIEW"
    nonisolated static let openResultActionIdentifier = "OPEN_RESULT"
    nonisolated static let saveResultActionIdentifier = "SAVE_RESULT"

    @Published private(set) var authorizationStatus: UNAuthorizationStatus = .notDetermined
    @Published private(set) var lastRemoteRegistrationError: String?

    private let center: NotificationCenterClient
    private let remoteRegistration: @MainActor () -> Void
    private let maximumAttachmentBytes: Int64 = 10 * 1024 * 1024
    private var lastRegisteredDeviceToken: String?
    private struct TokenRegistrationWaiter {
        let continuation: CheckedContinuation<Bool, Never>
        let syncAfterVerification: Bool
    }
    private var tokenRegistrationWaiters: [UUID: TokenRegistrationWaiter] = [:]
    private var registrationRetryTask: Task<Void, Never>?
    private var registrationRetryAttempt = 0
    private let registrationRetryDelays: [UInt64] = [1, 5, 30, 120]

    init(
        center: NotificationCenterClient = UNUserNotificationCenter.current(),
        initialRegisteredDeviceToken: String? = NetworkManager.shared.hasRegisteredAPNSDeviceForCurrentEnvironment
            ? NetworkManager.shared.registeredAPNSDeviceToken
            : nil,
        remoteRegistration: @escaping @MainActor () -> Void = {
            UIApplication.shared.registerForRemoteNotifications()
        }
    ) {
        self.center = center
        self.remoteRegistration = remoteRegistration
        self.lastRegisteredDeviceToken = initialRegisteredDeviceToken
        Task {
            await refreshAuthorizationStatus()
        }
    }

    var statusText: String {
        let i18n = I18nManager.shared
        switch authorizationStatus {
        case .authorized:
            return i18n.t("notifStatusEnabled")
        case .provisional:
            return i18n.t("notifStatusProvisional")
        case .denied:
            return i18n.t("notifStatusDenied")
        case .ephemeral:
            return i18n.t("notifStatusEphemeral")
        case .notDetermined:
            return i18n.t("notifStatusNotDetermined")
        @unknown default:
            return i18n.t("notifStatusUnknown")
        }
    }

    var canScheduleNotifications: Bool {
        authorizationStatus == .authorized || authorizationStatus == .provisional || authorizationStatus == .ephemeral
    }

    var hasRemoteDeviceToken: Bool {
        lastRegisteredDeviceToken != nil || NetworkManager.shared.hasRegisteredAPNSDeviceForCurrentEnvironment
    }

    var remoteDeviceTokenSuffix: String? {
        let token = lastRegisteredDeviceToken ?? NetworkManager.shared.registeredAPNSDeviceToken
        guard let token, !token.isEmpty else { return nil }
        return String(token.suffix(8))
    }

    func registerNotificationCategories() {
        let i18n = I18nManager.shared
        let openAction = UNNotificationAction(
            identifier: Self.openResultActionIdentifier,
            title: i18n.t("notifActionOpen"),
            options: [.foreground]
        )
        let saveAction = UNNotificationAction(
            identifier: Self.saveResultActionIdentifier,
            title: i18n.t("notifActionSave"),
            options: []
        )
        let previewCategory = UNNotificationCategory(
            identifier: Self.resultPreviewCategoryIdentifier,
            actions: [openAction, saveAction],
            intentIdentifiers: [],
            options: [.customDismissAction]
        )
        center.setNotificationCategories([previewCategory])
    }

    func refreshAuthorizationStatus() async {
        authorizationStatus = await center.authorizationStatus()
    }

    @discardableResult
    func requestAuthorization() async -> Bool {
        do {
            let granted = try await center.requestAuthorization(options: [.alert, .badge, .sound])
            await refreshAuthorizationStatus()
            await registerForRemoteNotificationsIfAllowed()
            return granted
        } catch {
            await refreshAuthorizationStatus()
            return false
        }
    }

    func registerForRemoteNotificationsIfAllowed() async {
        await refreshAuthorizationStatus()
        guard canScheduleNotifications else { return }
        registerForRemoteNotificationsForDeviceAuthentication()
    }

    func registerForRemoteNotificationsForDeviceAuthentication(
        resetRetryStateIfExhausted: Bool = true
    ) {
        // APNs registration does not present the notification permission prompt.
        // Keep a device credential available for watch-term synchronization even
        // when the user has not granted alert presentation permission.
        if resetRetryStateIfExhausted,
           registrationRetryTask == nil,
           registrationRetryAttempt >= registrationRetryDelays.count {
            resetRegistrationRetryState()
        }
        remoteRegistration()
    }

    private func scheduleRegistrationRetry() {
        guard registrationRetryTask == nil else { return }
        guard registrationRetryAttempt < registrationRetryDelays.count else {
            NetworkManager.shared.clearRegisteredAPNSDeviceToken()
            let summary = I18nManager.shared.t("notifRegistrationRetryExhausted")
            if let detail = lastRemoteRegistrationError, !detail.isEmpty {
                lastRemoteRegistrationError = "\(summary) \(detail)"
            } else {
                lastRemoteRegistrationError = summary
            }
            return
        }

        let delay = registrationRetryDelays[registrationRetryAttempt]
        registrationRetryAttempt += 1
        registrationRetryTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(nanoseconds: delay * 1_000_000_000)
            } catch {
                return
            }
            guard let self else { return }
            self.registrationRetryTask = nil
            self.registerForRemoteNotificationsForDeviceAuthentication(
                resetRetryStateIfExhausted: false
            )
        }
    }

    private func resetRegistrationRetryState() {
        registrationRetryTask?.cancel()
        registrationRetryTask = nil
        registrationRetryAttempt = 0
    }

    func ensureRemoteNotificationsRegisteredIfAllowed(
        timeout: TimeInterval = 8,
        forceRefresh: Bool = false,
        syncAfterVerification: Bool = true
    ) async -> Bool {
        await refreshAuthorizationStatus()
        // APNs device registration is independent of alert presentation
        // permission. A user can deny banners/sounds and still need a server
        // device identity for term ownership and background refresh. Calling
        // registerForRemoteNotifications does not show the permission prompt.
        if !forceRefresh, lastRegisteredDeviceToken != nil, lastRemoteRegistrationError == nil { return true }

        if forceRefresh {
            resetRegistrationRetryState()
            lastRemoteRegistrationError = nil

            // APNs may return the same token without immediately invoking the
            // delegate. Revalidate a cached token directly so a device whose
            // backend verification expired can recover when the user enables
            // notifications.
            // Unit tests use fake tokens and must continue through the injected
            // registration callback instead of making a live request.
            if !NetworkManager.shared.isUnitTesting,
               NetworkManager.shared.hasRegisteredAPNSDeviceForCurrentEnvironment,
               let cachedToken = NetworkManager.shared.registeredAPNSDeviceToken {
                do {
                    try await NetworkManager.shared.registerAPNSDeviceToken(cachedToken)
                    lastRegisteredDeviceToken = cachedToken
                    let shouldSyncWaiters = completeTokenRegistrationWaiters(success: true)
                    if syncAfterVerification || shouldSyncWaiters {
                        _ = await NetworkManager.shared.syncWatchTermsToBackend(
                            localTerms: LocalDB.shared.terms
                        )
                    }
                    return true
                } catch {
                    AppLogger.notifications.warning(
                        "Cached APNs token revalidation failed: \(error.localizedDescription)"
                    )
                }
            }
        }

        return await withCheckedContinuation { continuation in
            let id = UUID()
            tokenRegistrationWaiters[id] = TokenRegistrationWaiter(
                continuation: continuation,
                syncAfterVerification: syncAfterVerification
            )
            registerForRemoteNotificationsForDeviceAuthentication()

            Task { @MainActor in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                if let waiter = tokenRegistrationWaiters.removeValue(forKey: id) {
                    waiter.continuation.resume(returning: false)
                }
            }
        }
    }

    func resetRemoteNotificationRegistrationCache() {
        resetRegistrationRetryState()
        lastRegisteredDeviceToken = nil
        lastRemoteRegistrationError = nil
        NetworkManager.shared.clearRegisteredAPNSDeviceToken()
    }

    #if DEBUG || targetEnvironment(simulator)
    func waitForTokenRegistrationWaiterForTesting(timeout: TimeInterval = 2) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while tokenRegistrationWaiters.isEmpty {
            if Date() >= deadline { return false }
            await Task.yield()
        }
        return true
    }
    #endif

    nonisolated static func deviceTokenString(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    func handleRegisteredDeviceToken(_ deviceToken: Data) async {
        let token = Self.deviceTokenString(deviceToken)
        do {
            try await NetworkManager.shared.registerAPNSDeviceToken(token)
            resetRegistrationRetryState()
            lastRegisteredDeviceToken = token
            lastRemoteRegistrationError = nil
            let hadRegistrationWaiters = !tokenRegistrationWaiters.isEmpty
            let shouldSyncWaiters = completeTokenRegistrationWaiters(success: true)
            // An unsolicited AppDelegate callback should keep the normal automatic
            // ownership sync. When the callback is satisfying explicit preflight
            // waiters, however, honor their syncAfterVerification flags: notification
            // preference writes pass false because they immediately perform their own
            // targeted PATCH/create recovery and a nested full sync can race it.
            if !hadRegistrationWaiters || shouldSyncWaiters {
                _ = await NetworkManager.shared.syncWatchTermsToBackend(
                    localTerms: LocalDB.shared.terms
                )
            }
        } catch {
            AppLogger.notifications.error("APNs device token registration failed: \(error.localizedDescription)")
            lastRemoteRegistrationError = error.localizedDescription
            completeTokenRegistrationWaiters(success: false)
            scheduleRegistrationRetry()
        }
    }

    func handleRemoteNotificationRegistrationFailed(_ error: Error) {
        AppLogger.notifications.error("APNs registration failed: \(error.localizedDescription)")
        lastRemoteRegistrationError = error.localizedDescription
        completeTokenRegistrationWaiters(success: false)
        scheduleRegistrationRetry()
    }

    @discardableResult
    private func completeTokenRegistrationWaiters(success: Bool) -> Bool {
        let waiters = tokenRegistrationWaiters.values
        tokenRegistrationWaiters.removeAll()
        for waiter in waiters {
            waiter.continuation.resume(returning: success)
        }
        return waiters.contains { $0.syncAfterVerification }
    }

    func notifyForNewItems(
        _ items: [FeedItem],
        terms: [WatchTerm],
        includeAttachments: Bool = true
    ) async {
        guard !items.isEmpty else { return }
        await refreshAuthorizationStatus()
        guard canScheduleNotifications else { return }

        let notifiedKeywords = Set(terms.filter(\.notify_on_new).map(\.keyword))
        guard !notifiedKeywords.isEmpty else { return }

        let counts = Dictionary(grouping: items.filter { notifiedKeywords.contains($0.watch_term_keyword) }) {
            $0.watch_term_keyword
        }

        let i18n = I18nManager.shared
        for (keyword, keywordItems) in counts where !keywordItems.isEmpty {
            let count = keywordItems.count
            let previewItem = keywordItems.sorted {
                (parseISO8601Date($0.published_at) ?? .distantPast) >
                (parseISO8601Date($1.published_at) ?? .distantPast)
            }.first
            let content = UNMutableNotificationContent()
            content.title = i18n.tFormat("notifNewItemsTitle", keyword)
            content.body = notificationBody(for: previewItem, count: count)
            content.sound = .default
            content.categoryIdentifier = Self.resultPreviewCategoryIdentifier
            content.userInfo = notificationUserInfo(for: previewItem, keyword: keyword, count: count)
            content.threadIdentifier = notificationThreadIdentifier(for: keyword)
            if let previewItem {
                content.targetContentIdentifier = previewItem.id
            }
            if includeAttachments,
               let attachment = await notificationAttachment(for: previewItem) {
                content.attachments = [attachment]
            }

            let request = UNNotificationRequest(
                identifier: "oshireader-new-\(keyword)",
                content: content,
                trigger: nil
            )
            do {
                try await center.add(request)
            } catch {
                AppLogger.notifications.error("Notification scheduling failed for \(keyword): \(error.localizedDescription)")
            }
        }
    }

    private func notificationBody(for item: FeedItem?, count: Int) -> String {
        let i18n = I18nManager.shared
        let preview = cleanDisplayText(item?.title)
            ?? cleanDisplayText(item?.content_text)
            ?? item?.url
            ?? (count == 1 ? i18n.t("notifNewItemsBodyOne") : i18n.tFormat("notifNewItemsBodyMany", count))
        let limitedPreview = preview.count > 140 ? "\(preview.prefix(137))..." : preview
        guard count > 1 else { return limitedPreview }
        return "\(limitedPreview)\n\(i18n.tFormat("notifNewItemsMoreFmt", count - 1))"
    }

    private func notificationUserInfo(for item: FeedItem?, keyword: String, count: Int) -> [AnyHashable: Any] {
        var userInfo: [AnyHashable: Any] = [
            "watch_term_keyword": keyword,
            "new_count": count,
        ]
        guard let item else { return userInfo }
        userInfo["item_id"] = item.id
        userInfo["item_url"] = item.url
        userInfo["item_platform"] = item.platform
        userInfo["item_media_type"] = item.media_type
        userInfo["item_published_at"] = item.published_at
        if let source = item.source {
            userInfo["item_source"] = source
        }
        var previewItem: [String: Any] = [
            "id": item.id,
            "url": item.url,
            "platform": item.platform,
            "media_type": item.media_type,
            "published_at": item.published_at,
        ]
        if let source = item.source {
            previewItem["source"] = source
        }
        if let title = cleanDisplayText(item.title) {
            userInfo["item_title"] = title
            previewItem["title"] = title
        }
        if let contentText = cleanDisplayText(item.content_text) {
            userInfo["item_content_text"] = contentText
            previewItem["content_text"] = contentText
        }
        if let author = cleanDisplayText(item.author) {
            userInfo["item_author"] = author
            previewItem["author"] = author
        }
        if let thumbnail = item.thumbnail_url {
            userInfo["thumbnail_url"] = thumbnail
            previewItem["thumbnail_url"] = thumbnail
        }
        userInfo["preview_item"] = previewItem
        return userInfo
    }

    private func notificationThreadIdentifier(for keyword: String) -> String {
        let normalized = keyword
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        return normalized.isEmpty ? "oshireader-new-items" : "oshireader-\(normalized)"
    }

    private func notificationAttachment(for item: FeedItem?) async -> UNNotificationAttachment? {
        guard let rawURL = item?.thumbnail_url,
              let url = URL(string: rawURL),
              ["http", "https"].contains(url.scheme?.lowercased())
        else { return nil }

        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 5
            let (tempURL, response) = try await URLSession.shared.download(for: request)
            guard let http = response as? HTTPURLResponse,
                  (200...299).contains(http.statusCode),
                  http.mimeType?.lowercased().hasPrefix("image/") == true,
                  http.expectedContentLength <= 0 || http.expectedContentLength <= maximumAttachmentBytes,
                  notificationFileSize(at: tempURL) <= maximumAttachmentBytes
            else {
                return nil
            }

            let extensionHint = notificationAttachmentExtension(for: http, url: url)
            let destination = FileManager.default.temporaryDirectory
                .appendingPathComponent("oshireader-notification-\(UUID().uuidString).\(extensionHint)")
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: tempURL, to: destination)
            return try UNNotificationAttachment(identifier: "preview", url: destination)
        } catch {
            AppLogger.notifications.warning("Notification preview attachment failed: \(error.localizedDescription)")
            return nil
        }
    }

    private func notificationFileSize(at url: URL) -> Int64 {
        let values = try? url.resourceValues(forKeys: [.fileSizeKey])
        return Int64(values?.fileSize ?? Int.max)
    }

    private func notificationAttachmentExtension(for response: HTTPURLResponse, url: URL) -> String {
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
