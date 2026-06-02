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

        (firstExistingButton(containing: "Filter") ?? app.buttons["feed.filterButton"]).tap()
        XCTAssertTrue(waitForButton(containing: "Media Only", timeout: 3) != nil)

        let mediaOnlyButton = firstExistingButton(containing: "メディア") ?? firstExistingButton(containing: "Media")
        XCTAssertNotNil(mediaOnlyButton)
        mediaOnlyButton?.tap()
    }

    func testOpenReaderFromFeedAndSave() throws {
        tapTab(index: 0, labels: ["Feed"])

        let headline = app.staticTexts["UITest Oshi headline"]
        XCTAssertTrue(headline.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest Oshi headline") ?? headline).tap()

        let readerModeButton = waitForButton(containing: "Reader Text Mode", timeout: 5)
        XCTAssertNotNil(readerModeButton)
        readerModeButton?.tap()
    }

    func testSavedReaderFlow() throws {
        tapTab(index: 2, labels: ["Saved"])

        let savedTitle = app.staticTexts["UITest saved article"]
        XCTAssertTrue(savedTitle.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest saved article") ?? savedTitle).tap()

        XCTAssertNotNil(waitForButton(containing: "Reader Text Mode", timeout: 5))
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

    private func tapTab(index: Int, labels: [String]) {
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
