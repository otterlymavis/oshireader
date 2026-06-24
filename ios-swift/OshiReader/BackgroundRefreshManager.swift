import BackgroundTasks
import Foundation
import UIKit

protocol BackgroundTaskSchedulerClient {
    func register(
        forTaskWithIdentifier identifier: String,
        using queue: DispatchQueue?,
        launchHandler: @escaping (BGTask) -> Void
    ) -> Bool
    func cancel(taskRequestWithIdentifier identifier: String)
    func submit(_ taskRequest: BGTaskRequest) throws
}

extension BGTaskScheduler: BackgroundTaskSchedulerClient {}

enum BackgroundRefreshPolicy {
    static let operationDeadline: TimeInterval = 25
    // Leave enough of iOS's short background execution window for syncing and
    // fetching the feed after the backend has made partial polling progress.
    static let pollTimeout: TimeInterval = 8
    static let incrementalFetchOverlap: TimeInterval = 15 * 60
    static let foregroundRefreshStaleAfter: TimeInterval = 5 * 60

    static func shouldScheduleLocalFallback(hasRegisteredRemoteDeviceForCurrentEnvironment: Bool) -> Bool {
        !hasRegisteredRemoteDeviceForCurrentEnvironment
    }

    static func shouldNotifyLocallyFromBackground(
        hasRegisteredRemoteDeviceForCurrentEnvironment: Bool,
        hadItemsInitially: Bool,
        hasPendingNotificationItems: Bool
    ) -> Bool {
        shouldScheduleLocalFallback(
            hasRegisteredRemoteDeviceForCurrentEnvironment: hasRegisteredRemoteDeviceForCurrentEnvironment
        ) &&
            hadItemsInitially &&
            hasPendingNotificationItems
    }

    static func notificationItems(
        incoming: [FeedItem],
        existingKeys: Set<String>,
        survivingKeys: Set<String>
    ) -> [FeedItem] {
        var itemsByKey: [String: FeedItem] = [:]
        for item in incoming {
            let key = itemKey(item)
            if !existingKeys.contains(key), survivingKeys.contains(key) {
                itemsByKey[key] = item
            }
        }
        return Array(itemsByKey.values)
    }

    static func itemKey(_ item: FeedItem) -> String {
        "\(item.id)::\(item.watch_term_keyword)"
    }

    static func shouldTriggerPoll(forRemoteNotification userInfo: [AnyHashable: Any]) -> Bool {
        if userInfo["new_count"] != nil ||
            userInfo["watch_term_keyword"] != nil ||
            userInfo["preview_item"] != nil {
            return false
        }
        return true
    }

    static func incrementalSince(
        in items: [FeedItem],
        platformId: String? = nil
    ) -> String? {
        items
            .filter { item in
                guard isBackendCursorCandidate(item) else { return false }
                guard let platformId else { return true }
                return Platform.normalize(item.platform) == platformId
            }
            .compactMap { parseISO8601Date($0.fetched_at) }
            .max()
            .map { latest in
                let overlapped = latest.addingTimeInterval(-incrementalFetchOverlap)
                return _ISO8601Cache.withoutFractional.string(from: overlapped)
            }
    }

    static func isBackendCursorCandidate(_ item: FeedItem) -> Bool {
        if Platform.normalize(item.platform) == "custom" { return false }
        if item.id.contains(":gnews:") { return false }
        if item.id.hasPrefix("news:nhk:") { return false }
        return true
    }

    static func shouldRefreshOnForeground(
        items: [FeedItem],
        now: Date = Date()
    ) -> Bool {
        guard !items.isEmpty else { return true }
        let latestFetchedAt = items
            .compactMap { parseISO8601Date($0.fetched_at) }
            .max()
        guard let latestFetchedAt else { return true }
        return now.timeIntervalSince(latestFetchedAt) >= foregroundRefreshStaleAfter
    }
}

enum BackgroundRefreshLiveTestProbe {
    static let resultKey = "live_background_refresh_test_result"

    static func reset() {
        guard NetworkManager.shared.isLiveBackgroundPushTesting else { return }
        UserDefaults.standard.removeObject(forKey: resultKey)
    }

    static func recordStarted() {
        guard NetworkManager.shared.isLiveBackgroundPushTesting else { return }
        UserDefaults.standard.set("started", forKey: resultKey)
    }

    static func recordCompleted(success: Bool) {
        guard NetworkManager.shared.isLiveBackgroundPushTesting else { return }
        if !success,
           let existing = UserDefaults.standard.string(forKey: resultKey),
           existing.hasPrefix("completed:failure:") {
            return
        }
        UserDefaults.standard.set(success ? "completed:success" : "completed:failure", forKey: resultKey)
    }

    static func recordCompleted(success: Bool, detail: String) {
        guard NetworkManager.shared.isLiveBackgroundPushTesting else { return }
        guard !success else {
            recordCompleted(success: true)
            return
        }
        let safeDetail = detail
            .replacingOccurrences(of: ":", with: "_")
            .replacingOccurrences(of: "\n", with: " ")
        UserDefaults.standard.set("completed:failure:\(safeDetail)", forKey: resultKey)
    }
}

@MainActor
final class BackgroundRefreshManager {
    static let shared = BackgroundRefreshManager()

    static let taskIdentifier = "com.otterpia.oshireader.plus.feed-refresh"
    static let minimumInterval: TimeInterval = 15 * 60

    private let scheduler: BackgroundTaskSchedulerClient
    private var isRegistered = false
    private var isRefreshing = false

    init(scheduler: BackgroundTaskSchedulerClient = BGTaskScheduler.shared) {
        self.scheduler = scheduler
    }

    func register() {
        guard !NetworkManager.shared.isUITesting, !isRegistered else { return }
        isRegistered = scheduler.register(
            forTaskWithIdentifier: Self.taskIdentifier,
            using: nil
        ) { task in
            guard let refreshTask = task as? BGAppRefreshTask else {
                task.setTaskCompleted(success: false)
                return
            }
            Task { @MainActor in
                await self.handle(task: refreshTask)
            }
        }
        if !isRegistered {
            AppLogger.network.warning("Background refresh registration failed for \(Self.taskIdentifier)")
        }
    }

    func schedule() {
        guard !NetworkManager.shared.isUITesting, isRegistered else { return }

        scheduler.cancel(taskRequestWithIdentifier: Self.taskIdentifier)

        let request = BGAppRefreshTaskRequest(identifier: Self.taskIdentifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: Self.minimumInterval)

        do {
            try scheduler.submit(request)
            AppLogger.network.info("Background refresh scheduled")
        } catch {
            AppLogger.network.warning("Background refresh schedule failed: \(error.localizedDescription)")
        }
    }

    private func handle(task: BGAppRefreshTask) async {
        AppLogger.network.notice("Background scheduled refresh started")
        schedule()

        let operation = Task { @MainActor in
            await refreshFromBackend()
        }

        task.expirationHandler = {
            operation.cancel()
        }

        let success = await operation.value
        AppLogger.network.notice("Background scheduled refresh completed success=\(success)")
        task.setTaskCompleted(success: success)
    }

    @discardableResult
    func refreshFromBackend(triggerPoll: Bool = true) async -> Bool {
        guard !isRefreshing else {
            AppLogger.network.notice("Background refresh skipped because another refresh is running")
            BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "busy")
            return false
        }
        AppLogger.network.notice("Background refresh started")
        isRefreshing = true
        defer {
            isRefreshing = false
            schedule()
            AppLogger.network.notice("Background refresh finished")
        }

        return await withTaskGroup(of: Bool.self) { group in
            group.addTask { @MainActor in
                await self.performRefreshFromBackend(triggerPoll: triggerPoll)
            }
            group.addTask {
                do {
                    try await Task.sleep(
                        nanoseconds: UInt64(BackgroundRefreshPolicy.operationDeadline * 1_000_000_000)
                    )
                } catch {
                    return false
                }
                BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "deadline")
                return false
            }

            let result = await group.next() ?? false
            group.cancelAll()
            return result
        }
    }

    private func performRefreshFromBackend(triggerPoll: Bool) async -> Bool {
        let db = LocalDB.shared

        if triggerPoll {
            await NetworkManager.shared.syncWatchTermsToBackend(localTerms: db.terms)
            guard !Task.isCancelled else {
                BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "cancelled_after_push_sync")
                return false
            }
            _ = await NetworkManager.shared.syncTermsFromBackend()
            guard !Task.isCancelled else {
                BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "cancelled_after_pull_sync")
                return false
            }

            guard !db.terms.filter(\.is_active).isEmpty else { return true }

            do {
                try await NetworkManager.shared.triggerBackgroundPoll(timeout: BackgroundRefreshPolicy.pollTimeout)
            } catch {
                AppLogger.network.warning("Background poll trigger failed: \(error.localizedDescription)")
            }
        }
        guard !Task.isCancelled else {
            BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "cancelled_after_poll")
            return false
        }

        let latestSince = latestFetchedAt(in: db.feedItems)
        let hadItemsInitially = !db.feedItems.isEmpty
        var pendingNotificationItems: [FeedItem] = []
        var pendingNotificationKeys = Set<String>()

        do {
            let items: [FeedItem]
            if let latestSince {
                items = try await NetworkManager.shared.fetchFeed(limit: 200, since: latestSince)
            } else {
                items = try await NetworkManager.shared.fetchFeed(limit: 120, days: 90)
            }
            guard !Task.isCancelled else {
                BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "cancelled_after_feed")
                return false
            }
            if !items.isEmpty {
                let existingKeys = Set(db.feedItems.map(BackgroundRefreshPolicy.itemKey))
                _ = db.mergeItems(newItems: items, notifyOnNew: false)
                appendNewNotificationItems(
                    from: items,
                    existingKeys: existingKeys,
                    db: db,
                    into: &pendingNotificationItems,
                    seenKeys: &pendingNotificationKeys
                )
            }

            let platformsToFetch = NetworkManager.shared.isLiveBackgroundPushTesting ? [] : db.subscribedPlatforms
                .filter { $0 != "custom" }
                .sorted { lhs, rhs in
                    let lhsHasItems = latestFetchedAt(in: db.feedItems, platformId: lhs) != nil
                    let rhsHasItems = latestFetchedAt(in: db.feedItems, platformId: rhs) != nil
                    if lhsHasItems != rhsHasItems { return !lhsHasItems }
                    return lhs < rhs
                }

            await withTaskGroup(of: [FeedItem].self) { group in
                for platform in platformsToFetch {
                    let platformSince = latestFetchedAt(in: db.feedItems, platformId: platform)
                    group.addTask {
                        do {
                            if let platformSince {
                                return try await NetworkManager.shared.fetchFeed(platform: platform, limit: 60, since: platformSince)
                            } else {
                                return try await NetworkManager.shared.fetchFeed(platform: platform, limit: 60, days: 30)
                            }
                        } catch {
                            AppLogger.network.warning("Background platform refresh \(platform) failed: \(error.localizedDescription)")
                            return []
                        }
                    }
                }

                for await platformItems in group where !platformItems.isEmpty {
                    guard !Task.isCancelled else { break }
                    let existingKeys = Set(db.feedItems.map(BackgroundRefreshPolicy.itemKey))
                    _ = db.mergeItems(newItems: platformItems, notifyOnNew: false)
                    appendNewNotificationItems(
                        from: platformItems,
                        existingKeys: existingKeys,
                        db: db,
                        into: &pendingNotificationItems,
                        seenKeys: &pendingNotificationKeys
                    )
                }
            }

            let shouldNotifyLocally = BackgroundRefreshPolicy.shouldNotifyLocallyFromBackground(
                hasRegisteredRemoteDeviceForCurrentEnvironment: NetworkManager.shared.hasRegisteredAPNSDeviceForCurrentEnvironment,
                hadItemsInitially: hadItemsInitially,
                hasPendingNotificationItems: !pendingNotificationItems.isEmpty
            )
            if shouldNotifyLocally {
                await NotificationManager.shared.notifyForNewItems(
                    pendingNotificationItems,
                    terms: db.terms,
                    includeAttachments: false
                )
            }
            return true
        } catch {
            AppLogger.network.warning("Background refresh failed: \(error.localizedDescription)")
            let detail = Self.refreshErrorKind(error)
            BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: detail)
            await sendLiveTestFailureDiagnostic(
                reason: "background_refresh_failed",
                detail: "\(detail): \(error.localizedDescription)",
                db: db
            )
            return false
        }
    }

    private func sendLiveTestFailureDiagnostic(reason: String, detail: String, db: LocalDB) async {
        guard NetworkManager.shared.isLiveBackgroundPushTesting else { return }
        let info = Bundle.main.infoDictionary
        let diagnostic = ClientDiagnosticReport(
            reason: reason,
            environment: NetworkManager.shared.environmentName,
            api_base: NetworkManager.shared.apiBase,
            app_version: info?["CFBundleShortVersionString"] as? String,
            build: info?["CFBundleVersion"] as? String,
            active_terms_count: db.terms.filter(\.is_active).count,
            subscribed_platforms: db.subscribedPlatforms,
            cached_feed_count: db.feedItems.count,
            events: [
                ClientDiagnosticEvent(
                    strategy: "background_refresh",
                    status: "failed",
                    item_count: 0,
                    added_count: 0,
                    detail: detail
                )
            ]
        )
        await NetworkManager.shared.sendClientDiagnostic(diagnostic)
    }

    private static func refreshErrorKind(_ error: Error) -> String {
        if error is DecodingError { return "decode" }
        guard let e = error as? URLError else { return "unknown" }
        switch e.code {
        case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed: return "offline"
        case .timedOut: return "timeout"
        case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed: return "unreachable"
        case .badServerResponse: return "server_error"
        default: return "url_\(e.code.rawValue)"
        }
    }

    private func appendNewNotificationItems(
        from incoming: [FeedItem],
        existingKeys: Set<String>,
        db: LocalDB,
        into pending: inout [FeedItem],
        seenKeys: inout Set<String>
    ) {
        let survivingKeys = Set(db.feedItems.map(BackgroundRefreshPolicy.itemKey))
        let notificationItems = BackgroundRefreshPolicy.notificationItems(
            incoming: incoming,
            existingKeys: existingKeys,
            survivingKeys: survivingKeys
        )
        for item in notificationItems {
            let key = BackgroundRefreshPolicy.itemKey(item)
            guard seenKeys.insert(key).inserted else { continue }
            pending.append(item)
        }
    }

    private func latestFetchedAt(in items: [FeedItem], platformId: String? = nil) -> String? {
        BackgroundRefreshPolicy.incrementalSince(in: items, platformId: platformId)
    }
}
