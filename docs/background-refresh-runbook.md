# Background Refresh Runbook

This repo relies on three moving pieces for automatic feed notifications:

- Cloudflare Worker cron calls the backend poll endpoint every 15 minutes.
- Render runs the backend poll and records `backend_events`.
- APNs delivery happens when a completed poll creates eligible new matches.

## Health Checks

Primary monitor:

```sh
curl -fsS https://oshireader-feed-poller.oshireader-otterlymavis.workers.dev/health
```

Healthy output must include:

- `status: "ok"`
- `healthy: true`
- `notifications.healthy: true`
- a recent `diagnostics.latest_successful_poll`

The backend commit currently served by Render:

```sh
curl -fsS https://oshireader.onrender.com/api/health
```

## Failure Pattern

The common polling failure is repeated `poll started` events with no newer
`poll completed` event. This usually means the Render process restarted or ran
out of budget before the full connector/term workload finished.

Look for:

- `latest_poll.status == "started"`
- `latest_successful_poll.created_at` older than 45 minutes
- repeated `latest_poll.id` changes without a matching completed event

The common notification failure is healthy polling with
`notifications.healthy: false`. This usually means APNs is not configured, there
are no verified APNs devices, or at least one active notification term has no
verified device attached.

Look for:

- `notifications.reason`
- `notifications.at_risk_keywords`
- `diagnostics.latest_apns`
- `diagnostics.apns.device_tokens_by_environment_and_verification`

## Safe Production Knobs

These are intentionally conservative for the current Render instance:

- `POLL_TERMS_PER_RUN=1`
- `CONNECTOR_CONCURRENCY=1`
- `CONNECTOR_FETCH_TIMEOUT_SECONDS=8`
- `ORPHANED_NOTIFICATION_GRACE_MINUTES=60`

Increase them only after the service has stayed healthy for a few days, and change
one knob at a time. If polling starts to miss completion markers again, roll the
knob back before debugging connectors.

## Alerting

The GitHub Actions workflow `Poller monitor` runs every 15 minutes and fails if
the worker health endpoint is degraded or if the latest successful poll is older
than 45 minutes.

The Cloudflare Worker also sends `ALERT_WEBHOOK_URL` a compact watchdog summary
when a scheduled poll finishes with degraded poll or notification health.

## Recovery Steps

1. Check worker health and backend health.
2. If worker health is degraded, inspect `latest_poll`, `latest_successful_poll`,
   `latest_apns`, and `notifications`.
3. If polls are starting but not completing, reduce workload knobs in `render.yaml`.
4. If `notifications` is degraded, open the app on the affected device and use
   Settings notification repair/test to re-register APNs, then check health again.
   The backend automatically disables push alerts for owner-scoped terms older
   than `ORPHANED_NOTIFICATION_GRACE_MINUTES` when their APNs device no longer
   exists, while preserving the feed term itself.
5. Trigger `gh workflow run deploy-render.yml --ref master` for backend config changes.
6. Keep polling worker health until a fresh `latest_successful_poll` appears.
