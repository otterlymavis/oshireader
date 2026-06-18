import XCTest

class OshiReaderRealSourcesUITests: XCTestCase {
    var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        // DO NOT add --uitesting so it hits real network
        app.launch()
        bringAppToForeground()
    }

    func testRealSourcesFeedFetching() throws {
        let randomKeywords = ["Apple", "iOS", "Swift"]

        // Navigate to settings using index 4 or Settings label
        let settingsTab = app.tabBars.buttons["Settings"]
        if !settingsTab.waitForExistence(timeout: 5) {
            app.tabBars.buttons.element(boundBy: 4).tap()
        } else {
            settingsTab.tap()
        }

        for keyword in randomKeywords {
            let addButton = app.buttons["settings.addKeywordButton"]
            XCTAssertTrue(addButton.waitForExistence(timeout: 10))
            addButton.tap()

            let keywordField = app.textFields.firstMatch
            XCTAssertTrue(keywordField.waitForExistence(timeout: 5))
            keywordField.tap()
            keywordField.typeText(keyword)

            let confirmButton = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Add' OR label CONTAINS '追加' OR label CONTAINS '添加' OR label CONTAINS '新增'")).firstMatch
            if confirmButton.exists {
                confirmButton.tap()
            } else {
                app.buttons["settings.confirmAddKeywordButton"].tap()
            }

            // Wait for sheet to disappear
            _ = addButton.waitForExistence(timeout: 5)
        }

        // Go to feed
        bringAppToForeground()
        let feedTab = app.tabBars.buttons["Feed"]
        if !feedTab.waitForExistence(timeout: 5) {
            app.tabBars.buttons.element(boundBy: 0).tap()
        } else {
            feedTab.tap()
        }

        let refreshButton = app.buttons["feed.refreshButton"]
        XCTAssertTrue(refreshButton.waitForExistence(timeout: 10))
        refreshButton.tap()

        // Wait for feed items to populate (real network call)
        let anyFeedItem = app.buttons["feed.card"]
        let loaded = anyFeedItem.waitForExistence(timeout: 20)

        XCTAssertTrue(loaded, "Failed to load feed items from real sources")

        // Scroll down to view the feed
        app.swipeUp()
        app.swipeUp()
        app.swipeUp()
    }

    private func bringAppToForeground() {
        if app.state == .runningForeground { return }
        app.activate()
        if app.state != .runningForeground {
            app.launch()
        }
    }

}
