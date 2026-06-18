import Foundation
import Security

enum KeychainHelper {
    private static let service = Bundle.main.bundleIdentifier ?? "com.otterpia.oshireader.plus"
    private static let fallbackLock = NSLock()
    private static var testFallbackStore: [String: Data] = [:]

    private static var isRunningTests: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil ||
        NSClassFromString("XCTestCase") != nil
    }

    private static func fallbackKey(_ key: String) -> String {
        "\(service):\(key)"
    }

    static func read(key: String) -> String? {
        let query: [CFString: Any] = [
            kSecClass:       kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: key,
            kSecReturnData:  true,
            kSecMatchLimit:  kSecMatchLimitOne
        ]
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else {
            guard isRunningTests else { return nil }
            fallbackLock.lock()
            defer { fallbackLock.unlock() }
            guard let data = testFallbackStore[fallbackKey(key)] else { return nil }
            return String(data: data, encoding: .utf8)
        }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func write(key: String, value: String) -> Bool {
        let data = Data(value.utf8)
        let query: [CFString: Any] = [
            kSecClass:       kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: key,
            kSecValueData:   data
        ]
        SecItemDelete(query as CFDictionary)
        let didWrite = SecItemAdd(query as CFDictionary, nil) == errSecSuccess
        guard isRunningTests else { return didWrite }
        fallbackLock.lock()
        testFallbackStore[fallbackKey(key)] = data
        fallbackLock.unlock()
        return true
    }

    static func delete(key: String) {
        let query: [CFString: Any] = [
            kSecClass:       kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: key
        ]
        SecItemDelete(query as CFDictionary)
        guard isRunningTests else { return }
        fallbackLock.lock()
        testFallbackStore.removeValue(forKey: fallbackKey(key))
        fallbackLock.unlock()
    }
}
