import SwiftUI

@main
struct OshiReaderApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    init() {
        LocalDB.shared.resetForUITesting()
    }

    var body: some Scene {
        WindowGroup {
            if NetworkManager.shared.isUnitTesting {
                Color.clear
            } else {
                ContentView()
            }
        }
    }
}
