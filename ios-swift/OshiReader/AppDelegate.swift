import UIKit
import UserNotifications

@MainActor
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        NotificationManager.shared.registerNotificationCategories()
        BackgroundRefreshLiveTestProbe.reset()
        if !NetworkManager.shared.isUnitTesting {
            application.setMinimumBackgroundFetchInterval(BackgroundRefreshManager.minimumInterval)
            BackgroundRefreshManager.shared.register()
            BackgroundRefreshManager.shared.schedule()
            if !NetworkManager.shared.isUITesting, NetworkManager.shared.usesBackend {
                NotificationManager.shared.registerForRemoteNotificationsForDeviceAuthentication()
            }
        }
        return true
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        guard !NetworkManager.shared.isUnitTesting else { return }
        BackgroundRefreshManager.shared.schedule()
    }

    func application(
        _ application: UIApplication,
        performFetchWithCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void
    ) {
        AppLogger.network.notice("Legacy background fetch started")
        Task { @MainActor in
            let refreshed = await BackgroundRefreshManager.shared.refreshFromBackend()
            AppLogger.network.notice("Legacy background fetch completed success=\(refreshed)")
            completionHandler(refreshed ? .newData : .failed)
        }
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        guard !NetworkManager.shared.isUnitTesting else { return }
        Task {
            await NotificationManager.shared.handleRegisteredDeviceToken(deviceToken)
        }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        guard !NetworkManager.shared.isUnitTesting else { return }
        NotificationManager.shared.handleRemoteNotificationRegistrationFailed(error)
    }

    func application(
        _ application: UIApplication,
        didReceiveRemoteNotification userInfo: [AnyHashable: Any],
        fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void
    ) {
        AppLogger.network.notice("Silent push requested a background refresh")
        BackgroundRefreshLiveTestProbe.recordStarted()
        Task { @MainActor in
            let shouldTriggerPoll = BackgroundRefreshPolicy.shouldTriggerPoll(forRemoteNotification: userInfo)
            let refreshed = await BackgroundRefreshManager.shared.refreshFromBackend(triggerPoll: shouldTriggerPoll)
            BackgroundRefreshLiveTestProbe.recordCompleted(success: refreshed)
            AppLogger.network.notice("Silent push background refresh completed success=\(refreshed)")
            completionHandler(refreshed ? .newData : .failed)
        }
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .list, .sound, .badge])
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        Task { @MainActor in
            let userInfo = response.notification.request.content.userInfo
            switch response.actionIdentifier {
            case NotificationManager.saveResultActionIdentifier:
                NotificationNavigationManager.shared.saveNotificationItem(userInfo: userInfo)
            case NotificationManager.openResultActionIdentifier, UNNotificationDefaultActionIdentifier:
                NotificationNavigationManager.shared.openNotification(userInfo: userInfo)
            default:
                break
            }
            completionHandler()
        }
    }
}
