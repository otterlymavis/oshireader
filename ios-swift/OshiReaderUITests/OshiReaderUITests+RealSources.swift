import XCTest

class OshiReaderRealSourcesUITests: XCTestCase {
    var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        guard ProcessInfo.processInfo.environment["OSHI_READER_RUN_LIVE_UI_TESTS"] == "1" else {
            throw XCTSkip("Set OSHI_READER_RUN_LIVE_UI_TESTS=1 to run live source UI checks")
        }
        app = XCUIApplication()
        // DO NOT add --uitesting so it hits real network
        app.launch()
        bringAppToForeground()
    }

    func testRealSourcesFeedFetching() throws {
        let randomKeywords = ["Apple", "iOS", "Swift"]

        tapTab(index: 4, identifier: "tab.settings", labels: ["Settings"])

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
        tapTab(index: 0, identifier: "tab.feed", labels: ["Feed"])

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

    private func tapTab(index: Int, identifier: String, labels: [String]) {
        let identifiedButtons = app.buttons.matching(identifier: identifier)
        let identifiedButton = identifiedButtons.firstMatch
        if identifiedButton.waitForExistence(timeout: 5) {
            let target = identifiedButtons.allElementsBoundByIndex.first(where: { $0.isHittable }) ?? identifiedButton
            target.tap()
            return
        }

        for label in labels {
            let button = app.tabBars.buttons[label]
            if button.waitForExistence(timeout: 1) {
                button.tap()
                return
            }
        }

        let indexedButton = app.tabBars.buttons.element(boundBy: index)
        if indexedButton.waitForExistence(timeout: 2) {
            indexedButton.tap()
            return
        }

        XCTFail("Could not find tab at index \(index) with labels \(labels)")
    }

}
