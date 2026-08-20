import XCTest

final class OshiReaderUITests: XCTestCase {
    private var app: XCUIApplication!
    private var liveUITestsEnabled: Bool {
        ProcessInfo.processInfo.environment["OSHI_READER_RUN_LIVE_UI_TESTS"] == "1"
    }

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        if name.contains("LiveBackgroundPush"), liveUITestsEnabled {
            app.launchArguments = ["--live-background-push-test"]
            app.launchEnvironment["OSHI_READER_API_BASE_URL"] = "https://oshireader.onrender.com"
        } else {
            app.launchArguments = uiTestingLaunchArguments()
        }
        app.launch()
    }

    private func uiTestingLaunchArguments(_ extraArguments: [String] = []) -> [String] {
        ["--uitesting", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"] + extraArguments
    }

    func testAddKeywordFlow() throws {
        tapTab(index: 4, labels: ["Settings"])

        XCTAssertFalse(app.buttons["settings.keywordMode.UITest Oshi"].exists)

        app.buttons["settings.addKeywordButton"].tap()

        // Sheet may take a moment; wait up to 5 s for the only non-secure text field to appear
        let keywordField = app.textFields.firstMatch
        XCTAssertTrue(keywordField.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", "media only")).firstMatch.exists)
        keywordField.tap()
        keywordField.typeText("New UI Keyword")

        // Confirm button label is locale-dependent — try all known translations then fall back to ID
        let confirmButton = waitForAnyButton(containing: ["Add", "追加", "添加", "新增"], timeout: 5)
            ?? waitForButton(identifier: "settings.confirmAddKeywordButton", timeout: 3)
        XCTAssertNotNil(confirmButton, "Confirm add-keyword button not found")
        confirmButton?.tap()

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
        app.terminate()
        app.launchArguments = uiTestingLaunchArguments(["--uitesting-reader-images"])
        app.launch()
        tapTab(index: 0, labels: ["Feed"])

        let headline = app.staticTexts["UITest Oshi headline"]
        XCTAssertTrue(headline.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest Oshi headline") ?? headline).tap()

        let readerModeButton = waitForButton(identifier: "reader.modeToggleButton", timeout: 10)
        XCTAssertNotNil(readerModeButton)
        assertReaderLoadedWithoutFallbackBanner()

        tapToolbarButton(identifier: "reader.imageActionsMenuButton")
        let selectImagesButton = app.buttons["reader.selectImagesButton"]
        XCTAssertTrue(selectImagesButton.waitForExistence(timeout: 3))
        selectImagesButton.tap()
        let cancelSelectionButton = app.buttons["reader.cancelImageSelectionButton"]
        let saveSelectedImagesButton = app.buttons["reader.saveSelectedImagesButton"]
        XCTAssertTrue(cancelSelectionButton.waitForExistence(timeout: 3))
        XCTAssertTrue(saveSelectedImagesButton.waitForExistence(timeout: 3))
        XCTAssertFalse(saveSelectedImagesButton.isEnabled)

        let firstImage = app.buttons["fixture image one"]
        let secondImage = app.buttons["fixture image two"]
        XCTAssertTrue(firstImage.waitForExistence(timeout: 3))
        XCTAssertTrue(secondImage.waitForExistence(timeout: 3))
        firstImage.tap()
        XCTAssertTrue(saveSelectedImagesButton.waitForExistence(timeout: 3))
        XCTAssertTrue(saveSelectedImagesButton.isEnabled)
        XCTAssertTrue(saveSelectedImagesButton.label.contains("1"))
        secondImage.tap()
        let twoSelected = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label CONTAINS %@", "2"),
            object: saveSelectedImagesButton
        )
        XCTAssertEqual(XCTWaiter().wait(for: [twoSelected], timeout: 3), .completed)
        saveSelectedImagesButton.tap()
        let saveStatusAlert = app.alerts.firstMatch
        XCTAssertTrue(saveStatusAlert.waitForExistence(timeout: 10))
        let saveStatusMessage = saveStatusAlert.staticTexts.element(boundBy: 1)
        XCTAssertTrue(saveStatusMessage.exists)
        XCTAssertFalse(saveStatusMessage.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        XCTAssertTrue(toolbarButtonIsIdle(identifier: "reader.imageActionsMenuButton", timeout: 10))

        readerModeButton?.tap()
        assertReaderLoadedWithoutFallbackBanner()
    }

    func testOpenReaderFromFeedTapOnLinkedImageNavigates() throws {
        app.terminate()
        app.launchArguments = uiTestingLaunchArguments(["--uitesting-reader-images"])
        app.launch()
        tapTab(index: 0, labels: ["Feed"])

        let headline = app.staticTexts["UITest Oshi headline"]
        XCTAssertTrue(headline.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest Oshi headline") ?? headline).tap()

        XCTAssertNotNil(waitForButton(identifier: "reader.modeToggleButton", timeout: 10))
        assertReaderLoadedWithoutFallbackBanner()

        // Outside of the explicit save-images menu, tapping an image must not
        // show a save/share sheet — it's normal page content.
        let firstImage = app.buttons["fixture image one"]
        XCTAssertTrue(firstImage.waitForExistence(timeout: 3))
        firstImage.tap()
        XCTAssertFalse(app.sheets.firstMatch.waitForExistence(timeout: 2))
        XCTAssertFalse(app.alerts.firstMatch.waitForExistence(timeout: 1))

        // A photo that IS a link (the reported bug: tapping it showed the
        // save sheet instead of navigating) must actually navigate.
        let linkedImage = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", "fixture linked image"))
            .firstMatch
        XCTAssertTrue(linkedImage.waitForExistence(timeout: 3))
        linkedImage.tap()
        XCTAssertFalse(app.sheets.firstMatch.waitForExistence(timeout: 2))
        XCTAssertFalse(app.alerts.firstMatch.waitForExistence(timeout: 1))
        let navStatus = app.staticTexts["navigated:#fixture-target"]
        XCTAssertTrue(navStatus.waitForExistence(timeout: 3), "Tapping a linked image should navigate instead of being swallowed by the save-image handler")
    }

    func testOpenReaderFromFeedSaveAllImages() throws {
        app.terminate()
        app.launchArguments = uiTestingLaunchArguments(["--uitesting-reader-images"])
        app.launch()
        tapTab(index: 0, labels: ["Feed"])

        let headline = app.staticTexts["UITest Oshi headline"]
        XCTAssertTrue(headline.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest Oshi headline") ?? headline).tap()

        let readerModeButton = waitForButton(identifier: "reader.modeToggleButton", timeout: 10)
        XCTAssertNotNil(readerModeButton)
        assertReaderLoadedWithoutFallbackBanner()

        tapToolbarButton(identifier: "reader.imageActionsMenuButton")
        let saveAllImagesButton = app.buttons["reader.saveAllImagesButton"]
        XCTAssertTrue(saveAllImagesButton.waitForExistence(timeout: 3))
        saveAllImagesButton.tap()

        let saveStatusAlert = app.alerts.firstMatch
        XCTAssertTrue(saveStatusAlert.waitForExistence(timeout: 10))
        let saveStatusMessage = saveStatusAlert.staticTexts.element(boundBy: 1)
        XCTAssertTrue(saveStatusMessage.exists)
        XCTAssertFalse(saveStatusMessage.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        XCTAssertTrue(toolbarButtonIsIdle(identifier: "reader.imageActionsMenuButton", timeout: 10))

        readerModeButton?.tap()
        assertReaderLoadedWithoutFallbackBanner()
    }

    func testOpenReaderFromFeedSkipsStaleBackendMatchRedirect() throws {
        app.terminate()
        app.launchArguments = uiTestingLaunchArguments(["--uitesting-redirect-feed"])
        app.launch()

        tapTab(index: 0, labels: ["Feed"])

        XCTAssertFalse(app.staticTexts["UITest Oshi stale redirect headline"].waitForExistence(timeout: 2))
        let headline = app.staticTexts["UITest Oshi stable headline"]
        XCTAssertTrue(headline.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest Oshi stable headline") ?? headline).tap()

        XCTAssertNotNil(waitForButton(identifier: "reader.modeToggleButton", timeout: 10))
        assertReaderLoadedWithoutFallbackBanner()
    }

    func testAllRegisteredPlatformFeedItemsDisplayAndOpen() throws {
        let platforms = [
            ("youtube", "YouTube"),
            ("niconico", "NicoNico"),
            ("tver", "TVer"),
            ("twitter", "X"),
            ("note", "Note"),
            ("girlschannel", "GirlsChannel"),
            ("5ch", "5ch"),
            ("news", "News"),
            ("yahoonews", "YahooNews"),
            ("mdpr", "ModelPress"),
            ("oricon", "Oricon"),
            ("smartnews", "SmartNews"),
            ("ameblo", "Ameblo"),
            ("aera", "AERA dot."),
            ("hochi", "Hochi"),
            ("sponichi", "Sponichi"),
            ("livedoor", "Livedoor"),
            ("mantanweb", "Mantan Web"),
            ("realsound", "Real Sound"),
            ("cinemacafe", "CinemaCafe"),
            ("thetv", "TheTV"),
            ("natalie", "Natalie"),
            ("billboardjapan", "Billboard Japan"),
            ("soompi", "Soompi"),
            ("allkpop", "allkpop"),
            ("kpopofficial", "KpopOfficial"),
            ("barks", "BARKS"),
            ("custom", "Custom Feeds"),
        ]

        for (platformId, platformName) in platforms {
            app.terminate()
            app.launchArguments = uiTestingLaunchArguments(["--uitesting-single-platform-feed", platformId])
            app.launch()

            let title = "UITest Oshi \(platformName) item"
            let cardTitle = app.staticTexts[title]
            XCTAssertTrue(cardTitle.waitForExistence(timeout: 3), "Missing feed card for \(platformId)")

            guard let card = firstExistingButton(containing: title) else {
                XCTFail("Missing tappable feed card for \(platformId)")
                continue
            }
            card.tap()

            XCTAssertTrue(
                app.buttons["reader.bookmarkButton"].waitForExistence(timeout: 5),
                "Reader did not open for \(platformId)"
            )
            assertReaderLoadedWithoutFallbackBanner()

            tapReaderBackButton(platformId: platformId)
        }
    }

    func testAllRegisteredPlatformFeedItemsAreSortedNewestToOldest() throws {
        app.terminate()
        app.launchArguments = uiTestingLaunchArguments(["--uitesting-all-platform-sort-feed"])
        app.launch()

        let expectedTitles = [
            "UITest Oshi YouTube item",
            "UITest Oshi NicoNico item",
            "UITest Oshi TVer item",
            "UITest Oshi X item",
            "UITest Oshi Note item",
            "UITest Oshi GirlsChannel item",
            "UITest Oshi 5ch item",
            "UITest Oshi News item",
            "UITest Oshi YahooNews item",
            "UITest Oshi ModelPress item",
            "UITest Oshi Oricon item",
            "UITest Oshi SmartNews item",
            "UITest Oshi Ameblo item",
            "UITest Oshi AERA dot. item",
            "UITest Oshi Hochi item",
            "UITest Oshi Sponichi item",
            "UITest Oshi Livedoor item",
            "UITest Oshi Mantan Web item",
            "UITest Oshi Real Sound item",
            "UITest Oshi CinemaCafe item",
            "UITest Oshi TheTV item",
            "UITest Oshi Natalie item",
            "UITest Oshi Billboard Japan item",
            "UITest Oshi Soompi item",
            "UITest Oshi allkpop item",
            "UITest Oshi KpopOfficial item",
            "UITest Oshi BARKS item",
            "UITest Oshi Custom Feeds item",
        ]

        XCTAssertTrue(app.staticTexts[expectedTitles[0]].waitForExistence(timeout: 3))
        assertFeedTitlesAppearInOrder(expectedTitles)
    }

    func testStopFollowingFromFeedCard() throws {
        tapTab(index: 0, labels: ["Feed"])

        let headline = app.staticTexts["UITest Oshi headline"]
        XCTAssertTrue(headline.waitForExistence(timeout: 3))

        let card = app.buttons["feed.card"]
        XCTAssertTrue(card.waitForExistence(timeout: 3))
        card.swipeLeft()

        let hidePost = waitForAnyButton(
            containing: ["Hide Post", "投稿を非表示", "隱藏貼文", "隐藏帖子"],
            timeout: 3
        )
        XCTAssertNotNil(hidePost)
        hidePost?.tap()

        let confirm = ["Hide Post", "非表示にする", "隱藏貼文", "隐藏帖子"]
            .map { app.alerts.buttons[$0] }
            .first { $0.waitForExistence(timeout: 1) }
        XCTAssertNotNil(confirm)
        confirm?.tap()

        XCTAssertFalse(headline.waitForExistence(timeout: 2))

        tapTab(index: 4, labels: ["Settings"])
        XCTAssertTrue(app.staticTexts["UITest Oshi"].waitForExistence(timeout: 2))
    }

    func testSavedReaderFlow() throws {
        tapTab(index: 2, labels: ["Saved"])

        let savedTitle = app.staticTexts["UITest saved article"]
        XCTAssertTrue(savedTitle.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest saved article") ?? savedTitle).tap()

        XCTAssertNotNil(waitForButton(identifier: "reader.modeToggleButton", timeout: 10))
        assertReaderLoadedWithoutFallbackBanner()
    }

    func testSearchFlow() throws {
        app.terminate()
        app.launchArguments = uiTestingLaunchArguments(["--uitesting-start-search"])
        app.launch()

        // Keyword chip from the seeded UITest term should be visible
        XCTAssertTrue(app.staticTexts["UITest Oshi"].waitForExistence(timeout: 3))

        // onAppear pre-fills the field with the first active term's keyword.
        XCTAssertTrue(app.buttons["Clear search"].waitForExistence(timeout: 3))
    }

    func testSearchClearShowsEmptyKeywordPrompt() throws {
        app.terminate()
        app.launchArguments = uiTestingLaunchArguments(["--uitesting-start-search"])
        app.launch()

        XCTAssertTrue(app.staticTexts["UITest Oshi"].waitForExistence(timeout: 3))

        let clearButton = app.buttons["Clear search"]
        XCTAssertTrue(clearButton.waitForExistence(timeout: 3))
        clearButton.tap()

        XCTAssertTrue(app.staticTexts["search.emptyKeywordTitle"].waitForExistence(timeout: 3))
        XCTAssertFalse(clearButton.exists)
    }

    func testSearchResultOpensReaderInWebMode() throws {
        app.terminate()
        app.launchArguments = uiTestingLaunchArguments(["--uitesting-start-search", "--uitesting-search-social"])
        app.launch()

        let xSearchLink = app.buttons["search.link.x"]
        XCTAssertTrue(xSearchLink.waitForExistence(timeout: 3))
        xSearchLink.tap()

        let modeButton = app.buttons["reader.modeToggleButton"]
        XCTAssertTrue(modeButton.waitForExistence(timeout: 10))
        XCTAssertEqual(modeButton.value as? String, "web")

        let loadingState = app.otherElements["reader.loadingState"]
        let loadFinished = NSPredicate(format: "exists == false")
        expectation(for: loadFinished, evaluatedWith: loadingState)
        waitForExpectations(timeout: 15)
        XCTAssertFalse(app.otherElements["reader.failedState"].exists)
    }

    func testXSearchRendersRealContentNotBlank() throws {
        app.terminate()
        app.launchArguments = uiTestingLaunchArguments(["--uitesting-start-search", "--uitesting-search-social"])
        app.launch()

        let xSearchLink = app.buttons["search.link.x"]
        XCTAssertTrue(xSearchLink.waitForExistence(timeout: 3))
        xSearchLink.tap()

        let loadingState = app.otherElements["reader.loadingState"]
        let loadFinished = NSPredicate(format: "exists == false")
        expectation(for: loadFinished, evaluatedWith: loadingState)
        waitForExpectations(timeout: 15)
        XCTAssertFalse(app.otherElements["reader.failedState"].exists)

        // x.com should render its own page (search results, or its login prompt for a guest
        // session) rather than our in-app fallback banner or a blank page.
        XCTAssertFalse(app.buttons["reader.signInButton"].exists, "Should not need the fallback banner when x.com renders normally")
    }

    func testXSearchSignInFallbackOffersInAppLoginAndReturn() throws {
        app.terminate()
        app.launchArguments = uiTestingLaunchArguments(["--uitesting-start-search", "--uitesting-search-social"])
        app.launch()

        let xSearchLink = app.buttons["search.link.x"]
        XCTAssertTrue(xSearchLink.waitForExistence(timeout: 3))
        xSearchLink.tap()

        let signInButton = app.buttons["reader.signInButton"]
        guard signInButton.waitForExistence(timeout: 15) else {
            throw XCTSkip("x.com loaded normally this run; the sign-in fallback only appears when x.com fails to load")
        }
        signInButton.tap()

        let returnButton = app.buttons["reader.signInReturnButton"]
        XCTAssertTrue(returnButton.waitForExistence(timeout: 10), "Expected return banner while signing in to X in-app")
        returnButton.tap()

        XCTAssertFalse(app.buttons["reader.signInReturnButton"].exists)
    }

    func testAvatarEditorFlow() throws {
        tapTab(index: 3, labels: ["My Oshi"])

        XCTAssertFalse(app.staticTexts["📄 All info"].exists)
        XCTAssertFalse(app.staticTexts["📹 Media only"].exists)

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

        let privacyLink = waitForElement(identifier: "settings.privacyPolicyLink", timeout: 2, swipes: 3)
        XCTAssertTrue(privacyLink.waitForExistence(timeout: 3))
        privacyLink.tap()

        XCTAssertTrue(app.staticTexts["Data Stored on This Device"].waitForExistence(timeout: 3))
    }

    func testSettingsNotificationControls() throws {
        tapTab(index: 4, labels: ["Settings"])

        let keywordBell = waitForElement(identifier: "settings.keywordBell.UITest Oshi", timeout: 2, swipes: 3)
        XCTAssertTrue(keywordBell.exists, "Expected the seeded keyword to expose a notification bell")

        XCTAssertTrue(waitForElement(identifier: "settings.notificationStatus", timeout: 2, swipes: 4).exists)
        XCTAssertTrue(waitForElement(identifier: "settings.notificationSetupHint", timeout: 2, swipes: 0).exists)

        // Only one button renders depending on permission state — verify at least one exists
        let hasEnable = waitForElement(identifier: "settings.enableNotificationsButton", timeout: 2, swipes: 1).exists
        let hasOpenSettings = waitForElement(identifier: "settings.openSettingsButton", timeout: 1, swipes: 0).exists
        XCTAssertTrue(hasEnable || hasOpenSettings, "Expected a notification control button")
    }

    func testSettingsKeywordRowTextTapDoesNotOpenAvatarEditor() throws {
        tapTab(index: 4, labels: ["Settings"])

        let keyword = waitForElement(label: "UITest Oshi", timeout: 2, swipes: 3)
        XCTAssertTrue(keyword.exists, "Expected the seeded keyword to be visible")

        keyword.tap()
        XCTAssertFalse(
            app.buttons["avatar.saveButton"].waitForExistence(timeout: 1),
            "Tapping the keyword row text should not open the avatar editor"
        )
    }

    private func tapTab(index: Int, labels: [String]) {
        let tabIdentifiers = ["tab.feed", "tab.search", "tab.saved", "tab.oshi", "tab.settings"]
        if tabIdentifiers.indices.contains(index) {
            let tabButtons = app.buttons.matching(identifier: tabIdentifiers[index])
            let tabButton = tabButtons.firstMatch
            if tabButton.waitForExistence(timeout: 1) {
                let target = tabButtons.allElementsBoundByIndex.first(where: { $0.isHittable }) ?? tabButton
                target.tap()
                return
            }
        }

        let localizedLabels = [
            ["Feed", "フィード", "動態", "动态"],
            ["Search", "検索", "搜尋", "搜索"],
            ["Saved", "保存済み", "已儲存", "已保存"],
            ["My Oshi", "推し", "推"],
            ["Settings", "設定", "设置"],
        ]
        let tabLabels = Array(Set(labels + (localizedLabels.indices.contains(index) ? localizedLabels[index] : [])))
        for label in tabLabels {
            let button = app.tabBars.buttons[label]
            if button.waitForExistence(timeout: 1) {
                button.tap()
                return
            }
            let sidebarButton = app.buttons[label]
            if sidebarButton.waitForExistence(timeout: 1), sidebarButton.isHittable {
                sidebarButton.tap()
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

    /// Finds and taps a navigation bar button by identifier, falling back to iOS's
    /// automatic "More" overflow when the toolbar is too narrow to show it directly.
    private func tapToolbarButton(identifier: String, timeout: TimeInterval = 5) {
        let button = app.buttons[identifier]
        if button.waitForExistence(timeout: timeout) {
            button.tap()
            return
        }
        let overflowButton = app.buttons["OverflowBarButtonItem"]
        if overflowButton.waitForExistence(timeout: 1) {
            overflowButton.tap()
        }
        XCTAssertTrue(button.waitForExistence(timeout: timeout), "Could not find toolbar button '\(identifier)', including via the overflow menu")
        button.tap()
    }

    /// True once a toolbar button has returned to its idle state — either visible
    /// directly, or (on a narrow toolbar) collapsed back into the "More" overflow.
    private func toolbarButtonIsIdle(identifier: String, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        let button = app.buttons[identifier]
        let overflowButton = app.buttons["OverflowBarButtonItem"]
        while Date() < deadline {
            if button.exists || overflowButton.exists { return true }
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        return button.exists || overflowButton.exists
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

    private func waitForElement(label: String, timeout: TimeInterval, swipes: Int) -> XCUIElement {
        let element = app.staticTexts[label]
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

    private func assertFeedTitlesAppearInOrder(_ expectedTitles: [String], file: StaticString = #filePath, line: UInt = #line) {
        var nextExpectedIndex = 0
        let expectedSet = Set(expectedTitles)

        for attempt in 0..<20 {
            let visibleTitles = app.staticTexts.allElementsBoundByIndex
                .filter { expectedSet.contains($0.label) && $0.exists && $0.frame.minY > 0 }
                .sorted { lhs, rhs in
                    if lhs.frame.minY != rhs.frame.minY { return lhs.frame.minY < rhs.frame.minY }
                    return lhs.frame.minX < rhs.frame.minX
                }

            for titleElement in visibleTitles {
                guard let actualIndex = expectedTitles.firstIndex(of: titleElement.label) else { continue }
                if actualIndex < nextExpectedIndex { continue }

                XCTAssertEqual(
                    actualIndex,
                    nextExpectedIndex,
                    "Expected \(expectedTitles[nextExpectedIndex]) before \(titleElement.label)",
                    file: file,
                    line: line
                )
                nextExpectedIndex += 1

                if nextExpectedIndex == expectedTitles.count {
                    return
                }
            }

            if attempt < 19 {
                app.swipeUp()
            }
        }

        XCTFail(
            "Only verified \(nextExpectedIndex) of \(expectedTitles.count) feed titles in newest-to-oldest order",
            file: file,
            line: line
        )
    }

    private func tapReaderBackButton(platformId: String, file: StaticString = #filePath, line: UInt = #line) {
        let navigationButtons = app.navigationBars.buttons
        let firstButton = navigationButtons.element(boundBy: 0)
        if firstButton.waitForExistence(timeout: 3), firstButton.isHittable {
            firstButton.tap()
            return
        }

        let fallback = waitForAnyButton(containing: ["Back", "OshiReader", "戻る", "返回"], timeout: 2)
        if let fallback, fallback.isHittable {
            fallback.tap()
            return
        }

        XCTFail("Could not return from Reader for \(platformId)", file: file, line: line)
    }

    private func assertReaderLoadedWithoutFallbackBanner(file: StaticString = #filePath, line: UInt = #line) {
        let loadingState = app.otherElements["reader.loadingState"]
        if loadingState.exists {
            let loadFinished = NSPredicate(format: "exists == false")
            expectation(for: loadFinished, evaluatedWith: loadingState)
            waitForExpectations(timeout: 15)
        }
        XCTAssertFalse(app.otherElements["reader.failedState"].exists, file: file, line: line)
        XCTAssertFalse(app.buttons["reader.openInBrowserButton"].exists, file: file, line: line)
        XCTAssertFalse(app.buttons["reader.failedOpenInBrowserButton"].exists, file: file, line: line)
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
    func testRealSourcesFeedFetching() throws {
        guard liveUITestsEnabled else {
            throw XCTSkip("Set OSHI_READER_RUN_LIVE_UI_TESTS=1 to run live source UI checks")
        }
        // Relaunch the app without the --uitesting argument so it hits real endpoints
        app.terminate()
        app = XCUIApplication()
        app.launch()

        let randomKeywords = ["木村拓哉", "戸田恵梨香"]

        tapTab(index: 4, labels: ["Settings"])

        for keyword in randomKeywords {
            if app.staticTexts[keyword].exists {
                continue
            }

            let addButton = app.buttons["settings.addKeywordButton"]
            XCTAssertTrue(addButton.waitForExistence(timeout: 10))
            addButton.tap()

            let keywordField = app.textFields.firstMatch
            XCTAssertTrue(keywordField.waitForExistence(timeout: 5))
            keywordField.tap()
            keywordField.typeText(keyword)

            let confirmButton = waitForAnyButton(containing: ["Add", "追加", "添加", "新增"], timeout: 5)
                ?? waitForButton(identifier: "settings.confirmAddKeywordButton", timeout: 3)
            XCTAssertNotNil(confirmButton, "Confirm add-keyword button not found")
            confirmButton?.tap()

            // Wait for sheet to disappear
            _ = addButton.waitForExistence(timeout: 5)
            XCTAssertTrue(
                app.staticTexts[keyword].waitForExistence(timeout: 5),
                "Keyword was not added: \(keyword)"
            )
        }

        // Go to feed
        if app.state != .runningForeground {
            app.activate()
            if app.state != .runningForeground {
                app.launch()
            }
        }
        tapTab(index: 0, labels: ["Feed"])

        let refreshButton = app.buttons["feed.refreshButton"]
        XCTAssertTrue(refreshButton.waitForExistence(timeout: 10))
        refreshButton.tap()

        // Wait for feed items to populate (real network call)
        let anyFeedItem = app.buttons["feed.card"]
        let loaded = anyFeedItem.waitForExistence(timeout: 30)

        XCTAssertTrue(loaded, "Failed to load feed items from real sources")

        // Scroll down to view the feed
        app.swipeUp()
        app.swipeUp()
        app.swipeUp()
    }
}
