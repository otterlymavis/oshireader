# Background Refresh Runbook

This repo relies on three moving pieces for automatic feed notifications:

- Cloudflare Worker cron checks diagnostics hourly, using two alternating
  two-hour cron entries, and calls the backend poll endpoint only when active
  watch terms or pending notifications need work.
- Render runs the backend poll and records `backend_events`.
- The backend in-process scheduler is disabled by default; Cloudflare should be
  the single production scheduler.
- APNs delivery happens when a completed poll creates eligible new matches.

## Health Checks

External uptime monitor:

- UptimeRobot should monitor `https://oshireader-feed-poller.oshireader-otterlymavis.workers.dev/liveness`.
- `/liveness` is a fast Worker availability check and does not call Render.

Primary scheduled monitor:

- GitHub Actions `Poller monitor`, every 6 hours.

Manual worker health check:

```sh
curl -fsS https://oshireader-feed-poller.oshireader-otterlymavis.workers.dev/health
```

Healthy output must include:

- `status: "ok"`
- `healthy: true`
- `notifications.healthy: true`
- either `idle: true` when there is no active polling work, or a recent
  `diagnostics.latest_successful_poll`

The worker health endpoint reads authenticated backend diagnostics on every
request. Avoid high-frequency external uptime checks; use the scheduled GitHub
monitor cadence unless you are doing manual diagnostics.

The backend commit currently served by Render:

```sh
curl -fsS https://oshireader.onrender.com/api/health
```

Authenticated backend notification health is exposed through:

```sh
curl -fsS https://oshireader.onrender.com/api/admin/stats \
  -H "Authorization: Bearer $ADMIN_API_TOKEN"
```

Healthy notification output must include:

- `apns.configured: true`
- `notification_health.healthy: true`
- `notification_health.active_silent_orphan_terms: 0`
- `notification_health.active_notify_terms_without_verified_devices: 0`
- `pending_notifications: []`

### Automation credential rotation

`ADMIN_API_TOKEN` is intentionally stored independently in three places: the
Render backend, the Cloudflare Worker secret, and the GitHub Actions secret.
When rotating it, generate one value and update all three before testing. A
successful GitHub workflow does not update Render, and updating Render does not
update the Worker.

Verify the backend first:

```sh
curl -i https://oshireader.onrender.com/api/admin/poller-health \
  -H "Authorization: Bearer $ADMIN_API_TOKEN"
```

Then verify the Worker:

```sh
curl -fsS https://oshireader-feed-poller.oshireader-otterlymavis.workers.dev/health
```

The Worker health check should report `status: ok`, `healthy: true`, no pending
notifications, and no orphaned notification terms. Run the poll workflow before
the monitor when manually recovering stale poll data; starting both at once can
make the monitor observe the pre-poll state.

The `Admin auth canary` workflow runs daily and can also be dispatched manually:

```sh
gh workflow run admin-auth-canary.yml --repo otterlymavis/oshireader
```

It verifies the GitHub Actions copy of `ADMIN_API_TOKEN` against Render without
starting a poll or changing production data.

Manual end-to-end APNs canary:

```sh
gh workflow run notification-canary.yml --ref master
```

This intentionally sends one visible synthetic "OshiReader notification canary"
push through the same new-match APNs path used for feed notifications. Use it
when you need proof that the backend can still produce and deliver a real push.

## Failure Pattern

The common polling failure is repeated `poll started` events with no newer
`poll completed` event. This usually means the Render process restarted or ran
out of budget before the full connector/term workload finished.

Look for:

- `latest_poll.status == "started"` or `"running_past_request_timeout"`
- `latest_poll.created_at` older than 30 minutes while still active
- `latest_successful_poll.created_at` older than 240 minutes
- repeated `latest_poll.id` changes without a matching completed event

The common notification failure is healthy polling with degraded notification
health. This usually means APNs is not configured, pending notifications are
stuck, or at least one active notification term has no verified device attached.

Look for:

- `notification_health`
- `pending_notifications`
- `latest_relevant_apns`
- `apns.device_tokens_by_environment_and_verification`

## Safe Production Knobs

These are intentionally conservative for the current Render instance:

- `POLL_TERMS_PER_RUN=2`
- `CONNECTOR_CONCURRENCY=1`
- `CONNECTOR_FETCH_TIMEOUT_SECONDS=8`
- `NOTIFICATION_FRESHNESS_WINDOW_MINUTES=1440`
- `ORPHANED_NOTIFICATION_GRACE_MINUTES=60`
- Worker `MIN_POLL_INTERVAL_MINUTES=120`
- Worker `STALE_AFTER_MINUTES=360`
- Worker `ACTIVE_POLL_TIMEOUT_MINUTES=30`
- iOS/backend device-triggered polls remain throttled to a 170-minute cadence,
  so app background refresh cannot bypass the Worker schedule.

Increase them only after the service has stayed healthy for a few days, and change
one knob at a time. If polling starts to miss completion markers again, roll the
knob back before debugging connectors.

## Alerting

The GitHub Actions workflow `Poller monitor` runs every 6 hours and fails if
the worker health endpoint is degraded, if an active poll is still marked
`started` or `running_past_request_timeout` after 30 minutes, if the latest
successful poll is older than 360 minutes without an active poll inside the
stale window, or if authenticated backend notification health is degraded.
The `Backend keep-alive` workflow is
manual-only so it does not wake Render and trigger startup database work on a
fixed schedule.

Set the GitHub Actions secret `ALERT_WEBHOOK_URL` to a Slack/Discord-compatible
incoming webhook to receive failure alerts from `Poll feed`, `Poller monitor`,
and `Notification canary`. The workflows still fail normally when this secret is
not configured; the alert step is skipped.

The Cloudflare Worker also sends `ALERT_WEBHOOK_URL` a compact watchdog summary
when a scheduled poll finishes with degraded poll or notification health.

The GitHub Actions workflow `Notification canary` is manual-only to avoid noisy
scheduled test pushes. It fails if the backend cannot choose an active
notification term, if APNs delivery fails for any registered device, if APNs
reports retryable failures, or if APNs prunes invalid tokens. Inspect the
canary event's redacted `device_results` rows to confirm the affected token
suffix and environment.

## Recovery Steps

1. Check worker health and backend health.
2. If worker health is degraded, inspect `latest_poll`, `latest_successful_poll`,
   `latest_apns`, and `notifications`.
3. If polls are starting but not completing, reduce workload knobs in `render.yaml`.
4. If `notification_health` is degraded, inspect the listed term ids and use
   Settings notification repair/test on the affected device to re-register APNs,
   then check health again. The `APNs registration monitor` also runs guarded
   orphaned-term maintenance for notification-enabled owner-scoped terms older
   than `ORPHANED_NOTIFICATION_GRACE_MINUTES`: it mutes the orphaned term,
   clears pending notifications, and rechecks diagnostics for up to three
   remediation passes before failing.
5. If health looks clean but notifications are still suspect, run
   `gh workflow run notification-canary.yml --ref master` and inspect the APNs
   canary event.
6. Trigger `gh workflow run deploy-render.yml --ref master` for backend config changes.
7. Check worker health after the next scheduled run, or manually trigger a poll
   when you need immediate proof of a fresh `latest_successful_poll`.
