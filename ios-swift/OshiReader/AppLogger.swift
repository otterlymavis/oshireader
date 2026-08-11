import OSLog

enum AppLogger {
    static let network      = Logger(subsystem: subsystem, category: "network")
    static let persistence  = Logger(subsystem: subsystem, category: "persistence")
    static let notifications = Logger(subsystem: subsystem, category: "notifications")
    static let scraping     = Logger(subsystem: subsystem, category: "scraping")
    static let security     = Logger(subsystem: subsystem, category: "security")

    private static let subsystem = Bundle.main.bundleIdentifier ?? "com.otterpia.oshireader.plus"
}
