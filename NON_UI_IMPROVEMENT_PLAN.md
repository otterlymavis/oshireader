# Non-UI Improvement Plan

This plan focuses on reliability, security, testing, networking, and maintainability. It intentionally excludes visual/UI redesign work.

## Goals

- Make local data persistence safer and easier to evolve.
- Make networking failures observable instead of silent.
- Reduce risk around stored credentials and arbitrary URL handling.
- Improve test isolation and coverage for core app behavior.
- Simplify future maintenance by separating large services into clearer modules.

## Phase 1: Safety Baseline

1. Audit current local data files and persistence paths.
   - List every JSON file `LocalDB` reads/writes.
   - Document each file schema and owner.
   - Identify which writes can happen close together.

2. Add structured internal logging.
   - Replace important `print` calls and silent `try?` failure points with a small logging wrapper.
   - Include categories such as `network`, `persistence`, `notifications`, and `scraping`.
   - Keep logs privacy-conscious and avoid storing tokens, full user content, or private URLs.

3. Add explicit error surfaces for refresh flows.
   - Distinguish backend unavailable, network timeout, decode failure, and empty result.
   - Keep user-facing behavior calm, but preserve enough internal information for debugging.

## Phase 2: Persistence Reliability

1. Introduce a persistence boundary.
   - Move JSON read/write logic behind a dedicated store object or actor.
   - Ensure writes for the same file are serialized.
   - Consider debounced saves for frequently changing collections.

2. Add local schema versioning.
   - Add a version marker for persisted data.
   - Define simple migrations for future field renames or storage layout changes.
   - Add tests for loading old/missing/malformed files.

3. Normalize date handling.
   - Parse feed item dates into `Date` for filtering/sorting logic.
   - Avoid relying on string comparison except at serialization boundaries.
   - Add tests for timezone, fractional seconds, and malformed date inputs.

## Phase 3: Networking Robustness

1. Split `NetworkManager` by responsibility.
   - Backend API client.
   - Local fallback/RSS scraper.
   - Credential API.
   - Translation/image lookup helpers.
   - APNs registration.

2. Add a shared request layer.
   - Centralize timeout configuration.
   - Centralize status-code handling and JSON decoding.
   - Return typed errors where practical.

3. Rework feed refresh into smaller operations.
   - Separate quick refresh from deep refresh.
   - Make backend sync, platform fetches, fallback scraping, poll trigger, and custom URL scraping independently observable.
   - Avoid doing all expensive refresh work every time when not needed.

## Phase 4: Security And Privacy

1. Move sensitive values out of `UserDefaults`.
   - Store admin API token and external API credentials in Keychain.
   - Keep non-sensitive preferences in `UserDefaults`.
   - Add migration from existing `UserDefaults` values to Keychain.

2. Review admin-token behavior.
   - Decide whether admin token support should be available in production builds.
   - If it remains, gate it clearly and document expected usage.

3. Harden custom URL scraping.
   - Restrict allowed URL schemes to `https` and possibly `http`.
   - Reject local network, file, data, and unsupported schemes.
   - Add clear errors for invalid or blocked URLs.

## Phase 5: Test Coverage

1. Make `LocalDB` test-injectable.
   - Avoid relying on `LocalDB.shared` in unit tests.
   - Use temporary directories for persistence tests.
   - Reset state deterministically between tests.

2. Add focused unit tests.
   - Duplicate feed item merging.
   - Hidden item keys.
   - Strict keyword and alias matching.
   - Date filtering and sorting.
   - Platform normalization.
   - Malformed JSON and backend decoding failures.

3. Add network-client tests.
   - Mock successful responses.
   - Mock status-code failures.
   - Mock malformed payloads.
   - Mock timeout/cancelation behavior.

4. Strengthen notification tests.
   - Verify de-duping when the same item appears through multiple paths.
   - Verify disabled terms never schedule notifications.
   - Verify capped/evicted feed items do not notify.

## Phase 6: Concurrency Cleanup

1. Clarify actor ownership.
   - Decide whether `LocalDB` is `@MainActor` or backed by a separate persistence actor.
   - Remove mixed manual main dispatch where actor isolation can do the job.

2. Manage view-started tasks.
   - Replace fire-and-forget view tasks with owned tasks when cancellation matters.
   - Cancel obsolete searches/refreshes when views disappear or inputs change.

3. Prevent overlapping refreshes.
   - Keep the existing guard, then expand it into a small refresh coordinator if needed.
   - Ensure platform fetches and fallback scraping cannot fight each other in ways that overwrite fresher state.

## Phase 7: Maintainability

1. Centralize platform definitions.
   - One source for platform id, display name, icon, aliases, backend key, strict matching behavior, and subscription defaults.
   - Replace scattered string comparisons with this model.

2. Replace raw string modes with enums.
   - `collection_mode`: `all_info`, `media_only`.
   - `mediaFilter`: `all`, `media_only`.
   - Platform IDs where feasible.

3. Document core workflows.
   - Feed refresh flow.
   - Watch term sync flow.
   - Notification flow.
   - Local persistence layout.

## Suggested Order

1. Logging and error visibility.
2. Persistence boundary and test-injectable storage.
3. Date normalization and persistence tests.
4. Keychain migration for sensitive values.
5. Network request layer and typed errors.
6. Feed refresh decomposition.
7. Platform-definition cleanup and enum migration.

## Success Criteria

- Unit tests can run without relying on shared app state.
- Local data writes are serialized and migration-ready.
- Sensitive tokens are no longer stored in `UserDefaults`.
- Feed refresh failures are diagnosable.
- Platform behavior is defined in one place.
- Core filtering, merging, notification, and decoding behavior has focused test coverage.
