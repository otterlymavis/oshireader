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

    func updateCredential(platform: String, apiKey: String? = nil, bearerToken: String? = nil) async throws -> Credential {
        var body = [String: String]()
        if let apiKey { body["api_key"] = apiKey }
        if let bearerToken { body["bearer_token"] = bearerToken }
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        return try await apiRequest(URL(string: "\(apiBase)/api/credentials/\(platform)")!, method: "PUT", body: bodyData, authorized: true)
    }

    // MARK: - APNs Registration

    func registerAPNSDeviceToken(_ token: String) async throws {
        let deviceId = await MainActor.run { UIDevice.current.identifierForVendor?.uuidString ?? "" }
        let body: [String: String] = ["token": token, "environment": apnsEnvironment, "device_id": deviceId]
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        try await apiVoid(URL(string: "\(apiBase)/api/devices/apns-token")!, method: "POST", body: bodyData)
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

    func triggerPoll() async throws {
        if isUITesting { return }
        try await apiVoid(URL(string: "\(apiBase)/api/admin/poll")!, method: "POST", authorized: true)
    }
}
