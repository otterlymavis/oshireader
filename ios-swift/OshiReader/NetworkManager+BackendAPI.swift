import Foundation
import UIKit

struct BackendTermSyncResult {
    let succeeded: Bool
    let changed: Bool
}

extension NetworkManager {

    // MARK: - Watch Terms

    static func automaticSyncNotifyOnNewUpdate(localTerm: WatchTerm, serverTerm: WatchTerm) -> Bool? {
        localTerm.notify_on_new && !serverTerm.notify_on_new ? true : nil
    }

    static func automaticSyncNotifyOnNewCreate(localTerm: WatchTerm) -> Bool {
        localTerm.notify_on_new
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
        try await createWatchTerm(
            keyword: term.keyword,
            collectionMode: term.collection_mode,
            notifyOnNew: Self.automaticSyncNotifyOnNewCreate(localTerm: term),
            isActive: term.is_active,
            aliases: term.aliases,
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
                    let notifyOnNewUpdate = Self.automaticSyncNotifyOnNewUpdate(
                        localTerm: term,
                        serverTerm: serverTerm
                    )
                    let needsUpdate = serverTerm.collection_mode != term.collection_mode ||
                        serverTerm.is_active != term.is_active ||
                        notifyOnNewUpdate != nil ||
                        serverTerm.aliases != term.aliases
                    if needsUpdate,
                       let updatedTerm = try? await updateWatchTerm(
                        id: serverTerm.id,
                        isActive: term.is_active,
                        collectionMode: term.collection_mode,
                        notifyOnNew: notifyOnNewUpdate,
                        aliases: term.aliases,
                        timeout: timeout
                       ) {
                        let replaced = await MainActor.run {
                            LocalDB.shared.replaceTerm(localId: term.id, with: updatedTerm, ifUnchangedFrom: term)
                        }
                        if !replaced { succeeded = false }
                    } else if notifyOnNewUpdate != nil {
                        succeeded = false
                    } else {
                        let replaced = await MainActor.run {
                            LocalDB.shared.replaceTerm(localId: term.id, with: serverTerm, ifUnchangedFrom: term)
                        }
                        if needsUpdate { succeeded = false }
                        if !replaced { succeeded = false }
                    }
                    continue
                }
                let notifyOnNewUpdate = Self.automaticSyncNotifyOnNewUpdate(
                    localTerm: term,
                    serverTerm: serverTerm
                )
                let needsUpdate = serverTerm.collection_mode != term.collection_mode ||
                    serverTerm.is_active != term.is_active ||
                    notifyOnNewUpdate != nil ||
                    serverTerm.aliases != term.aliases
                if needsUpdate,
                   let updatedTerm = try? await updateWatchTerm(
                    id: serverTerm.id,
                    isActive: term.is_active,
                    collectionMode: term.collection_mode,
                    notifyOnNew: notifyOnNewUpdate,
                    aliases: term.aliases,
                    timeout: timeout
                   ) {
                    let replaced = await MainActor.run {
                        LocalDB.shared.replaceTerm(localId: term.id, with: updatedTerm, ifUnchangedFrom: term)
                    }
                    if !replaced { succeeded = false }
                } else if notifyOnNewUpdate != nil {
                    // Keep the user's local preference when APNs verification or
                    // the backend is temporarily unavailable. Replacing it with
                    // the server's false value makes a transient sync failure
                    // look like an intentional opt-out.
                    succeeded = false
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
        let localByKeyword = await MainActor.run {
            firstTermByKeyword(LocalDB.shared.terms)
        }
        var changed = false
        for term in backendTerms {
            let shouldSkipAfterDelete = await MainActor.run {
                LocalDB.shared.shouldSkipBackendTermAfterLocalDelete(term)
            }
            if shouldSkipAfterDelete { continue }
            if let localTerm = localByKeyword[term.keyword] {
                if localTerm != term {
                    let replaced = await MainActor.run {
                        LocalDB.shared.replaceTerm(localId: localTerm.id, with: term, ifUnchangedFrom: localTerm)
                    }
                    changed = changed || replaced
                }
            } else {
                await MainActor.run { LocalDB.shared.addTermFromBackend(term) }
                changed = true
            }
        }
        return BackendTermSyncResult(succeeded: true, changed: changed)
    }

    func createWatchTerm(
        keyword: String,
        collectionMode: CollectionMode,
        notifyOnNew: Bool = true,
        isActive: Bool = true,
        aliases: [String] = [],
        timeout: TimeInterval = 30
    ) async throws -> WatchTerm {
        if isUITesting {
            return WatchTerm(
                keyword: keyword,
                collection_mode: collectionMode,
                is_active: isActive,
                notify_on_new: notifyOnNew,
                aliases: aliases
            )
        }
        if notifyOnNew {
            _ = await NotificationManager.shared.ensureRemoteNotificationsRegisteredIfAllowed()
        }
        var body: [String: Any] = [
            "keyword": keyword,
            "collection_mode": collectionMode.rawValue,
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
        notifyOnNew: Bool? = nil,
        aliases: [String]? = nil,
        timeout: TimeInterval = 30
    ) async throws -> WatchTerm {
        if isUITesting { throw URLError(.cancelled) }
        if notifyOnNew == true {
            _ = await NotificationManager.shared.ensureRemoteNotificationsRegisteredIfAllowed()
        }
        var body: [String: Any] = [:]
        if let isActive { body["is_active"] = isActive }
        if let collectionMode { body["collection_mode"] = collectionMode.rawValue }
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

    var apnsDeviceSecret: String {
        let key = "apns_device_secret"
        if let existing = KeychainHelper.read(key: key), !existing.isEmpty {
            return existing
        }
        let generated = UUID().uuidString
        KeychainHelper.write(key: key, value: generated)
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
        try await apiVoid(URL(string: "\(apiBase)/api/devices/apns-token")!, method: "POST", body: bodyData)
        KeychainHelper.write(key: "apns_device_token", value: token)
        KeychainHelper.write(key: "apns_device_environment", value: apnsEnvironment)
    }

    func sendRemoteTestPush() async throws -> APNSTestPushReport {
        if isUITesting {
            return APNSTestPushReport(configured: false, results: [], note: "ui testing", pruned_tokens: 0)
        }
        if hasRegisteredAPNSDeviceForCurrentEnvironment,
           let token = registeredAPNSDeviceToken,
           !token.isEmpty {
            var body: [String: Any] = [
                "token": token,
                "device_secret": apnsDeviceSecret,
            ]
            if isLiveBackgroundPushTesting {
                body["delivery_delay_seconds"] = 4
                body["return_before_delivery"] = true
            }
            let bodyData = try JSONSerialization.data(withJSONObject: body)
            return try await sendDeviceScopedRemoteTestPush(bodyData: bodyData)
        }
        let deviceId = await apnsDeviceId()
        var body: [String: Any] = [
            "device_id": deviceId,
            "environment": apnsEnvironment,
            "device_secret": apnsDeviceSecret,
        ]
        if isLiveBackgroundPushTesting {
            body["delivery_delay_seconds"] = 4
            body["return_before_delivery"] = true
        }
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        return try await sendDeviceScopedRemoteTestPush(bodyData: bodyData)
    }

    private func sendDeviceScopedRemoteTestPush(bodyData: Data) async throws -> APNSTestPushReport {
        let url = URL(string: "\(apiBase)/api/devices/apns-test-push")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.httpBody = bodyData
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        do {
            let (data, response) = try await session.data(for: request)
            return try handleDeviceScopedResponse(data: data, response: response)
        } catch let error as URLError where error.code == .networkConnectionLost {
            AppLogger.network.warning("Connection lost for apns-test-push, retrying request once...")
            let (data, response) = try await session.data(for: request)
            return try handleDeviceScopedResponse(data: data, response: response)
        }
    }

    private func handleDeviceScopedResponse(data: Data, response: URLResponse) throws -> APNSTestPushReport {
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard http.statusCode == 200 else {
            if http.statusCode == 404 {
                clearRegisteredAPNSDeviceToken()
            }
            throw APIClientError.httpStatus(http.statusCode, detail: backendErrorDetail(from: data))
        }
        return try JSONDecoder().decode(APNSTestPushReport.self, from: data)
    }

    // MARK: - Health & Admin

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
            try await registerAPNSDeviceToken(storedToken)
            try await sendDeviceBackgroundRefresh(bodyData: bodyData, timeout: timeout)
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

    func sendClientDiagnostic(_ report: ClientDiagnosticReport) async {
        guard !isUITesting else { return }
        do {
            let body = try JSONEncoder().encode(report)
            try await apiVoid(
                URL(string: "\(apiBase)/api/client-diagnostics")!,
                method: "POST",
                body: body,
                timeout: 12
            )
        } catch {
            AppLogger.network.warning("sendClientDiagnostic failed: \(error.localizedDescription)")
        }
    }
}
