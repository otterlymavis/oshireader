import XCTest

final class OshiReaderUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["--uitesting"]
        app.launch()
    }

    func testAddKeywordFlow() throws {
        tapTab(index: 4, labels: ["Settings"])

        app.buttons["settings.addKeywordButton"].tap()
        let keywordField = firstExistingTextField(labels: ["settings.keywordField", "Enter keyword..."]) ?? app.textFields.firstMatch
        XCTAssertTrue(keywordField.waitForExistence(timeout: 3))

        keywordField.tap()
        keywordField.typeText("New UI Keyword")
        (firstExistingButton(containing: "追加") ?? app.buttons["settings.confirmAddKeywordButton"]).tap()

        XCTAssertTrue(app.staticTexts["New UI Keyword"].waitForExistence(timeout: 3))
    }

    func testRefreshFeedAndFilterSheet() throws {
        tapTab(index: 0, labels: ["Feed"])

        XCTAssertTrue(app.buttons["feed.refreshButton"].waitForExistence(timeout: 3))
        app.buttons["feed.refreshButton"].tap()

        let filterButton = firstFeedFilterButton() ?? app.buttons["feed.filterButton"]
        XCTAssertTrue(filterButton.waitForExistence(timeout: 3))
        filterButton.tap()
        let mediaOnlyButton = app.buttons["filter.mediaOnlyButton"]
        XCTAssertTrue(mediaOnlyButton.waitForExistence(timeout: 3))
        mediaOnlyButton.tap()
    }

    func testOpenReaderFromFeedAndSave() throws {
        tapTab(index: 0, labels: ["Feed"])

        let headline = app.staticTexts["UITest Oshi headline"]
        XCTAssertTrue(headline.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest Oshi headline") ?? headline).tap()

        let readerModeButton = waitForButton(identifier: "reader.modeToggleButton", timeout: 5)
        XCTAssertNotNil(readerModeButton)
        readerModeButton?.tap()
    }

    func testSavedReaderFlow() throws {
        tapTab(index: 2, labels: ["Saved"])

        let savedTitle = app.staticTexts["UITest saved article"]
        XCTAssertTrue(savedTitle.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest saved article") ?? savedTitle).tap()

        XCTAssertNotNil(waitForButton(identifier: "reader.modeToggleButton", timeout: 5))
    }

    func testSearchFlow() throws {
        tapTab(index: 1, labels: ["Search"])

        let searchField = firstExistingTextField(labels: ["search.field", "Search articles..."]) ?? app.textFields.firstMatch
        XCTAssertTrue(searchField.waitForExistence(timeout: 3))
        searchField.tap()
        searchField.typeText("headline")

        XCTAssertTrue(app.staticTexts["UITest Oshi headline"].waitForExistence(timeout: 3))
    }

    func testAvatarEditorFlow() throws {
        tapTab(index: 3, labels: ["My Oshi"])

        let editButton = app.buttons["oshi.editButton.UITest Oshi"]
        XCTAssertTrue(editButton.waitForExistence(timeout: 3))
        editButton.tap()

        XCTAssertTrue(app.staticTexts["✨ UITest Oshi"].waitForExistence(timeout: 5))
        let saveButton = waitForButton(containing: "保存", timeout: 3)
        XCTAssertNotNil(saveButton)
        saveButton?.tap()
    }

    func testSettingsPrivacyPolicyFlow() throws {
        tapTab(index: 4, labels: ["Settings"])

        let comicSansButton = waitForButton(containing: "Comic", timeout: 2, swipes: 2)
        XCTAssertNotNil(comicSansButton)
        comicSansButton?.tap()

        let largeButton = waitForButton(containing: "Large", timeout: 2, swipes: 1)
        XCTAssertNotNil(largeButton)
        largeButton?.tap()

        app.swipeUp()
        app.swipeUp()

        let privacyLink = firstExistingButton(containing: "Privacy Policy") ?? app.buttons["settings.privacyPolicyLink"]
        XCTAssertTrue(privacyLink.waitForExistence(timeout: 3))
        privacyLink.tap()

        XCTAssertTrue(app.staticTexts["Data Stored on This Device"].exists)
    }

    func testSettingsNotificationControls() throws {
        tapTab(index: 4, labels: ["Settings"])

        XCTAssertTrue(waitForElement(identifier: "settings.notificationStatus", timeout: 2, swipes: 4).exists)
        XCTAssertTrue(waitForElement(identifier: "settings.enableNotificationsButton", timeout: 2, swipes: 1).exists)
        XCTAssertTrue(waitForElement(identifier: "settings.testNotificationButton", timeout: 2, swipes: 1).exists)
    }

    private func tapTab(index: Int, labels: [String]) {
        let tabIdentifiers = ["tab.feed", "tab.search", "tab.saved", "tab.oshi", "tab.settings"]
        if tabIdentifiers.indices.contains(index) {
            let tabElement = app.descendants(matching: .any)[tabIdentifiers[index]]
            if tabElement.waitForExistence(timeout: 1) {
                tabElement.tap()
                return
            }
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

    private func firstExistingButton(containing text: String) -> XCUIElement? {
        let buttons = app.buttons.allElementsBoundByIndex
        return buttons.first { button in
            button.exists && button.label.localizedCaseInsensitiveContains(text)
        }
    }

    private func firstFeedFilterButton() -> XCUIElement? {
        let labels = ["Filter", "フィルター", "篩選", "筛选"]
        let scopedButtons = app.buttons.matching(identifier: "feed.screen").allElementsBoundByIndex
        return scopedButtons.first { button in
            button.exists && labels.contains { button.label.localizedCaseInsensitiveContains($0) }
        } ?? app.buttons.allElementsBoundByIndex.first { button in
            button.exists && labels.contains { button.label.localizedCaseInsensitiveContains($0) }
        }
    }

    private func waitForButton(identifier: String, timeout: TimeInterval) -> XCUIElement? {
        let button = app.buttons[identifier]
        return button.waitForExistence(timeout: timeout) ? button : nil
    }

    private func waitForAnyButton(containing texts: [String], timeout: TimeInterval) -> XCUIElement? {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if let button = texts.compactMap({ firstExistingButton(containing: $0) }).first {
                return button
            }
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        return texts.compactMap { firstExistingButton(containing: $0) }.first
    }

    private func waitForButton(containing text: String, timeout: TimeInterval) -> XCUIElement? {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if let button = firstExistingButton(containing: text) {
                return button
            }
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        return firstExistingButton(containing: text)
    }

    private func waitForButton(containing text: String, timeout: TimeInterval, swipes: Int) -> XCUIElement? {
        for attempt in 0...swipes {
            if let button = waitForButton(containing: text, timeout: timeout) {
                return button
            }
            if attempt < swipes {
                app.swipeUp()
            }
        }
        return nil
    }

    private func waitForElement(identifier: String, timeout: TimeInterval, swipes: Int) -> XCUIElement {
        let element = app.descendants(matching: .any)[identifier]
        for attempt in 0...swipes {
            if element.waitForExistence(timeout: timeout) {
                return element
            }
            if attempt < swipes {
                app.swipeUp()
            }
        }
        return element
    }

    private func firstExistingTextField(labels: [String]) -> XCUIElement? {
        for label in labels {
            let field = app.textFields[label]
            if field.exists {
                return field
            }
        }
        return nil
    }
}
