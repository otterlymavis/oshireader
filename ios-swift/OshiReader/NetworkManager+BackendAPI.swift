import Foundation
import UIKit

struct BackendTermSyncResult {
    let succeeded: Bool
    let changed: Bool
}

struct SourceHealthEntry: Decodable, Identifiable {
    let platform: String
    let status: String?
    let last_checked_at: String?
    let last_success_at: String?
    let last_item_count: Int?
    let last_error: String?
    let consecutive_failures: Int
    // jina_ok reflects r.jina.ai's raw request outcome, independent of `status` above —
    // a platform can show status "success"/"empty" (Bing covered for it) while jina_ok is
    // false, meaning jina itself is degraded even though the platform looks healthy
    // overall. See backend/app/source_health.py's record_jina_result.
    let jina_checked_at: String?
    let jina_ok: Bool?
    let jina_error: String?

    var id: String { platform }
}

struct SourceHealthResponse: Decodable {
    let sources: [SourceHealthEntry]
}

extension NetworkManager {

    // MARK: - Watch Terms

    static func automaticSyncNotifyOnNewUpdate(localTerm: WatchTerm, serverTerm: WatchTerm) -> Bool? {
        nil
    }

    static func automaticSyncNotifyOnNewCreate(localTerm: WatchTerm) -> Bool {
        false
    }

    static func automaticSyncForceAPNSRefreshCreate(localTerm: WatchTerm) -> Bool {
        false
    }

    static func pulledBackendTermForLocalMerge(localTerm: WatchTerm, serverTerm: WatchTerm) -> WatchTerm {
        serverTerm
    }

    static func shouldRevalidateAPNSBeforeNotificationWrite(
        notifyOnNew: Bool?,
        forceAPNSRefresh: Bool,
        isUnitTesting: Bool
    ) -> Bool {
        notifyOnNew == true && (forceAPNSRefresh || !isUnitTesting)
    }

    private func firstTermByKeyword(_ terms: [WatchTerm]) -> [String: WatchTerm] {
        var result: [String: WatchTerm] = [:]
        for term in terms where result[term.keyword] == nil {
            result[term.keyword] = term
        }
        return result
    }

    private func createWatchTermForAutomaticSync(
        _ term: WatchTerm,
        timeout: TimeInterval = 30
    ) async throws -> WatchTerm {
        let notifyOnNew = term.repaired_from_cache
            ? false
            : Self.automaticSyncNotifyOnNewCreate(localTerm: term)
        return try await createWatchTerm(
            keyword: term.keyword,
            collectionMode: term.collection_mode,
            sourceMode: term.source_mode,
            selectedPlatforms: term.selected_platforms,
            notifyOnNew: notifyOnNew,
            isActive: term.is_active,
            aliases: term.aliases,
            forceAPNSRefresh: !term.repaired_from_cache &&
                Self.automaticSyncForceAPNSRefreshCreate(localTerm: term) &&
                !isUnitTesting,
            timeout: timeout
        )
    }

    func fetchWatchTerms(timeout: TimeInterval = 30) async throws -> [WatchTerm] {
        try await apiRequest(
            URL(string: "\(apiBase)/api/watch-terms/")!,
            authorized: true,
            deviceAuthorized: true,
            acceptRange: 200...200,
            timeout: timeout
        )
    }

    // Pushes local watch terms missing from the backend (e.g. after a database reset).
    @discardableResult
    func syncWatchTermsToBackend(
        localTerms: [WatchTerm],
        timeout: TimeInterval = 30
    ) async -> Bool {
        guard !isUITesting else { return false }
        guard !localTerms.isEmpty else { return true }
        guard let backendTerms = try? await fetchWatchTerms(timeout: timeout) else { return false }
        var succeeded = true

        let backendByKeyword = firstTermByKeyword(backendTerms)
        for term in localTerms {
            let shouldSkipAfterDelete = await MainActor.run {
                LocalDB.shared.shouldSkipBackendTermAfterLocalDelete(term)
            }
            if shouldSkipAfterDelete { continue }
            if let serverTerm = backendByKeyword[term.keyword] {
                if term.repaired_from_cache {
                    let needsUpdate = serverTerm.collection_mode != term.collection_mode ||
                        serverTerm.source_mode != term.source_mode ||
                        serverTerm.selected_platforms != term.selected_platforms ||
                        serverTerm.is_active != term.is_active ||
                        serverTerm.aliases != term.aliases
                    if needsUpdate,
                       let updatedTerm = try? await updateWatchTerm(
                        id: serverTerm.id,
                        isActive: term.is_active,
                        collectionMode: term.collection_mode,
                        sourceMode: term.source_mode,
                        selectedPlatforms: term.selected_platforms,
                        notifyOnNew: nil,
                        aliases: term.aliases,
                        timeout: timeout
                       ) {
                        let replaced = await MainActor.run {
                            LocalDB.shared.replaceTerm(localId: term.id, with: updatedTerm, ifUnchangedFrom: term)
                        }
                        if !replaced { succeeded = false }
                    } else {
                        let replaced = await MainActor.run {
                            LocalDB.shared.replaceTerm(localId: term.id, with: serverTerm, ifUnchangedFrom: term)
                        }
                        if needsUpdate { succeeded = false }
                        if !replaced { succeeded = false }
                    }
                    continue
                }
                let needsUpdate = serverTerm.collection_mode != term.collection_mode ||
                    serverTerm.source_mode != term.source_mode ||
                    serverTerm.selected_platforms != term.selected_platforms ||
                    serverTerm.is_active != term.is_active ||
                    serverTerm.aliases != term.aliases
                if needsUpdate,
                   let updatedTerm = try? await updateWatchTerm(
                    id: serverTerm.id,
                    isActive: term.is_active,
                    collectionMode: term.collection_mode,
                    sourceMode: term.source_mode,
                    selectedPlatforms: term.selected_platforms,
                    notifyOnNew: nil,
                    aliases: term.aliases,
                    timeout: timeout
                   ) {
                    let replaced = await MainActor.run {
                        LocalDB.shared.replaceTerm(localId: term.id, with: updatedTerm, ifUnchangedFrom: term)
                    }
                    if !replaced { succeeded = false }
                } else {
                    let replaced = await MainActor.run {
                        LocalDB.shared.replaceTerm(localId: term.id, with: serverTerm, ifUnchangedFrom: term)
                    }
                    if needsUpdate { succeeded = false }
                    if !replaced { succeeded = false }
                }
            } else if let serverTerm = try? await createWatchTermForAutomaticSync(term, timeout: timeout) {
                let replaced = await MainActor.run {
                    LocalDB.shared.replaceTerm(localId: term.id, with: serverTerm, ifUnchangedFrom: term)
                }
                if !replaced { succeeded = false }
            } else {
                succeeded = false
            }
        }
        return succeeded
    }

    // Pulls backend watch terms absent locally (e.g. fresh install after another session registered terms).
    @discardableResult
    func syncTermsFromBackend() async -> Bool {
        let result = await syncTermsFromBackendWithStatus()
        return result.changed
    }

    @discardableResult
    func syncTermsFromBackendWithStatus(timeout: TimeInterval = 30) async -> BackendTermSyncResult {
        guard !isUITesting else {
            return BackendTermSyncResult(succeeded: false, changed: false)
        }
        guard let backendTerms = try? await fetchWatchTerms(timeout: timeout) else {
            return BackendTermSyncResult(succeeded: false, changed: false)
        }
        // Mutated as backend terms are processed (not just seeded once) so that
        // multiple backend rows sharing a keyword — e.g. leftover server-side
        // duplicates from a prior device-identity reset — merge into the same
        // local term instead of each spawning its own local row.
        var localByKeyword = await MainActor.run {
            firstTermByKeyword(LocalDB.shared.terms)
        }
        var changed = false
        for backendTerm in backendTerms {
            var term = backendTerm
            term.collection_mode = .allInfo
            let shouldSkipAfterDelete = await MainActor.run {
                LocalDB.shared.shouldSkipBackendTermAfterLocalDelete(term)
            }
            if shouldSkipAfterDelete { continue }
            if let localTerm = localByKeyword[term.keyword] {
                let mergedTerm = localTerm.repaired_from_cache
                    ? term
                    : Self.pulledBackendTermForLocalMerge(
                        localTerm: localTerm,
                        serverTerm: term
                    )
                if localTerm != mergedTerm {
                    let replaced = await MainActor.run {
                        LocalDB.shared.replaceTerm(localId: localTerm.id, with: mergedTerm, ifUnchangedFrom: localTerm)
                    }
                    changed = changed || replaced
                }
                localByKeyword[term.keyword] = mergedTerm
            } else {
                await MainActor.run { LocalDB.shared.addTermFromBackend(term) }
                localByKeyword[term.keyword] = term
                changed = true
            }
        }
        return BackendTermSyncResult(succeeded: true, changed: changed)
    }

    func createWatchTerm(
        keyword: String,
        collectionMode: CollectionMode,
        sourceMode: SourceMode = .all,
        selectedPlatforms: [String] = [],
        notifyOnNew: Bool = false,
        isActive: Bool = true,
        aliases: [String] = [],
        forceAPNSRefresh: Bool = false,
        timeout: TimeInterval = 30
    ) async throws -> WatchTerm {
        try await createWatchTermRequest(
            keyword: keyword,
            collectionMode: collectionMode,
            sourceMode: sourceMode,
            selectedPlatforms: selectedPlatforms,
            notifyOnNew: notifyOnNew,
            isActive: isActive,
            aliases: aliases,
            forceAPNSRefresh: forceAPNSRefresh,
            apnsAlreadyVerified: false,
            timeout: timeout
        )
    }

    private func createWatchTermRequest(
        keyword: String,
        collectionMode: CollectionMode,
        sourceMode: SourceMode = .all,
        selectedPlatforms: [String] = [],
        notifyOnNew: Bool = false,
        isActive: Bool = true,
        aliases: [String] = [],
        forceAPNSRefresh: Bool = false,
        apnsAlreadyVerified: Bool = false,
        timeout: TimeInterval = 30
    ) async throws -> WatchTerm {
        if isUITesting {
            return WatchTerm(
                keyword: keyword,
                collection_mode: collectionMode,
                source_mode: sourceMode,
                selected_platforms: selectedPlatforms,
                is_active: isActive,
                notify_on_new: notifyOnNew,
                aliases: aliases
            )
        }
        let shouldRevalidateAPNS = Self.shouldRevalidateAPNSBeforeNotificationWrite(
            notifyOnNew: notifyOnNew,
            forceAPNSRefresh: forceAPNSRefresh,
            isUnitTesting: isUnitTesting
        ) && !apnsAlreadyVerified
        if shouldRevalidateAPNS {
            let registrationReady = await NotificationManager.shared.ensureRemoteNotificationsRegisteredIfAllowed(
                timeout: min(timeout, 8),
                forceRefresh: forceAPNSRefresh || !isUnitTesting,
                syncAfterVerification: false
            )
            guard registrationReady else {
                throw APIClientError.apnsRegistrationUnverified(
                    "Timed out waiting for APNs device verification"
                )
            }
        }
        var body: [String: Any] = [
            "keyword": keyword,
            "collection_mode": collectionMode.rawValue,
            "source_mode": sourceMode.rawValue,
            "selected_platforms": selectedPlatforms,
            "notify_on_new": notifyOnNew,
            "is_active": isActive,
        ]
        if !aliases.isEmpty { body["aliases"] = aliases }
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        return try await apiRequest(
            URL(string: "\(apiBase)/api/watch-terms/")!,
            method: "POST",
            body: bodyData,
            authorized: true,
            deviceAuthorized: true,
            timeout: timeout
        )
    }

    func updateWatchTerm(
        id: String,
        isActive: Bool? = nil,
        collectionMode: CollectionMode? = nil,
        sourceMode: SourceMode? = nil,
        selectedPlatforms: [String]? = nil,
        notifyOnNew: Bool? = nil,
        aliases: [String]? = nil,
        timeout: TimeInterval = 30,
        forceAPNSRefresh: Bool = false
    ) async throws -> WatchTerm {
        try await updateWatchTermRequest(
            id: id,
            isActive: isActive,
            collectionMode: collectionMode,
            sourceMode: sourceMode,
            selectedPlatforms: selectedPlatforms,
            notifyOnNew: notifyOnNew,
            aliases: aliases,
            timeout: timeout,
            forceAPNSRefresh: forceAPNSRefresh,
            apnsAlreadyVerified: false
        )
    }

    private func updateWatchTermRequest(
        id: String,
        isActive: Bool? = nil,
        collectionMode: CollectionMode? = nil,
        sourceMode: SourceMode? = nil,
        selectedPlatforms: [String]? = nil,
        notifyOnNew: Bool? = nil,
        aliases: [String]? = nil,
        timeout: TimeInterval = 30,
        forceAPNSRefresh: Bool = false,
        apnsAlreadyVerified: Bool = false
    ) async throws -> WatchTerm {
        if isUITesting { throw URLError(.cancelled) }
        let shouldRevalidateAPNS = Self.shouldRevalidateAPNSBeforeNotificationWrite(
            notifyOnNew: notifyOnNew,
            forceAPNSRefresh: forceAPNSRefresh,
            isUnitTesting: isUnitTesting
        ) && !apnsAlreadyVerified
        if shouldRevalidateAPNS {
            // A cached token may have become unverified on the backend after
            // rotation or a transient APNs validation failure. Re-register when
            // the user enables notifications so the preference update follows a
            // fresh device verification attempt.
            let registrationReady = await NotificationManager.shared.ensureRemoteNotificationsRegisteredIfAllowed(
                timeout: min(timeout, 8),
                forceRefresh: forceAPNSRefresh || !isUnitTesting,
                syncAfterVerification: false
            )
            guard registrationReady else {
                throw APIClientError.apnsRegistrationUnverified(
                    "Timed out waiting for APNs device verification"
                )
            }
        }
        var body: [String: Any] = [:]
        if let isActive { body["is_active"] = isActive }
        if let collectionMode { body["collection_mode"] = collectionMode.rawValue }
        if let sourceMode { body["source_mode"] = sourceMode.rawValue }
        if let selectedPlatforms { body["selected_platforms"] = selectedPlatforms }
        if let notifyOnNew { body["notify_on_new"] = notifyOnNew }
        if let aliases { body["aliases"] = aliases }
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        return try await apiRequest(
            URL(string: "\(apiBase)/api/watch-terms/\(id)")!,
            method: "PATCH",
            body: bodyData,
            authorized: true,
            deviceAuthorized: true,
            timeout: timeout
        )
    }

    func setWatchTermNotificationPreference(
        for term: WatchTerm,
        enabled: Bool,
        timeout: TimeInterval = 30
    ) async throws -> WatchTerm {
        do {
            return try await updateWatchTerm(
                id: term.id,
                notifyOnNew: enabled,
                timeout: timeout,
                forceAPNSRefresh: enabled
            )
        } catch APIClientError.httpStatus(404, _) {
            if let existing = try await fetchWatchTerms(timeout: timeout).first(where: { $0.keyword == term.keyword }) {
                return try await updateWatchTermRequest(
                    id: existing.id,
                    notifyOnNew: enabled,
                    timeout: timeout,
                    forceAPNSRefresh: enabled,
                    apnsAlreadyVerified: true
                )
            }
            if !enabled {
                // Term doesn't exist on the backend — treat as a local-only
                // term so the next sync resolves it by keyword rather than
                // repeatedly PATCHing the local UUID (which always 404s).
                return WatchTerm(
                    id: term.id,
                    keyword: term.keyword,
                    collection_mode: term.collection_mode,
                    source_mode: term.source_mode,
                    selected_platforms: term.selected_platforms,
                    is_active: term.is_active,
                    notify_on_new: false,
                    aliases: term.aliases,
                    repaired_from_cache: true,
                    created_at: term.created_at
                )
            }
            return try await createWatchTermRequest(
                keyword: term.keyword,
                collectionMode: term.collection_mode,
                sourceMode: term.source_mode,
                selectedPlatforms: term.selected_platforms,
                notifyOnNew: true,
                isActive: term.is_active,
                aliases: term.aliases,
                forceAPNSRefresh: true,
                apnsAlreadyVerified: true,
                timeout: timeout
            )
        }
    }

    func triggerWatchTermNotification(id: String, timeout: TimeInterval = 30) async throws {
        if isUITesting { return }
        try await apiVoid(
            URL(string: "\(apiBase)/api/watch-terms/\(id)/notify")!,
            method: "POST",
            authorized: true,
            deviceAuthorized: true,
            timeout: timeout
        )
    }

    func clearWatchTermNotification(id: String, timeout: TimeInterval = 30) async throws {
        if isUITesting { return }
        try await apiVoid(
            URL(string: "\(apiBase)/api/watch-terms/\(id)/notify")!,
            method: "DELETE",
            authorized: true,
            deviceAuthorized: true,
            timeout: timeout
        )
    }

    func deleteWatchTerm(id: String) async throws {
        if isUITesting { return }
        try await apiVoid(
            URL(string: "\(apiBase)/api/watch-terms/\(id)")!,
            method: "DELETE",
            authorized: true,
            deviceAuthorized: true,
            additionalAcceptedStatuses: [404]
        )
    }

    func deleteWatchTermIfSynced(_ term: WatchTerm) async throws {
        if UUID(uuidString: term.id) == nil {
            try await deleteWatchTerm(id: term.id)
            return
        }
        guard term.repaired_from_cache else { return }
        let backendTerms = try await fetchWatchTerms()
        if let serverTerm = firstTermByKeyword(backendTerms)[term.keyword] {
            try await deleteWatchTerm(id: serverTerm.id)
        }
    }

    // MARK: - Feed

    func fetchFeed(
        termId: Int? = nil,
        platform: String? = nil,
        limit: Int = 50,
        days: Int = 30,
        since: String? = nil,
        timeout: TimeInterval = 30
    ) async throws -> [FeedItem] {
        if isUITesting { return [] }
        var components = URLComponents(string: "\(apiBase)/api/feed/")!
        var queryItems: [URLQueryItem] = [URLQueryItem(name: "limit", value: String(limit))]
        if let since { queryItems.append(URLQueryItem(name: "since", value: since)) }
        else { queryItems.append(URLQueryItem(name: "days", value: String(days))) }
        if let termId { queryItems.append(URLQueryItem(name: "term_id", value: String(termId))) }
        if let platform { queryItems.append(URLQueryItem(name: "platform", value: platform)) }
        components.queryItems = queryItems
        let backendItems: [BackendFeedItem] = try await apiRequest(
            components.url!,
            authorized: true,
            deviceAuthorized: true,
            acceptRange: 200...200,
            timeout: timeout
        )
        return backendItems.map { $0.toFeedItem() }
    }

    func muteFeedItem(sourceItemID: String, watchTermID: Int) async throws {
        if isUITesting { return }
        let body: [String: Any] = [
            "source_item_id": sourceItemID,
            "watch_term_id": watchTermID,
        ]
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        try await apiVoid(
            URL(string: "\(apiBase)/api/feed/muted-items")!,
            method: "POST",
            body: bodyData,
            authorized: true,
            deviceAuthorized: true
        )
    }

    // MARK: - Credentials

    func fetchCredentials() async throws -> [Credential] {
        try await apiRequest(URL(string: "\(apiBase)/api/credentials/")!, authorized: true, acceptRange: 200...200)
    }

    func updateCredential(platform: String, apiKey: String? = nil, bearerToken: String? = nil, apiSecret: String? = nil) async throws -> Credential {
        var body = [String: String]()
        if let apiKey { body["api_key"] = apiKey }
        if let bearerToken { body["bearer_token"] = bearerToken }
        if let apiSecret { body["api_secret"] = apiSecret }
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        return try await apiRequest(URL(string: "\(apiBase)/api/credentials/\(platform)")!, method: "PUT", body: bodyData, authorized: true)
    }

    // MARK: - APNs Registration

    private static let cachedAPNSDeviceSecretLock = NSLock()
    private static var cachedAPNSDeviceSecret: String?

    var apnsDeviceSecret: String {
        let key = "apns_device_secret"
        if let existing = KeychainHelper.read(key: key), !existing.isEmpty {
            return existing
        }
        // Keychain write can fail transiently (e.g. before first unlock after
        // reboot). Cache the generated value in memory so every call this session
        // returns the same secret instead of minting a new one each time and
        // desyncing device identity between calls (e.g. token registration vs.
        // background refresh). Locked because this property is read from
        // concurrent tasks (background refresh, foreground requests) — without
        // it, two callers hitting the Keychain miss at once could each mint and
        // cache a different UUID before either write completes.
        NetworkManager.cachedAPNSDeviceSecretLock.lock()
        defer { NetworkManager.cachedAPNSDeviceSecretLock.unlock() }
        if let cached = NetworkManager.cachedAPNSDeviceSecret {
            return cached
        }
        let generated = UUID().uuidString
        if !KeychainHelper.write(key: key, value: generated) {
            AppLogger.network.warning("Failed to persist APNs device secret to Keychain; using in-memory value for this session")
        }
        NetworkManager.cachedAPNSDeviceSecret = generated
        return generated
    }

    private func apnsDeviceId() async -> String {
        await MainActor.run { UIDevice.current.identifierForVendor?.uuidString ?? "" }
    }

    var registeredAPNSDeviceToken: String? {
        KeychainHelper.read(key: "apns_device_token")
    }

    var registeredAPNSDeviceEnvironment: String? {
        KeychainHelper.read(key: "apns_device_environment")
    }

    var hasRegisteredAPNSDeviceForCurrentEnvironment: Bool {
        guard let token = registeredAPNSDeviceToken, !token.isEmpty else { return false }
        return registeredAPNSDeviceEnvironment == apnsEnvironment
    }

    func clearRegisteredAPNSDeviceToken() {
        KeychainHelper.delete(key: "apns_device_token")
        KeychainHelper.delete(key: "apns_device_environment")
    }

    func unregisterAPNSDeviceToken() async throws {
        guard let token = registeredAPNSDeviceToken, !token.isEmpty else { return }
        let url = URL(string: "\(apiBase)/api/devices/apns-token/\(token)")!
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        request.timeoutInterval = 15
        request.setValue(apnsDeviceSecret, forHTTPHeaderField: "X-Device-Secret")

        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard http.statusCode == 204 || http.statusCode == 404 else {
            throw APIClientError.httpStatus(http.statusCode, detail: nil)
        }
        clearRegisteredAPNSDeviceToken()
    }

    func registerAPNSDeviceToken(_ token: String) async throws {
        let deviceId = await apnsDeviceId()
        let body: [String: String] = [
            "token": token,
            "environment": apnsEnvironment,
            "device_id": deviceId,
            "device_secret": apnsDeviceSecret,
        ]
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        let registration: APNSDeviceRegistrationResponse = try await apiRequest(
            URL(string: "\(apiBase)/api/devices/apns-token")!,
            method: "POST",
            body: bodyData,
            acceptRange: 200...299,
            timeout: 90
        )
        guard registration.is_verified == true else {
            throw APIClientError.apnsRegistrationUnverified(
                registration.verification_error ?? "The server could not verify this device token"
            )
        }
        // If either write fails (e.g. Keychain unavailable before first unlock),
        // the backend now has a verified device but this device won't know it —
        // hasRegisteredAPNSDeviceForCurrentEnvironment will keep reporting false
        // until a later successful write. Logging makes that state visible;
        // subsequent registration attempts (launch, background refresh) retry it.
        if !KeychainHelper.write(key: "apns_device_token", value: token) {
            AppLogger.network.warning("Failed to persist verified APNs device token to Keychain")
        }
        if !KeychainHelper.write(key: "apns_device_environment", value: apnsEnvironment) {
            AppLogger.network.warning("Failed to persist APNs device environment to Keychain")
        }
    }

    // MARK: - Health & Admin

    // Returns nil on a failed request so callers can keep showing previously
    // loaded data instead of mistaking a network failure for "nothing polled
    // yet" — [] from the backend (a real empty snapshot) stays distinct.
    func fetchSourceHealth(timeout: TimeInterval = 15) async -> [SourceHealthEntry]? {
        guard let response: SourceHealthResponse = try? await apiRequest(
            URL(string: "\(apiBase)/api/source-health")!,
            deviceAuthorized: true,
            acceptRange: 200...200,
            timeout: timeout
        ) else { return nil }
        return response.sources
    }

    func checkHealth() async throws -> Bool {
        let url = URL(string: "\(apiBase)/api/health")!
        var request = URLRequest(url: url)
        request.timeoutInterval = 10
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            return false
        }
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let status = json["status"] as? String, status == "ok" {
            return true
        }
        return false
    }

    func triggerPoll(timeout: TimeInterval = 90) async throws {
        if isUITesting { return }
        try await apiVoid(URL(string: "\(apiBase)/api/admin/poll")!, method: "POST", authorized: true, timeout: timeout)
    }

    func triggerBackgroundPoll(timeout: TimeInterval = 90) async throws {
        if isUITesting { return }
        do {
            try await triggerDeviceBackgroundRefresh(timeout: timeout)
        } catch {
            guard !Task.isCancelled else { throw error }
            AppLogger.network.warning("Device-scoped background refresh failed: \(error.localizedDescription)")
            throw error
        }
    }

    private func triggerDeviceBackgroundRefresh(timeout: TimeInterval) async throws {
        let body: [String: String]
        let storedToken: String?
        if hasRegisteredAPNSDeviceForCurrentEnvironment,
           let token = registeredAPNSDeviceToken,
           !token.isEmpty {
            storedToken = token
            body = [
                "token": token,
                "device_secret": apnsDeviceSecret,
            ]
        } else {
            storedToken = nil
            let deviceId = await apnsDeviceId()
            body = [
                "device_id": deviceId,
                "environment": apnsEnvironment,
                "device_secret": apnsDeviceSecret,
            ]
        }
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        do {
            try await sendDeviceBackgroundRefresh(bodyData: bodyData, timeout: timeout)
        } catch APIClientError.httpStatus(404, let detail) {
            guard let storedToken else { throw APIClientError.httpStatus(404, detail: detail) }
            AppLogger.network.notice("Background refresh credential rejected; re-registering APNs token and retrying once")
            do {
                try await registerAPNSDeviceToken(storedToken)
            } catch APIClientError.apnsRegistrationUnverified(let reason) {
                AppLogger.network.warning("APNs token failed server-side verification after re-registration — clearing stale token")
                clearRegisteredAPNSDeviceToken()
                throw APIClientError.apnsRegistrationUnverified(reason)
            }
            do {
                try await sendDeviceBackgroundRefresh(bodyData: bodyData, timeout: timeout)
            } catch APIClientError.httpStatus(404, _) {
                AppLogger.network.warning("Background refresh still rejected after re-registration — clearing stale APNs token")
                clearRegisteredAPNSDeviceToken()
                throw APIClientError.httpStatus(404, detail: detail)
            }
        }
    }

    private func sendDeviceBackgroundRefresh(bodyData: Data, timeout: TimeInterval) async throws {
        let url = URL(string: "\(apiBase)/api/devices/background-refresh")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.httpBody = bodyData
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        do {
            let (data, response) = try await session.data(for: request)
            try validateBackgroundRefreshResponse(data: data, response: response)
        } catch let error as URLError where error.code == .networkConnectionLost {
            AppLogger.network.warning("Connection lost for background-refresh, retrying request once...")
            let (data, response) = try await session.data(for: request)
            try validateBackgroundRefreshResponse(data: data, response: response)
        }
    }

    private func validateBackgroundRefreshResponse(data: Data, response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard (200...299).contains(http.statusCode) else {
            throw APIClientError.httpStatus(http.statusCode, detail: backendErrorDetail(from: data))
        }
    }

    @discardableResult
    func sendClientDiagnostic(_ report: ClientDiagnosticReport) async -> Bool {
        guard !isUITesting else { return false }
        do {
            let body = try JSONEncoder().encode(report)
            try await apiVoid(
                URL(string: "\(apiBase)/api/client-diagnostics")!,
                method: "POST",
                body: body,
                timeout: 12
            )
            return true
        } catch {
            AppLogger.network.warning("sendClientDiagnostic failed: \(error.localizedDescription)")
            return false
        }
    }
}
