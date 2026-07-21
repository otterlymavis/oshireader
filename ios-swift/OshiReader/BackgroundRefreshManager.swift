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
    struct SourceScope: Equatable, Sendable {
        let needsBackend: Bool
        let needsCustom: Bool
        let needsDevice: Bool

        var needsLocal: Bool {
            needsCustom || needsDevice
        }

        var hasRefreshableSources: Bool {
            needsBackend || needsLocal
        }

        func isCovered(
            completedBackendPlatforms: Set<String>,
            completedDevicePlatforms: Set<String>,
            customRefreshed: Bool,
            subscribedPlatforms: [String]
        ) -> Bool {
            let backendPlatforms = BackgroundRefreshPolicy.backendPlatformsNeedingRefresh(in: subscribedPlatforms)
            let devicePlatforms = BackgroundRefreshPolicy.fallbackPlatformsNeedingDevice(in: subscribedPlatforms)
            return (!needsBackend || backendPlatforms.isSubset(of: completedBackendPlatforms)) &&
                (!needsCustom || customRefreshed) &&
                (!needsDevice || devicePlatforms.isSubset(of: completedDevicePlatforms))
        }
    }

    static let operationDeadline: TimeInterval = 25
    // Leave enough of iOS's short background execution window for syncing and
    // fetching the feed after the backend has made partial polling progress.
    static let pollTimeout: TimeInterval = 8
    static let incrementalFetchOverlap: TimeInterval = 15 * 60
    static let foregroundRefreshStaleAfter: TimeInterval = 5 * 60
    static let minimumLocalRefreshWindow: TimeInterval = 3
    private static let lastForegroundCompletedRefreshAtKey = "background_refresh.foreground_completed_at"
    private static let lastBackendCompletedRefreshAtKey = "background_refresh.backend_completed_at"
    private static let feedScopeNeedsRefreshKey = "background_refresh.feed_scope_needs_refresh"
    private static let feedScopeRevisionKey = "background_refresh.feed_scope_revision"

    static var lastCompletedRefreshAt: Date? {
        lastForegroundRefreshCompletedAt
    }

    static var currentFeedScopeRevision: Int {
        UserDefaults.standard.integer(forKey: feedScopeRevisionKey)
    }

    private static var lastForegroundRefreshCompletedAt: Date? {
        date(forKey: lastForegroundCompletedRefreshAtKey)
    }

    private static var lastBackendRefreshCompletedAt: Date? {
        date(forKey: lastBackendCompletedRefreshAtKey)
    }

    private static var lastAnyRefreshCompletedAt: Date? {
        [lastForegroundRefreshCompletedAt, lastBackendRefreshCompletedAt]
            .compactMap { $0 }
            .max()
    }

    private static func date(forKey key: String) -> Date? {
        let timestamp = UserDefaults.standard.double(forKey: key)
        return timestamp > 0 ? Date(timeIntervalSince1970: timestamp) : nil
    }

    static func recordRefreshCompleted(
        at date: Date = Date(),
        feedScopeRevision: Int? = nil
    ) {
        recordForegroundRefreshCompleted(at: date, feedScopeRevision: feedScopeRevision)
    }

    static func recordForegroundRefreshCompleted(
        at date: Date = Date(),
        feedScopeRevision: Int? = nil
    ) {
        guard feedScopeRevision == nil || feedScopeRevision == currentFeedScopeRevision else {
            AppLogger.network.notice("Skipping stale feed-scope completion marker")
            return
        }
        UserDefaults.standard.set(date.timeIntervalSince1970, forKey: lastForegroundCompletedRefreshAtKey)
        UserDefaults.standard.set(false, forKey: feedScopeNeedsRefreshKey)
    }

    static func recordBackendRefreshCompleted(at date: Date = Date()) {
        UserDefaults.standard.set(date.timeIntervalSince1970, forKey: lastBackendCompletedRefreshAtKey)
    }

    static func recordBackgroundRefreshCompleted(
        completedBackendPlatforms: Set<String>,
        customRefreshed: Bool,
        completedDevicePlatforms: Set<String>,
        activeTerms: [WatchTerm],
        customUrls: [CustomUrl],
        subscribedPlatforms: [String],
        feedScopeRevision: Int? = nil,
        at date: Date = Date()
    ) {
        let scope = sourceScope(
            activeTerms: activeTerms,
            customUrls: customUrls,
            subscribedPlatforms: subscribedPlatforms
        )
        let backendPlatforms = backendPlatformsNeedingRefresh(in: subscribedPlatforms)
        if !backendPlatforms.isEmpty,
           backendPlatforms.isSubset(of: completedBackendPlatforms) {
            recordBackendRefreshCompleted(at: date)
        }
        guard scope.hasRefreshableSources else {
            recordForegroundRefreshCompleted(at: date, feedScopeRevision: feedScopeRevision)
            return
        }
        guard scope.isCovered(
                completedBackendPlatforms: completedBackendPlatforms,
                completedDevicePlatforms: completedDevicePlatforms,
                customRefreshed: customRefreshed,
                subscribedPlatforms: subscribedPlatforms
              ) else {
            return
        }
        recordForegroundRefreshCompleted(at: date, feedScopeRevision: feedScopeRevision)
    }

    static func invalidateRefreshCompletionsForFeedScopeChange() {
        clearRecordedRefreshCompletions()
        UserDefaults.standard.set(currentFeedScopeRevision + 1, forKey: feedScopeRevisionKey)
        UserDefaults.standard.set(true, forKey: feedScopeNeedsRefreshKey)
    }

    static func clearRecordedRefreshCompletionsForTesting() {
        clearRecordedRefreshCompletions()
        UserDefaults.standard.removeObject(forKey: feedScopeRevisionKey)
    }

    private static func clearRecordedRefreshCompletions() {
        UserDefaults.standard.removeObject(forKey: lastForegroundCompletedRefreshAtKey)
        UserDefaults.standard.removeObject(forKey: lastBackendCompletedRefreshAtKey)
        UserDefaults.standard.removeObject(forKey: feedScopeNeedsRefreshKey)
    }

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

    static func shouldUseDateWindowForPlatformRefresh(_ platformId: String) -> Bool {
        Platform.shouldUseDateWindowRefresh(platformId)
    }

    static func shouldTriggerPoll(forRemoteNotification userInfo: [AnyHashable: Any]) -> Bool {
        if userInfo["new_count"] != nil ||
            userInfo["watch_term_keyword"] != nil ||
            userInfo["preview_item"] != nil {
            return false
        }
        return true
    }

    static func shouldMergePreviewBeforeRefresh(forRemoteNotification userInfo: [AnyHashable: Any]) -> Bool {
        userInfo["preview_item"] != nil ||
            userInfo["item_id"] != nil ||
            userInfo["item_url"] != nil
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
        now: Date = Date(),
        lastRefreshAt: Date? = lastForegroundRefreshCompletedAt
    ) -> Bool {
        if UserDefaults.standard.bool(forKey: feedScopeNeedsRefreshKey) { return true }
        return shouldRefreshCachedFeed(items: items, now: now, lastRefreshAt: lastRefreshAt)
    }

    static func shouldRefreshBeforeSuspension(
        items: [FeedItem],
        now: Date = Date(),
        lastRefreshAt: Date? = lastAnyRefreshCompletedAt
    ) -> Bool {
        if UserDefaults.standard.bool(forKey: feedScopeNeedsRefreshKey) { return true }
        return shouldRefreshCachedFeed(items: items, now: now, lastRefreshAt: lastRefreshAt)
    }

    static func shouldRefreshBeforeSuspension(
        activeTerms: [WatchTerm],
        customUrls: [CustomUrl],
        subscribedPlatforms: [String],
        items: [FeedItem],
        now: Date = Date(),
        lastRefreshAt: Date? = lastForegroundRefreshCompletedAt,
        lastBackendRefreshAt: Date? = lastBackendRefreshCompletedAt
    ) -> Bool {
        if UserDefaults.standard.bool(forKey: feedScopeNeedsRefreshKey) { return true }
        return shouldRefreshSourceScope(
            activeTerms: activeTerms,
            customUrls: customUrls,
            subscribedPlatforms: subscribedPlatforms,
            items: items,
            now: now,
            lastRefreshAt: lastRefreshAt,
            lastBackendRefreshAt: lastBackendRefreshAt
        )
    }

    static func shouldLaunchForegroundRefresh(
        activeTerms: [WatchTerm],
        customUrls: [CustomUrl],
        subscribedPlatforms: [String],
        items: [FeedItem],
        pulledNewTerms: Bool,
        now: Date = Date(),
        lastRefreshAt: Date? = lastForegroundRefreshCompletedAt,
        lastBackendRefreshAt: Date? = lastBackendRefreshCompletedAt
    ) -> Bool {
        let hasRefreshableScope = !activeTerms.isEmpty ||
            pulledNewTerms ||
            !customUrls.isEmpty ||
            !items.isEmpty
        guard hasRefreshableScope else { return false }
        if UserDefaults.standard.bool(forKey: feedScopeNeedsRefreshKey) { return true }
        return shouldRefreshSourceScope(
            activeTerms: activeTerms,
            customUrls: customUrls,
            subscribedPlatforms: subscribedPlatforms,
            items: items,
            now: now,
            lastRefreshAt: lastRefreshAt,
            lastBackendRefreshAt: lastBackendRefreshAt
        )
    }

    static func shouldLaunchForegroundRefresh(
        activeTerms: [WatchTerm],
        customUrls: [CustomUrl],
        items: [FeedItem],
        pulledNewTerms: Bool,
        now: Date = Date(),
        lastRefreshAt: Date? = lastForegroundRefreshCompletedAt
    ) -> Bool {
        let inferredPlatforms = Array(Set(items.map { Platform.normalize($0.platform) }))
        return shouldLaunchForegroundRefresh(
            activeTerms: activeTerms,
            customUrls: customUrls,
            subscribedPlatforms: inferredPlatforms,
            items: items,
            pulledNewTerms: pulledNewTerms,
            now: now,
            lastRefreshAt: lastRefreshAt,
            lastBackendRefreshAt: nil
        )
    }

    static func fallbackPlatformsNeedingDevice(in subscribedPlatforms: [String]) -> Set<String> {
        Set(subscribedPlatforms.compactMap { platformId -> String? in
            let normalized = Platform.normalize(platformId)
            return Platform.shouldRunDeviceFallback(normalized) ? normalized : nil
        })
    }

    static func completedDeviceFallbackPlatforms(
        from items: [FeedItem],
        subscribedPlatforms: [String]
    ) -> Set<String> {
        let fallbackPlatforms = fallbackPlatformsNeedingDevice(in: subscribedPlatforms)
        guard !fallbackPlatforms.isEmpty else { return [] }
        return Set(items.compactMap { item in
            let platform = Platform.normalize(item.platform)
            return fallbackPlatforms.contains(platform) ? platform : nil
        })
    }

    static func completedDeviceFallbackPlatforms(
        from scrapeResult: LocalFallbackScrapeResult,
        subscribedPlatforms: [String]
    ) -> Set<String> {
        let fallbackPlatforms = fallbackPlatformsNeedingDevice(in: subscribedPlatforms)
        guard !fallbackPlatforms.isEmpty else { return [] }
        return Set(scrapeResult.completedPlatforms.compactMap { platformId in
            let platform = Platform.normalize(platformId)
            return fallbackPlatforms.contains(platform) ? platform : nil
        })
    }

    static func completedDeviceFallbackPlatformsForAllSearches(
        from scrapeResults: [LocalFallbackScrapeResult],
        subscribedPlatforms: [String]
    ) -> Set<String> {
        let fallbackPlatforms = fallbackPlatformsNeedingDevice(in: subscribedPlatforms)
        guard !fallbackPlatforms.isEmpty, !scrapeResults.isEmpty else { return [] }
        return scrapeResults.reduce(fallbackPlatforms) { completed, result in
            completed.intersection(
                completedDeviceFallbackPlatforms(
                    from: result,
                    subscribedPlatforms: subscribedPlatforms
                )
            )
        }
    }

    static func sourceScope(
        activeTerms: [WatchTerm],
        customUrls: [CustomUrl],
        subscribedPlatforms: [String]
    ) -> SourceScope {
        let hasActiveTerms = !activeTerms.isEmpty
        let needsBackend = hasActiveTerms && subscribedPlatforms.contains {
            Platform.shouldFetchFromBackend($0)
        }
        let needsDevice = hasActiveTerms && !fallbackPlatformsNeedingDevice(in: subscribedPlatforms).isEmpty
        return SourceScope(
            needsBackend: needsBackend,
            needsCustom: !customUrls.isEmpty,
            needsDevice: needsDevice
        )
    }

    static func remainingOperationTime(
        startedAt: Date,
        now: Date = Date()
    ) -> TimeInterval {
        max(0, operationDeadline - now.timeIntervalSince(startedAt))
    }

    static func shouldStartLocalBackgroundRefresh(remainingTime: TimeInterval) -> Bool {
        remainingTime >= minimumLocalRefreshWindow
    }

    static func shouldStartForegroundDeviceRefresh(
        cacheIsEmpty: Bool,
        elapsedSinceLastDeviceScrape: TimeInterval,
        throttle: TimeInterval
    ) -> Bool {
        cacheIsEmpty ||
            UserDefaults.standard.bool(forKey: feedScopeNeedsRefreshKey) ||
            elapsedSinceLastDeviceScrape > throttle
    }

    private static func shouldRefreshSourceScope(
        activeTerms: [WatchTerm],
        customUrls: [CustomUrl],
        subscribedPlatforms: [String],
        items: [FeedItem],
        now: Date,
        lastRefreshAt: Date?,
        lastBackendRefreshAt: Date?
    ) -> Bool {
        if let lastRefreshAt, now.timeIntervalSince(lastRefreshAt) < foregroundRefreshStaleAfter {
            return false
        }

        let scope = sourceScope(
            activeTerms: activeTerms,
            customUrls: customUrls,
            subscribedPlatforms: subscribedPlatforms
        )
        guard scope.hasRefreshableSources else {
            return shouldRefreshCachedFeed(items: items, now: now, lastRefreshAt: nil)
        }

        let backendPlatforms = backendPlatformsNeedingRefresh(in: subscribedPlatforms)
        let devicePlatforms = fallbackPlatformsNeedingDevice(in: subscribedPlatforms)
        if scope.needsBackend,
           !isRecent(lastBackendRefreshAt, now: now),
           !allActiveTermsHaveRecentFetchedItems(
                backendPlatforms,
                activeTerms: activeTerms,
                in: items,
                now: now
           ) {
            return true
        }
        if scope.needsCustom,
           !hasRecentFetchedItem(in: items, now: now, where: { Platform.normalize($0) == "custom" }) {
            return true
        }
        if scope.needsDevice,
           !allActiveTermsHaveRecentFetchedItems(
                devicePlatforms,
                activeTerms: activeTerms,
                in: items,
                now: now
           ) {
            return true
        }
        return false
    }

    static func backendPlatformsNeedingRefresh(in subscribedPlatforms: [String]) -> Set<String> {
        Set(subscribedPlatforms.compactMap { platformId -> String? in
            let normalized = Platform.normalize(platformId)
            return Platform.shouldFetchFromBackend(normalized) ? normalized : nil
        })
    }

    private static func allActiveTermsHaveRecentFetchedItems(
        _ platforms: Set<String>,
        activeTerms: [WatchTerm],
        in items: [FeedItem],
        now: Date
    ) -> Bool {
        guard !platforms.isEmpty else { return true }
        let activeKeywords = Set(activeTerms.map(\.keyword))
        guard !activeKeywords.isEmpty else { return true }
        let recentPairs = Set(
            items.compactMap { item -> String? in
                guard activeKeywords.contains(item.watch_term_keyword),
                      let fetchedAt = parseISO8601Date(item.fetched_at),
                      now.timeIntervalSince(fetchedAt) < foregroundRefreshStaleAfter else { return nil }
                let platform = Platform.normalize(item.platform)
                guard platforms.contains(platform) else { return nil }
                return "\(platform)\u{1F}\(item.watch_term_keyword)"
            }
        )
        return platforms.allSatisfy { platform in
            activeKeywords.allSatisfy { keyword in
                recentPairs.contains("\(platform)\u{1F}\(keyword)")
            }
        }
    }

    private static func hasRecentFetchedItem(
        in items: [FeedItem],
        now: Date,
        where belongsToSource: (String) -> Bool
    ) -> Bool {
        items
            .filter { belongsToSource(Platform.normalize($0.platform)) }
            .compactMap { parseISO8601Date($0.fetched_at) }
            .contains { now.timeIntervalSince($0) < foregroundRefreshStaleAfter }
    }

    private static func isRecent(_ date: Date?, now: Date) -> Bool {
        guard let date else { return false }
        return now.timeIntervalSince(date) < foregroundRefreshStaleAfter
    }

    private static func shouldRefreshCachedFeed(
        items: [FeedItem],
        now: Date,
        lastRefreshAt: Date?
    ) -> Bool {
        if let lastRefreshAt, now.timeIntervalSince(lastRefreshAt) < foregroundRefreshStaleAfter {
            return false
        }
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
    private struct BackendSourceRefreshCompletion: Sendable {
        var refreshed = false
        var completedPlatforms = Set<String>()
        var feedScopeRevision: Int?
    }

    private struct BackendPlatformRefreshResult: Sendable {
        let platform: String
        let items: [FeedItem]
        let completed: Bool
    }

    private struct LocalSourceRefreshCompletion: Sendable {
        var customRefreshed = false
        var completedDevicePlatforms = Set<String>()

        var completedAnySource: Bool {
            customRefreshed || !completedDevicePlatforms.isEmpty
        }
    }

    static let shared = BackgroundRefreshManager()

    static let taskIdentifier = "com.otterpia.oshireader.plus.feed-refresh"
    static let minimumInterval: TimeInterval = 15 * 60

    private let scheduler: BackgroundTaskSchedulerClient
    private var isRegistered = false
    private var isRefreshing = false
    private var suspensionRefreshTask: Task<Void, Never>?
    private var suspensionBackgroundTaskIdentifier: UIBackgroundTaskIdentifier = .invalid

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

    func refreshBeforeSuspensionIfNeeded(application: UIApplication) {
        guard !NetworkManager.shared.isUnitTesting,
              !NetworkManager.shared.isUITesting else { return }
        let db = LocalDB.shared
        guard BackgroundRefreshPolicy.shouldRefreshBeforeSuspension(
            activeTerms: db.terms.filter(\.is_active),
            customUrls: db.customUrls,
            subscribedPlatforms: db.subscribedPlatforms,
            items: db.feedItems
        ) else {
            AppLogger.network.debug("Skipping suspension refresh because cached feed is still fresh")
            return
        }
        guard suspensionRefreshTask == nil else {
            AppLogger.network.notice("Suspension refresh skipped because one is already running")
            return
        }

        var identifier: UIBackgroundTaskIdentifier = .invalid
        identifier = application.beginBackgroundTask(withName: "OshiReader.feed-refresh") { [weak self, weak application] in
            Task { @MainActor in
                guard let self, let application else { return }
                self.suspensionRefreshTask?.cancel()
                self.finishSuspensionRefresh(application: application, identifier: identifier)
            }
        }
        guard identifier != .invalid else {
            AppLogger.network.warning("Unable to begin suspension refresh background task")
            return
        }

        AppLogger.network.notice("Suspension refresh started")
        suspensionBackgroundTaskIdentifier = identifier
        suspensionRefreshTask = Task { @MainActor in
            _ = await self.refreshForBackground()
            self.finishSuspensionRefresh(application: application, identifier: identifier)
        }
    }

    private func handle(task: BGAppRefreshTask) async {
        AppLogger.network.notice("Background scheduled refresh started")
        schedule()

        let operation = Task { @MainActor in
            await refreshForBackground()
        }

        task.expirationHandler = {
            operation.cancel()
        }

        let success = await operation.value
        AppLogger.network.notice("Background scheduled refresh completed success=\(success)")
        task.setTaskCompleted(success: success)
    }

    private func finishSuspensionRefresh(
        application: UIApplication,
        identifier: UIBackgroundTaskIdentifier
    ) {
        guard suspensionBackgroundTaskIdentifier == identifier else { return }
        suspensionRefreshTask = nil
        suspensionBackgroundTaskIdentifier = .invalid
        application.endBackgroundTask(identifier)
        AppLogger.network.notice("Suspension refresh finished")
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

        let completion = await refreshBackendWithinDeadline(triggerPoll: triggerPoll)
        let backendPlatforms = BackgroundRefreshPolicy.backendPlatformsNeedingRefresh(
            in: LocalDB.shared.subscribedPlatforms
        )
        if !backendPlatforms.isEmpty,
           backendPlatforms.isSubset(of: completion.completedPlatforms) {
            BackgroundRefreshPolicy.recordBackendRefreshCompleted()
        }
        return completion.refreshed
    }

    @discardableResult
    func refreshForBackground(
        triggerPoll: Bool = true,
        notifyOnNew: Bool = true,
        skipTermSync: Bool = false,
        feedScopeRevision: Int? = nil
    ) async -> Bool {
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

        let startedAt = Date()
        let backendRefresh = await refreshBackendWithinDeadline(
            triggerPoll: triggerPoll,
            notifyOnNew: notifyOnNew,
            skipTermSync: skipTermSync,
            feedScopeRevision: feedScopeRevision
        )
        guard !Task.isCancelled else { return backendRefresh.refreshed }
        let feedScopeRevision = backendRefresh.feedScopeRevision ?? BackgroundRefreshPolicy.currentFeedScopeRevision

        let activeTerms = LocalDB.shared.terms.filter(\.is_active)
        let customUrls = LocalDB.shared.customUrls
        let subscribedPlatforms = LocalDB.shared.subscribedPlatforms
        let scope = BackgroundRefreshPolicy.sourceScope(
            activeTerms: activeTerms,
            customUrls: customUrls,
            subscribedPlatforms: subscribedPlatforms
        )

        guard scope.needsLocal else {
            BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
                completedBackendPlatforms: backendRefresh.completedPlatforms,
                customRefreshed: false,
                completedDevicePlatforms: [],
                activeTerms: activeTerms,
                customUrls: customUrls,
                subscribedPlatforms: subscribedPlatforms,
                feedScopeRevision: feedScopeRevision
            )
            return backendRefresh.refreshed
        }

        let remainingTime = BackgroundRefreshPolicy.remainingOperationTime(startedAt: startedAt)
        guard BackgroundRefreshPolicy.shouldStartLocalBackgroundRefresh(remainingTime: remainingTime) else {
            AppLogger.network.notice("Skipping local background refresh because the operation deadline is near")
            BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
                completedBackendPlatforms: backendRefresh.completedPlatforms,
                customRefreshed: false,
                completedDevicePlatforms: [],
                activeTerms: activeTerms,
                customUrls: customUrls,
                subscribedPlatforms: subscribedPlatforms,
                feedScopeRevision: feedScopeRevision
            )
            return backendRefresh.refreshed
        }

        let localRefresh = await refreshLocalDeviceSourcesForBackground(timeout: remainingTime)
        BackgroundRefreshPolicy.recordBackgroundRefreshCompleted(
            completedBackendPlatforms: backendRefresh.completedPlatforms,
            customRefreshed: localRefresh.customRefreshed,
            completedDevicePlatforms: localRefresh.completedDevicePlatforms,
            activeTerms: LocalDB.shared.terms.filter(\.is_active),
            customUrls: LocalDB.shared.customUrls,
            subscribedPlatforms: LocalDB.shared.subscribedPlatforms,
            feedScopeRevision: feedScopeRevision
        )
        return backendRefresh.refreshed || localRefresh.completedAnySource
    }

    private func refreshBackendWithinDeadline(
        triggerPoll: Bool,
        notifyOnNew: Bool = true,
        skipTermSync: Bool = false,
        feedScopeRevision: Int? = nil
    ) async -> BackendSourceRefreshCompletion {
        await withTaskGroup(of: BackendSourceRefreshCompletion.self) { group in
            group.addTask { @MainActor in
                await self.performRefreshFromBackend(
                    triggerPoll: triggerPoll,
                    notifyOnNew: notifyOnNew,
                    skipTermSync: skipTermSync,
                    feedScopeRevision: feedScopeRevision
                )
            }
            group.addTask {
                do {
                    try await Task.sleep(
                        nanoseconds: UInt64(BackgroundRefreshPolicy.operationDeadline * 1_000_000_000)
                    )
                } catch {
                    return BackendSourceRefreshCompletion()
                }
                BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "deadline")
                return BackendSourceRefreshCompletion()
            }

            let result = await group.next() ?? BackendSourceRefreshCompletion()
            group.cancelAll()
            return result
        }
    }

    private func performRefreshFromBackend(
        triggerPoll: Bool,
        notifyOnNew: Bool = true,
        skipTermSync: Bool = false,
        feedScopeRevision: Int? = nil
    ) async -> BackendSourceRefreshCompletion {
        let db = LocalDB.shared
        var completion = BackendSourceRefreshCompletion()

        if triggerPoll {
            let currentFeedScopeRevision = BackgroundRefreshPolicy.currentFeedScopeRevision
            if skipTermSync,
               let feedScopeRevision,
               feedScopeRevision == currentFeedScopeRevision {
                completion.feedScopeRevision = currentFeedScopeRevision
            } else {
                await NetworkManager.shared.syncWatchTermsToBackend(localTerms: db.terms)
                guard !Task.isCancelled else {
                    BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "cancelled_after_push_sync")
                    return BackendSourceRefreshCompletion()
                }
                _ = await NetworkManager.shared.syncTermsFromBackend()
                guard !Task.isCancelled else {
                    BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "cancelled_after_pull_sync")
                    return BackendSourceRefreshCompletion()
                }
                completion.feedScopeRevision = BackgroundRefreshPolicy.currentFeedScopeRevision
            }

            guard !db.terms.filter(\.is_active).isEmpty else {
                completion.refreshed = true
                return completion
            }

            do {
                try await NetworkManager.shared.triggerBackgroundPoll(timeout: BackgroundRefreshPolicy.pollTimeout)
            } catch {
                AppLogger.network.warning("Background poll trigger failed: \(error.localizedDescription)")
            }
        } else {
            completion.feedScopeRevision = BackgroundRefreshPolicy.currentFeedScopeRevision
        }
        guard !Task.isCancelled else {
            BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "cancelled_after_poll")
            return BackendSourceRefreshCompletion()
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
                return BackendSourceRefreshCompletion()
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
                .filter { Platform.shouldFetchFromBackend($0) }
                .sorted { lhs, rhs in
                    let lhsHasItems = latestFetchedAt(in: db.feedItems, platformId: lhs) != nil
                    let rhsHasItems = latestFetchedAt(in: db.feedItems, platformId: rhs) != nil
                    if lhsHasItems != rhsHasItems { return !lhsHasItems }
                    return lhs < rhs
                }

            await withTaskGroup(of: BackendPlatformRefreshResult.self) { group in
                for platform in platformsToFetch {
                    let platformSince = BackgroundRefreshPolicy.shouldUseDateWindowForPlatformRefresh(platform)
                        ? nil
                        : latestFetchedAt(in: db.feedItems, platformId: platform)
                    group.addTask {
                        do {
                            let items: [FeedItem]
                            if let platformSince {
                                items = try await NetworkManager.shared.fetchFeed(platform: platform, limit: 60, since: platformSince)
                            } else {
                                items = try await NetworkManager.shared.fetchFeed(platform: platform, limit: 60, days: 30)
                            }
                            return BackendPlatformRefreshResult(platform: platform, items: items, completed: true)
                        } catch {
                            AppLogger.network.warning("Background platform refresh \(platform) failed: \(error.localizedDescription)")
                            return BackendPlatformRefreshResult(platform: platform, items: [], completed: false)
                        }
                    }
                }

                for await result in group {
                    if Task.isCancelled {
                        group.cancelAll()
                        break
                    }
                    if result.completed {
                        completion.completedPlatforms.insert(result.platform)
                    }
                    let platformItems = result.items
                    guard !platformItems.isEmpty else { continue }
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
            guard !Task.isCancelled else {
                BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "cancelled_after_platforms")
                return BackendSourceRefreshCompletion()
            }

            let shouldNotifyLocally = BackgroundRefreshPolicy.shouldNotifyLocallyFromBackground(
                hasRegisteredRemoteDeviceForCurrentEnvironment: NetworkManager.shared.hasRegisteredAPNSDeviceForCurrentEnvironment,
                hadItemsInitially: hadItemsInitially,
                hasPendingNotificationItems: !pendingNotificationItems.isEmpty
            )
            if notifyOnNew, shouldNotifyLocally {
                await NotificationManager.shared.notifyForNewItems(
                    pendingNotificationItems,
                    terms: db.terms,
                    includeAttachments: false
                )
            }
            completion.refreshed = true
            return completion
        } catch {
            AppLogger.network.warning("Background refresh failed: \(error.localizedDescription)")
            let detail = Self.refreshErrorKind(error)
            BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: detail)
            await sendLiveTestFailureDiagnostic(
                reason: "background_refresh_failed",
                detail: "\(detail): \(error.localizedDescription)",
                db: db
            )
            return completion
        }
    }

    private func refreshLocalDeviceSourcesForBackground(timeout: TimeInterval) async -> LocalSourceRefreshCompletion {
        await withTaskGroup(of: LocalSourceRefreshCompletion.self) { group in
            group.addTask { @MainActor in
                await self.performLocalDeviceSourcesRefreshForBackground()
            }
            group.addTask {
                do {
                    try await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                } catch {
                    return LocalSourceRefreshCompletion()
                }
                BackgroundRefreshLiveTestProbe.recordCompleted(success: false, detail: "local_deadline")
                return LocalSourceRefreshCompletion()
            }

            let result = await group.next() ?? LocalSourceRefreshCompletion()
            group.cancelAll()
            return result
        }
    }

    private func performLocalDeviceSourcesRefreshForBackground() async -> LocalSourceRefreshCompletion {
        let db = LocalDB.shared
        var completion = LocalSourceRefreshCompletion()

        if !db.customUrls.isEmpty {
            let customItems = await NetworkManager.shared.scrapeCustomUrls(db.customUrls)
            guard !Task.isCancelled else { return completion }
            _ = db.mergeItems(newItems: customItems, notifyOnNew: false)
            completion.customRefreshed = true
        }

        let activeTerms = db.terms.filter(\.is_active)
        let fallbackPlatforms = BackgroundRefreshPolicy.fallbackPlatformsNeedingDevice(in: db.subscribedPlatforms)
        guard !activeTerms.isEmpty, !fallbackPlatforms.isEmpty else {
            return completion
        }

        await withTaskGroup(of: LocalFallbackScrapeResult.self) { group in
            for term in activeTerms {
                let searchTerms = [term.keyword] + term.aliases
                for searchTerm in searchTerms {
                    group.addTask {
                        await NetworkManager.shared.scrapeLocalFallbacksWithCompletion(
                            keyword: searchTerm,
                            tagKeyword: term.keyword,
                            platformIds: fallbackPlatforms
                        )
                    }
                }
            }

            var scrapeResults = [LocalFallbackScrapeResult]()
            for await scrapeResult in group {
                if Task.isCancelled {
                    group.cancelAll()
                    break
                }
                scrapeResults.append(scrapeResult)
                if !scrapeResult.items.isEmpty {
                    _ = db.mergeItems(newItems: scrapeResult.items, notifyOnNew: false)
                }
            }
            completion.completedDevicePlatforms.formUnion(
                BackgroundRefreshPolicy.completedDeviceFallbackPlatformsForAllSearches(
                    from: scrapeResults,
                    subscribedPlatforms: db.subscribedPlatforms
                )
            )
        }
        return Task.isCancelled ? LocalSourceRefreshCompletion() : completion
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
