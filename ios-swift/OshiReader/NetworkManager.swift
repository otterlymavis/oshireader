import Foundation

class NetworkManager {
    static let shared = NetworkManager()
    private init() {}

    var isUITesting: Bool {
        ProcessInfo.processInfo.arguments.contains("--uitesting")
    }

    // MARK: - Backend URL Config

    private let fallbackProductionAPIBase = "https://oshireader.onrender.com"

    var apiBase: String {
        configuredBundleValue(forKey: "OshiReaderAPIBaseURL") ?? fallbackProductionAPIBase
    }

    var environmentName: String {
        configuredBundleValue(forKey: "OshiReaderEnvironment") ?? "Production"
    }

    private func configuredBundleValue(forKey key: String) -> String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: key) as? String else {
            return nil
        }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !trimmed.hasPrefix("$(") else { return nil }
        return trimmed
    }

    // MARK: - Token & Auth

    var adminApiToken: String? {
        if let legacy = UserDefaults.standard.string(forKey: "admin_api_token"),
           !legacy.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
           KeychainHelper.write(key: "admin_api_token", value: legacy) {
            UserDefaults.standard.removeObject(forKey: "admin_api_token")
        }
        if let token = KeychainHelper.read(key: "admin_api_token"),
           !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return token
        }
        let envToken = ProcessInfo.processInfo.environment["OSHI_READER_ADMIN_API_TOKEN"] ?? ""
        let trimmed = envToken.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    func setAdminApiToken(_ token: String?) {
        if let token, !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            KeychainHelper.write(key: "admin_api_token", value: token)
        } else {
            KeychainHelper.delete(key: "admin_api_token")
        }
    }

    var apnsEnvironment: String {
        #if DEBUG
        return "sandbox"
        #else
        return "production"
        #endif
    }

    func applyAdminAuthorization(to request: inout URLRequest) {
        guard let adminApiToken else { return }
        request.setValue("Bearer \(adminApiToken)", forHTTPHeaderField: "Authorization")
    }

    // MARK: - Shared Request Helpers

    // Overridable in tests via a URLSession configured with MockURLProtocol.
    var session: URLSession = .shared

    func apiRequest<T: Decodable>(
        _ url: URL,
        method: String = "GET",
        body: Data? = nil,
        authorized: Bool = false,
        acceptRange: ClosedRange<Int> = 200...299,
        timeout: TimeInterval = 30
    ) async throws -> T {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = timeout
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if authorized { applyAdminAuthorization(to: &request) }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, acceptRange.contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    func apiVoid(
        _ url: URL,
        method: String,
        body: Data? = nil,
        authorized: Bool = false,
        acceptRange: ClosedRange<Int> = 200...299,
        timeout: TimeInterval = 30
    ) async throws {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = timeout
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if authorized { applyAdminAuthorization(to: &request) }
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, acceptRange.contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }
}
