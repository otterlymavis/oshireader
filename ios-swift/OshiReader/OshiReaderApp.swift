import SwiftUI

@main
struct OshiReaderApp: App {
    init() {
        LocalDB.shared.resetForUITesting()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
