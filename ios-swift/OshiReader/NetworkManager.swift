import Foundation

enum APIClientError: Error {
    case httpStatus(Int)
}

class NetworkManager {
    static let shared = NetworkManager()
    private init() {}

    var isUITesting: Bool {
        ProcessInfo.processInfo.arguments.contains("--uitesting")
    }

    var isLiveBackgroundPushTesting: Bool {
        ProcessInfo.processInfo.arguments.contains("--live-background-push-test")
    }

    var isUnitTesting: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
            || NSClassFromString("XCTestCase") != nil
    }

    // MARK: - Backend URL Config

    private let fallbackProductionAPIBase = "https://oshireader.onrender.com"

    var apiBase: String {
        let envBase = ProcessInfo.processInfo.environment["OSHI_READER_API_BASE_URL"] ?? ""
        let trimmedEnvBase = envBase.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedEnvBase.isEmpty {
            return trimmedEnvBase
        }
        return configuredBundleValue(forKey: "OshiReaderAPIBaseURL") ?? fallbackProductionAPIBase
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
        if let provisionedEnvironment = provisionedAPNSEnvironment() {
            return provisionedEnvironment
        }
        if let configuredEnvironment = Self.normalizedAPNSEnvironment(
            configuredBundleValue(forKey: "OshiReaderAPNSEnvironment")
        ) {
            return configuredEnvironment
        }
        #if DEBUG
        return "sandbox"
        #else
        return "production"
        #endif
    }

    static func normalizedAPNSEnvironment(_ value: String?) -> String? {
        guard let value else { return nil }
        switch value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "development", "sandbox":
            return "sandbox"
        case "production":
            return "production"
        default:
            return nil
        }
    }

    private func provisionedAPNSEnvironment() -> String? {
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .isoLatin1),
              let plistStart = text.range(of: "<plist"),
              let plistEnd = text.range(of: "</plist>")
        else { return nil }

        let plistXML = String(text[plistStart.lowerBound..<plistEnd.upperBound])
        guard let plistData = plistXML.data(using: .utf8),
              let plist = try? PropertyListSerialization.propertyList(
                from: plistData,
                options: [],
                format: nil
              ) as? [String: Any],
              let entitlements = plist["Entitlements"] as? [String: Any]
        else { return nil }

        return Self.normalizedAPNSEnvironment(entitlements["aps-environment"] as? String)
    }

    func applyAdminAuthorization(to request: inout URLRequest) {
        guard let adminApiToken else { return }
        request.setValue("Bearer \(adminApiToken)", forHTTPHeaderField: "Authorization")
    }

    func applyDeviceAuthorization(to request: inout URLRequest) {
        request.setValue(apnsDeviceSecret, forHTTPHeaderField: "X-Device-Secret")
        if hasRegisteredAPNSDeviceForCurrentEnvironment,
           let token = registeredAPNSDeviceToken,
           !token.isEmpty {
            request.setValue(token, forHTTPHeaderField: "X-Device-Token")
        }
    }

    // MARK: - Shared Request Helpers

    // Overridable in tests via a URLSession configured with MockURLProtocol.
    var session: URLSession = .shared

    private func dataWithConnectionRetry(for request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: request)
        } catch let error as URLError where error.code == .networkConnectionLost {
            AppLogger.network.warning("Connection lost for \(request.url?.path ?? "request"), retrying request once...")
            return try await session.data(for: request)
        }
    }

    private func retryingAfterDeviceCredentialRefresh(
        _ request: URLRequest,
        deviceAuthorized: Bool,
        statusCode: Int
    ) async -> URLRequest? {
        guard deviceAuthorized, statusCode == 401 else { return nil }
        guard hasRegisteredAPNSDeviceForCurrentEnvironment,
              let token = registeredAPNSDeviceToken,
              !token.isEmpty
        else { return nil }

        do {
            AppLogger.network.notice("Device credential rejected; re-registering APNs token and retrying once")
            try await registerAPNSDeviceToken(token)
            var retriedRequest = request
            applyDeviceAuthorization(to: &retriedRequest)
            return retriedRequest
        } catch {
            AppLogger.network.warning("Device credential recovery failed: \(error.localizedDescription)")
            return nil
        }
    }

    func apiRequest<T: Decodable>(
        _ url: URL,
        method: String = "GET",
        body: Data? = nil,
        authorized: Bool = false,
        deviceAuthorized: Bool = false,
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
        if deviceAuthorized { applyDeviceAuthorization(to: &request) }

        var (data, response) = try await dataWithConnectionRetry(for: request)
        guard var http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        if !acceptRange.contains(http.statusCode),
           let retriedRequest = await retryingAfterDeviceCredentialRefresh(
               request,
               deviceAuthorized: deviceAuthorized,
               statusCode: http.statusCode
           ) {
            (data, response) = try await dataWithConnectionRetry(for: retriedRequest)
            guard let retriedHTTP = response as? HTTPURLResponse else {
                throw URLError(.badServerResponse)
            }
            http = retriedHTTP
        }
        guard acceptRange.contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    func apiVoid(
        _ url: URL,
        method: String,
        body: Data? = nil,
        authorized: Bool = false,
        deviceAuthorized: Bool = false,
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
        if deviceAuthorized { applyDeviceAuthorization(to: &request) }

        var (_, response) = try await dataWithConnectionRetry(for: request)
        guard var http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        if !acceptRange.contains(http.statusCode),
           let retriedRequest = await retryingAfterDeviceCredentialRefresh(
               request,
               deviceAuthorized: deviceAuthorized,
               statusCode: http.statusCode
           ) {
            (_, response) = try await dataWithConnectionRetry(for: retriedRequest)
            guard let retriedHTTP = response as? HTTPURLResponse else {
                throw URLError(.badServerResponse)
            }
            http = retriedHTTP
        }
        guard acceptRange.contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }
}
