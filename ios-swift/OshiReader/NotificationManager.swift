import Foundation
import UIKit
import UserNotifications

protocol NotificationCenterClient {
    func authorizationStatus() async -> UNAuthorizationStatus
    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool
    func add(_ request: UNNotificationRequest) async throws
}

extension UNUserNotificationCenter: NotificationCenterClient {
    func authorizationStatus() async -> UNAuthorizationStatus {
        await notificationSettings().authorizationStatus
    }
}

@MainActor
final class NotificationManager: ObservableObject {
    static let shared = NotificationManager()

    @Published private(set) var authorizationStatus: UNAuthorizationStatus = .notDetermined

    private let center: NotificationCenterClient

    init(center: NotificationCenterClient = UNUserNotificationCenter.current()) {
        self.center = center
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

    func sendTestNotification() async throws {
        if !canScheduleNotifications {
            _ = await requestAuthorization()
        }
        guard canScheduleNotifications else { return }

        let i18n = I18nManager.shared
        let content = UNMutableNotificationContent()
        content.title = "OshiReader"
        content.body = i18n.t("notifTestBody")
        content.sound = .default

        let request = UNNotificationRequest(
            identifier: "oshireader-test-\(UUID().uuidString)",
            content: content,
            trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        )
        try await center.add(request)
    }

    func registerForRemoteNotificationsIfAllowed() async {
        await refreshAuthorizationStatus()
        guard canScheduleNotifications else { return }
        await MainActor.run {
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    nonisolated static func deviceTokenString(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    func handleRegisteredDeviceToken(_ deviceToken: Data) async {
        let token = Self.deviceTokenString(deviceToken)
        do {
            try await NetworkManager.shared.registerAPNSDeviceToken(token)
        } catch {
            AppLogger.notifications.error("APNs device token registration failed: \(error.localizedDescription)")
        }
    }

    func notifyForNewItems(_ items: [FeedItem], terms: [WatchTerm]) async {
        guard !items.isEmpty else { return }
        await refreshAuthorizationStatus()
        guard canScheduleNotifications else { return }

        let notifiedKeywords = Set(terms.filter(\.notify_on_new).map(\.keyword))
        guard !notifiedKeywords.isEmpty else { return }

        let counts = Dictionary(grouping: items.filter { notifiedKeywords.contains($0.watch_term_keyword) }) {
            $0.watch_term_keyword
        }.mapValues(\.count)

        let i18n = I18nManager.shared
        for (keyword, count) in counts where count > 0 {
            let content = UNMutableNotificationContent()
            content.title = i18n.tFormat("notifNewItemsTitle", keyword)
            content.body = count == 1
                ? i18n.t("notifNewItemsBodyOne")
                : i18n.tFormat("notifNewItemsBodyMany", count)
            content.sound = .default

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
}
