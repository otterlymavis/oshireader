import Foundation
import UIKit

extension NetworkManager {

    // MARK: - Watch Terms

    func fetchWatchTerms() async throws -> [WatchTerm] {
        try await apiRequest(URL(string: "\(apiBase)/api/watch-terms/")!, acceptRange: 200...200)
    }

    // Pushes local watch terms missing from the backend (e.g. after a database reset).
    func syncWatchTermsToBackend(localTerms: [WatchTerm]) async {
        guard !isUITesting, !localTerms.isEmpty else { return }
        guard let backendTerms = try? await fetchWatchTerms() else { return }
        let backendKeywords = Set(backendTerms.map { $0.keyword })
        for term in localTerms where !backendKeywords.contains(term.keyword) {
            if let serverTerm = try? await createWatchTerm(keyword: term.keyword, collectionMode: term.collection_mode, aliases: term.aliases) {
                await MainActor.run { LocalDB.shared.replaceTerm(localId: term.id, with: serverTerm) }
            }
        }
    }

    // Pulls backend watch terms absent locally (e.g. fresh install after another session registered terms).
    @discardableResult
    func syncTermsFromBackend() async -> Bool {
        guard !isUITesting else { return false }
        guard let backendTerms = try? await fetchWatchTerms() else { return false }
        let localKeywords = await MainActor.run { Set(LocalDB.shared.terms.map { $0.keyword }) }
        var added = false
        for term in backendTerms where !localKeywords.contains(term.keyword) {
            await MainActor.run { LocalDB.shared.addTermFromBackend(term) }
            added = true
        }
        return added
    }

    func createWatchTerm(keyword: String, collectionMode: CollectionMode, aliases: [String] = []) async throws -> WatchTerm {
        if isUITesting { return WatchTerm(keyword: keyword, collection_mode: collectionMode) }
        var body: [String: Any] = ["keyword": keyword, "collection_mode": collectionMode.rawValue]
        if !aliases.isEmpty { body["aliases"] = aliases }
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        return try await apiRequest(URL(string: "\(apiBase)/api/watch-terms/")!, method: "POST", body: bodyData, authorized: true)
    }

    func updateWatchTerm(id: String, isActive: Bool? = nil, collectionMode: CollectionMode? = nil, notifyOnNew: Bool? = nil, aliases: [String]? = nil) async throws -> WatchTerm {
        if isUITesting { throw URLError(.cancelled) }
        var body: [String: Any] = [:]
        if let isActive { body["is_active"] = isActive }
        if let collectionMode { body["collection_mode"] = collectionMode.rawValue }
        if let notifyOnNew { body["notify_on_new"] = notifyOnNew }
        if let aliases { body["aliases"] = aliases }
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        return try await apiRequest(URL(string: "\(apiBase)/api/watch-terms/\(id)")!, method: "PATCH", body: bodyData, authorized: true)
    }

    func deleteWatchTerm(id: String) async throws {
        if isUITesting { return }
        try await apiVoid(URL(string: "\(apiBase)/api/watch-terms/\(id)")!, method: "DELETE", authorized: true)
    }

    // MARK: - Feed

    func fetchFeed(termId: Int? = nil, platform: String? = nil, limit: Int = 50, days: Int = 30, since: String? = nil) async throws -> [FeedItem] {
        if isUITesting { return [] }
        var components = URLComponents(string: "\(apiBase)/api/feed/")!
        var queryItems: [URLQueryItem] = [URLQueryItem(name: "limit", value: String(limit))]
        if let since { queryItems.append(URLQueryItem(name: "since", value: since)) }
        else { queryItems.append(URLQueryItem(name: "days", value: String(days))) }
        if let termId { queryItems.append(URLQueryItem(name: "term_id", value: String(termId))) }
        if let platform { queryItems.append(URLQueryItem(name: "platform", value: platform)) }
        components.queryItems = queryItems
        let backendItems: [BackendFeedItem] = try await apiRequest(components.url!, acceptRange: 200...200)
        return backendItems.map { $0.toFeedItem() }
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

    private var apnsDeviceSecret: String {
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
            throw APIClientError.httpStatus(http.statusCode)
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
            let bodyData = try JSONSerialization.data(withJSONObject: [
                "token": token,
                "device_secret": apnsDeviceSecret,
            ])
            return try await sendDeviceScopedRemoteTestPush(bodyData: bodyData)
        }
        let deviceId = await apnsDeviceId()
        let bodyData = try JSONSerialization.data(withJSONObject: [
            "device_id": deviceId,
            "environment": apnsEnvironment,
            "device_secret": apnsDeviceSecret,
        ])
        return try await sendDeviceScopedRemoteTestPush(bodyData: bodyData)
    }

    private func sendDeviceScopedRemoteTestPush(bodyData: Data) async throws -> APNSTestPushReport {
        let url = URL(string: "\(apiBase)/api/devices/apns-test-push")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.httpBody = bodyData
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard http.statusCode == 200 else {
            if http.statusCode == 404 {
                clearRegisteredAPNSDeviceToken()
            }
            throw APIClientError.httpStatus(http.statusCode)
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
