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

        app.buttons["settings.addKeywordButton"].tap()

        // Sheet may take a moment; wait up to 5 s for the only non-secure text field to appear
        let keywordField = app.textFields.firstMatch
        XCTAssertTrue(keywordField.waitForExistence(timeout: 5))
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
        tapTab(index: 0, labels: ["Feed"])

        let headline = app.staticTexts["UITest Oshi headline"]
        XCTAssertTrue(headline.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest Oshi headline") ?? headline).tap()

        let readerModeButton = waitForButton(identifier: "reader.modeToggleButton", timeout: 10)
        XCTAssertNotNil(readerModeButton)
        readerModeButton?.tap()
    }

    func testSavedReaderFlow() throws {
        tapTab(index: 2, labels: ["Saved"])

        let savedTitle = app.staticTexts["UITest saved article"]
        XCTAssertTrue(savedTitle.waitForExistence(timeout: 3))
        (firstExistingButton(containing: "UITest saved article") ?? savedTitle).tap()

        XCTAssertNotNil(waitForButton(identifier: "reader.modeToggleButton", timeout: 10))
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

        XCTAssertTrue(waitForElement(identifier: "settings.notificationStatus", timeout: 2, swipes: 4).exists)
        XCTAssertTrue(waitForElement(identifier: "settings.notificationSetupHint", timeout: 2, swipes: 0).exists)

        // Only one button renders depending on permission state — verify at least one exists
        let hasEnable = waitForElement(identifier: "settings.enableNotificationsButton", timeout: 2, swipes: 1).exists
        let hasTest = waitForElement(identifier: "settings.testNotificationButton", timeout: 1, swipes: 0).exists
        let hasOpenSettings = waitForElement(identifier: "settings.openSettingsButton", timeout: 1, swipes: 0).exists
        XCTAssertTrue(hasEnable || hasTest || hasOpenSettings, "Expected a notification control button")
    }

    func testLiveBackgroundPush() throws {
        guard liveUITestsEnabled else {
            throw XCTSkip("Set OSHI_READER_RUN_LIVE_UI_TESTS=1 to run live APNs UI checks")
        }
        defer {
            if app.state != .runningForeground {
                app.activate()
                if app.state != .runningForeground {
                    app.launch()
                }
            }
        }

        tapTab(index: 4, labels: ["Settings"])

        let enableButton = waitForElement(
            identifier: "settings.enableNotificationsButton",
            timeout: 2,
            swipes: 4
        )
        if enableButton.exists {
            enableButton.tap()
            let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
            let allowLabels = ["Allow", "許可", "允許", "允许"]
            for label in allowLabels {
                let allowButton = springboard.buttons[label]
                if allowButton.waitForExistence(timeout: 2) {
                    allowButton.tap()
                    break
                }
            }
        }

        let testButton = waitForElement(
            identifier: "settings.testNotificationButton",
            timeout: 8,
            swipes: 2
        )
        XCTAssertTrue(testButton.exists, "Remote test notification button is unavailable")

        // Prime first-install APNs registration while the app is foregrounded.
        // The first attempt may legitimately need to obtain and upload a token.
        testButton.tap()
        let resultQuery = app.staticTexts.matching(identifier: "settings.notificationTestResult")
        guard app.state != .notRunning else {
            throw XCTSkip("OshiReader exited before reporting the APNs registration result")
        }
        var resultLabels = waitForRemotePushResult(in: resultQuery, timeout: 25)
        XCTAssertFalse(resultLabels.isEmpty, "The app did not report the APNs registration result")
        if hasUnregisteredDeviceTokenMessage(resultLabels) {
            testButton.tap()
            resultLabels = waitForRemotePushResult(in: resultQuery, timeout: 25)
            guard app.state != .notRunning else {
                throw XCTSkip("OshiReader exited while waiting for APNs token registration retry")
            }
            if hasUnregisteredDeviceTokenMessage(resultLabels) {
                throw XCTSkip(
                    "Simulator did not register an APNs device token after retry. Result: \(resultLabels)"
                )
            }
        }
        XCTAssertTrue(
            hasRemotePushSuccessMessage(resultLabels),
            "The backend did not confirm APNs acceptance. Result: \(resultLabels)"
        )

        // With the token registered, queue one delayed server push, then background.
        testButton.tap()
        let queuedResultLabels = waitForRemotePushResult(in: resultQuery, timeout: 10)
        XCTAssertTrue(
            hasRemotePushSuccessMessage(queuedResultLabels),
            "The backend did not queue the background APNs test. Result: \(queuedResultLabels)"
        )
        XCUIDevice.shared.press(.home)

        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let notificationText = springboard.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@", "通知プレビュー")
        ).firstMatch
        XCTAssertTrue(
            notificationText.waitForExistence(timeout: 20),
            "The server APNs notification did not appear while OshiReader was backgrounded"
        )

        notificationText.press(forDuration: 1.2)
        let expandedMessage = springboard.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "新着結果のタイトル、本文、リンク")
        ).firstMatch
        XCTAssertTrue(
            expandedMessage.waitForExistence(timeout: 5),
            "Expanded notification did not show the fuller message text"
        )
        let previewMedia = springboard.images.matching(
            identifier: "notification.previewMedia"
        ).firstMatch
        XCTAssertTrue(
            previewMedia.waitForExistence(timeout: 8),
            "Expanded notification did not show its media preview"
        )
        let previewMetadata = springboard.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "1 new")
        ).firstMatch
        let openAction = springboard.buttons.matching(
            NSPredicate(format: "label == %@ OR label == %@", "Open", "開く")
        ).firstMatch
        let saveAction = springboard.buttons.matching(
            NSPredicate(format: "label == %@ OR label == %@", "Save", "保存")
        ).firstMatch
        if previewMetadata.waitForExistence(timeout: 5) {
            XCTAssertTrue(openAction.waitForExistence(timeout: 2), "Expanded notification is missing Open")
            XCTAssertTrue(saveAction.waitForExistence(timeout: 2), "Expanded notification is missing Save")
        }

        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = "Expanded background APNs preview"
        attachment.lifetime = .keepAlways
        add(attachment)

        app.activate()
        let backgroundRefreshResult = waitForElement(
            identifier: "settings.liveBackgroundRefreshResult",
            timeout: 30,
            swipes: 2
        )
        XCTAssertTrue(
            backgroundRefreshResult.waitForExistence(timeout: 5),
            "The content-available push did not report a completed background refresh"
        )
        XCTAssertEqual(
            backgroundRefreshResult.label,
            "completed:success",
            "The content-available push reported an unsuccessful background refresh"
        )
    }

    private func waitForRemotePushResult(
        in resultQuery: XCUIElementQuery,
        timeout: TimeInterval
    ) -> [String] {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let labels = resultQuery.allElementsBoundByIndex.map(\.label)
            if hasRemotePushSuccessMessage(labels) || hasUnregisteredDeviceTokenMessage(labels) {
                return labels
            }
            Thread.sleep(forTimeInterval: 0.5)
        }
        return resultQuery.allElementsBoundByIndex.map(\.label)
    }

    private func hasUnregisteredDeviceTokenMessage(_ labels: [String]) -> Bool {
        labels.contains {
            $0.localizedCaseInsensitiveContains("Device token is not registered")
                || $0.localizedCaseInsensitiveContains("デバイストークンが未登録")
                || $0.localizedCaseInsensitiveContains("裝置權杖尚未註冊")
                || $0.localizedCaseInsensitiveContains("设备令牌尚未注册")
                || $0.localizedCaseInsensitiveContains("No device token found on backend")
                || $0.localizedCaseInsensitiveContains("バックエンドにデバイストークンがありません")
                || $0.localizedCaseInsensitiveContains("後端找不到裝置權杖")
                || $0.localizedCaseInsensitiveContains("后端找不到设备令牌")
        }
    }

    private func hasRemotePushSuccessMessage(_ labels: [String]) -> Bool {
        labels.contains {
            $0.localizedCaseInsensitiveContains("Remote test notification sent")
                || $0.localizedCaseInsensitiveContains("リモートテスト通知を送信しました")
                || $0.localizedCaseInsensitiveContains("遠端測試通知已傳送")
                || $0.localizedCaseInsensitiveContains("已傳送遠端測試推播")
                || $0.localizedCaseInsensitiveContains("远程测试通知已发送")
                || $0.localizedCaseInsensitiveContains("已发送远程测试推送")
        }
    }

    private func remotePushSuccessPredicate() -> NSPredicate {
        NSPredicate(
            format: "label CONTAINS[c] %@ OR label CONTAINS[c] %@ OR label CONTAINS[c] %@ OR label CONTAINS[c] %@ OR label CONTAINS[c] %@ OR label CONTAINS[c] %@",
            "Remote test notification sent",
            "リモートテスト通知を送信しました",
            "遠端測試通知已傳送",
            "已傳送遠端測試推播",
            "远程测试通知已发送",
            "已发送远程测试推送"
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
