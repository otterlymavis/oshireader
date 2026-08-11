import Foundation
import Security

enum KeychainHelper {
    private static let service = Bundle.main.bundleIdentifier ?? "com.otterpia.oshireader.plus"
    private static let fallbackLock = NSLock()
    private static var testFallbackStore: [String: Data] = [:]

    // Serializes every real Keychain call this type makes. Without this, a
    // read()'s accessibility migration (delete + re-add, see below) could
    // interleave with a concurrent write() to the same key and silently
    // clobber a freshly written value with the stale copy read() captured
    // before the migration began.
    private static let keychainLock = NSLock()
    private static var migratedKeys: Set<String> = []
    private static var migrationAttemptFailedKeys: Set<String> = []

    private static var isRunningTests: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil ||
        NSClassFromString("XCTestCase") != nil
    }

    private static func fallbackKey(_ key: String) -> String {
        "\(service):\(key)"
    }

    private static func baseQuery(_ key: String) -> [CFString: Any] {
        [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: key]
    }

    private static func addItem(key: String, data: Data) -> Bool {
        var addQuery = baseQuery(key)
        addQuery[kSecValueData] = data
        addQuery[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlock
        let added = SecItemAdd(addQuery as CFDictionary, nil) == errSecSuccess
        if added { migratedKeys.insert(fallbackKey(key)) }
        return added
    }

    static func read(key: String) -> String? {
        keychainLock.lock()
        defer { keychainLock.unlock() }
        var query = baseQuery(key)
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else {
            guard isRunningTests else { return nil }
            fallbackLock.lock()
            defer { fallbackLock.unlock() }
            guard let data = testFallbackStore[fallbackKey(key)] else { return nil }
            return String(data: data, encoding: .utf8)
        }
        migrateToAfterFirstUnlockIfNeeded(key: key, data: data)
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func write(key: String, value: String) -> Bool {
        keychainLock.lock()
        defer { keychainLock.unlock() }
        let data = Data(value.utf8)
        // Update-in-place first so a failed re-write (e.g. device locked) can
        // never delete the previously stored value before confirming the new
        // one landed. kSecAttrAccessible is an add-only attribute per Apple's
        // "SecItem: Pitfalls and Best Practices" guidance — it cannot be
        // changed via SecItemUpdate, so it's only set here on first creation.
        // Migrating an already-existing item to AfterFirstUnlock happens
        // separately in read(), via migrateToAfterFirstUnlockIfNeeded.
        let updateStatus = SecItemUpdate(baseQuery(key) as CFDictionary, [kSecValueData: data] as CFDictionary)
        let didWrite: Bool
        switch updateStatus {
        case errSecSuccess:
            didWrite = true
        case errSecItemNotFound:
            didWrite = addItem(key: key, data: data)
        case errSecInteractionNotAllowed:
            // Device is locked — falling back to delete+add here would risk
            // destroying the existing value if the add then also fails, so
            // leave the item alone and let the caller retry once unlocked.
            didWrite = false
        default:
            // Some other, unexpected failure (e.g. an ambiguous/duplicate
            // item left behind by a prior OS bug) that update can't recover
            // from. Fall back to delete+add to self-heal, matching the
            // unconditional delete+add this migrated away from.
            AppLogger.security.warning("Keychain SecItemUpdate failed status=\(updateStatus, privacy: .public); falling back to delete+add")
            SecItemDelete(baseQuery(key) as CFDictionary)
            didWrite = addItem(key: key, data: data)
            if !didWrite {
                AppLogger.security.warning("Keychain delete+add fallback also failed")
            }
        }
        guard isRunningTests else { return didWrite }
        fallbackLock.lock()
        testFallbackStore[fallbackKey(key)] = data
        fallbackLock.unlock()
        return true
    }

    static func delete(key: String) {
        keychainLock.lock()
        defer { keychainLock.unlock() }
        SecItemDelete(baseQuery(key) as CFDictionary)
        let cacheKey = fallbackKey(key)
        migratedKeys.remove(cacheKey)
        migrationAttemptFailedKeys.remove(cacheKey)
        guard isRunningTests else { return }
        fallbackLock.lock()
        testFallbackStore.removeValue(forKey: cacheKey)
        fallbackLock.unlock()
    }

    // kSecAttrAccessible can only be set when an item is created, so an item
    // stored by an older build under the default WhenUnlocked class has to
    // be deleted and re-added to move to AfterFirstUnlock. Only attempted
    // right after a successful read of `data`, since that proves the device
    // is currently unlocked and the delete+re-add is safe to perform now. If
    // the re-add fails after the delete succeeded, this makes a best-effort
    // attempt to restore the original value under the default accessibility
    // rather than leave the key empty; that restore attempt can itself fail
    // (e.g. a transient Keychain daemon error), in which case the key is
    // left empty and the next write() call will recreate it.
    // Callers hold keychainLock for the duration, and migratedKeys skips the
    // attribute lookup entirely once a key is known to be migrated, so this
    // adds no extra Keychain round-trip on the hot path after the first call.
    private static func migrateToAfterFirstUnlockIfNeeded(key: String, data: Data) {
        let cacheKey = fallbackKey(key)
        guard !migratedKeys.contains(cacheKey), !migrationAttemptFailedKeys.contains(cacheKey) else { return }

        var attrQuery = baseQuery(key)
        attrQuery[kSecReturnAttributes] = true
        attrQuery[kSecMatchLimit] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(attrQuery as CFDictionary, &result) == errSecSuccess,
              let attrs = result as? [CFString: Any],
              let currentAccessible = attrs[kSecAttrAccessible] as? String
        else { return }
        guard currentAccessible != (kSecAttrAccessibleAfterFirstUnlock as String) else {
            migratedKeys.insert(cacheKey)
            return
        }

        guard SecItemDelete(baseQuery(key) as CFDictionary) == errSecSuccess else { return }

        if addItem(key: key, data: data) {
            return
        }

        // The AfterFirstUnlock re-add failed after the delete already
        // succeeded. Don't retry on every subsequent read this launch —
        // that would reopen the delete/re-add gap repeatedly instead of
        // accepting the item stays under its original accessibility until
        // the next launch tries again once.
        migrationAttemptFailedKeys.insert(cacheKey)
        AppLogger.security.warning("Keychain AfterFirstUnlock re-add failed after migration delete; attempting restore")

        var restoreQuery = baseQuery(key)
        restoreQuery[kSecValueData] = data
        if SecItemAdd(restoreQuery as CFDictionary, nil) != errSecSuccess {
            AppLogger.security.warning("Keychain migration restore also failed; key is empty until next write()")
        }
    }
}
