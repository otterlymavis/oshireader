const POLL_TIMEOUT_MS = 4 * 60 * 1000;
const DEFAULT_POLL_RETRIES = 2;
const DEFAULT_RETRY_DELAY_MS = 5_000;
const DEFAULT_STALE_AFTER_MINUTES = 35;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function requireAdminToken(env) {
  if (!env.ADMIN_API_TOKEN) {
    throw new Error("Missing Worker secret: ADMIN_API_TOKEN");
  }
  return env.ADMIN_API_TOKEN;
}

async function fetchBackendDiagnostics(backendURL, adminToken) {
  const response = await fetch(`${backendURL}/api/admin/stats`, {
    headers: {
      authorization: `Bearer ${adminToken}`,
      "user-agent": "oshireader-cloudflare-poller/1.0",
    },
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) {
    throw new Error(`Backend diagnostics failed: HTTP ${response.status}`);
  }

  const stats = await response.json();
  const recentEvents = stats.recent_events || [];
  const successfulPoll = stats.latest_successful_poll || recentEvents.find(
    (event) => event.kind === "poll" &&
      (event.status === "completed" || event.status === "completed_with_errors"),
  ) || null;
  return {
    items_total: stats.items_total,
    matches_total: stats.matches_total,
    apns: stats.apns || null,
    watch_terms: stats.watch_terms || [],
    latest_poll: stats.latest_poll || recentEvents.find((event) => event.kind === "poll") || null,
    latest_successful_poll: successfulPoll,
    latest_apns: recentEvents.find((event) => event.kind === "apns") || null,
  };
}

function notificationHealth(diagnostics) {
  const watchTerms = diagnostics.watch_terms || [];
  const notifyTerms = watchTerms.filter((term) => term.is_active && term.notify_on_new);
  const atRiskTerms = notifyTerms.filter((term) => (term.notification_verified_devices || 0) <= 0);
  const apns = diagnostics.apns || {};
  const envCounts = apns.device_tokens_by_environment_and_verification || {};
  const verifiedDevices = Object.values(envCounts).reduce(
    (total, entry) => total + (entry?.verified || 0),
    0,
  );

  if (!notifyTerms.length) {
    return { healthy: true, reason: "No active notification terms" };
  }
  if (!apns.configured) {
    return {
      healthy: false,
      reason: "Backend APNs is not configured",
      active_notify_terms: notifyTerms.length,
      at_risk_terms: notifyTerms.length,
    };
  }
  if (verifiedDevices <= 0) {
    return {
      healthy: false,
      reason: "No verified APNs devices are registered",
      active_notify_terms: notifyTerms.length,
      at_risk_terms: notifyTerms.length,
      verified_devices: 0,
    };
  }
  if (atRiskTerms.length) {
    return {
      healthy: false,
      reason: "Some active notification terms have no verified device",
      active_notify_terms: notifyTerms.length,
      at_risk_terms: atRiskTerms.length,
      at_risk_keywords: atRiskTerms.slice(0, 5).map((term) => term.keyword),
      verified_devices: verifiedDevices,
    };
  }
  return {
    healthy: true,
    active_notify_terms: notifyTerms.length,
    at_risk_terms: 0,
    verified_devices: verifiedDevices,
  };
}

function pollHealth(diagnostics, staleAfterMinutes = DEFAULT_STALE_AFTER_MINUTES) {
  const event = diagnostics.latest_successful_poll;
  const latestPoll = diagnostics.latest_poll;
  const latestPollStartedAt = latestPoll?.created_at ? Date.parse(latestPoll.created_at) : Number.NaN;
  const latestPollAgeMinutes = Number.isFinite(latestPollStartedAt)
    ? (Date.now() - latestPollStartedAt) / 60_000
    : Number.NaN;
  const latestPollIsActive = latestPoll &&
    ["started", "running_past_request_timeout"].includes(latestPoll.status);
  const completedAt = event?.created_at ? Date.parse(event.created_at) : Number.NaN;
  if (!Number.isFinite(completedAt)) {
    if (latestPollIsActive && latestPollAgeMinutes <= staleAfterMinutes) {
      return {
        healthy: true,
        in_progress: true,
        age_minutes: Math.max(0, Math.floor(latestPollAgeMinutes)),
      };
    }
    return { healthy: false, reason: "No successful backend poll recorded" };
  }
  if (
    latestPollIsActive &&
    Number.isFinite(latestPollStartedAt) &&
    latestPollStartedAt > completedAt &&
    latestPollAgeMinutes <= staleAfterMinutes
  ) {
    return {
      healthy: true,
      in_progress: true,
      age_minutes: Math.max(0, Math.floor(latestPollAgeMinutes)),
    };
  }
  const ageMinutes = (Date.now() - completedAt) / 60_000;
  if (ageMinutes > staleAfterMinutes) {
    return {
      healthy: false,
      reason: `Latest successful poll is ${Math.floor(ageMinutes)} minutes old`,
    };
  }
  return { healthy: true, age_minutes: Math.max(0, Math.floor(ageMinutes)) };
}

async function notifyWatchdog(env, error) {
  if (!env.ALERT_WEBHOOK_URL) return;
  const message = typeof error === "string"
    ? error
    : `OshiReader automation failure: ${String(error)}`;
  try {
    await fetch(env.ALERT_WEBHOOK_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: message, content: message }),
      signal: AbortSignal.timeout(15_000),
    });
  } catch (webhookError) {
    console.error("Watchdog alert delivery failed", webhookError);
  }
}

function watchdogSummary(reason, diagnostics = {}) {
  const latestPoll = diagnostics.latest_poll;
  const latestSuccessfulPoll = diagnostics.latest_successful_poll;
  const latestApns = diagnostics.latest_apns;
  return [
    `OshiReader poll watchdog degraded: ${reason}`,
    `latest_poll=${latestPoll?.status || "none"} at ${latestPoll?.created_at || "n/a"}`,
    `latest_successful_poll=${latestSuccessfulPoll?.status || "none"} at ${latestSuccessfulPoll?.created_at || "n/a"}`,
    `latest_apns=${latestApns?.status || "none"} at ${latestApns?.created_at || "n/a"}`,
    `items_total=${diagnostics.items_total ?? "n/a"} matches_total=${diagnostics.matches_total ?? "n/a"}`,
  ].join("\n");
}

function notificationWatchdogSummary(health, diagnostics = {}) {
  const latestApns = diagnostics.latest_apns;
  return [
    `OshiReader notification watchdog degraded: ${health.reason || "unknown"}`,
    `active_notify_terms=${health.active_notify_terms ?? "n/a"} at_risk_terms=${health.at_risk_terms ?? "n/a"}`,
    `at_risk_keywords=${(health.at_risk_keywords || []).join(", ") || "n/a"}`,
    `verified_devices=${health.verified_devices ?? "n/a"}`,
    `latest_apns=${latestApns?.status || "none"} at ${latestApns?.created_at || "n/a"}`,
  ].join("\n");
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export async function triggerBackendPoll(env) {
  const adminToken = requireAdminToken(env);
  const backendURL = (env.BACKEND_URL || "https://oshireader.onrender.com").replace(/\/+$/, "");
  const startedAt = Date.now();

  const retries = Number(env.POLL_RETRIES ?? DEFAULT_POLL_RETRIES);
  const retryDelayMs = Number(env.POLL_RETRY_DELAY_MS ?? DEFAULT_RETRY_DELAY_MS);
  let response;
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      response = await fetch(`${backendURL}/api/admin/poll`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${adminToken}`,
          "content-type": "application/json",
          "user-agent": "oshireader-cloudflare-poller/1.0",
        },
        signal: AbortSignal.timeout(POLL_TIMEOUT_MS),
      });
      if (response.ok) break;
      lastError = new Error(`Backend poll failed: HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    if (attempt < retries) await delay(retryDelayMs * (attempt + 1));
  }
  if (!response?.ok) throw lastError || new Error("Backend poll failed without a response");
  const responseBody = await response.text();
  const durationMs = Date.now() - startedAt;

  let result;
  try {
    result = JSON.parse(responseBody);
  } catch {
    result = { status: responseBody || "poll completed" };
  }

  const diagnostics = await fetchBackendDiagnostics(backendURL, adminToken);
  return {
    ok: true,
    backend_status: result.status,
    duration_ms: durationMs,
    diagnostics,
  };
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(
      triggerBackendPoll(env).then(
        async (result) => {
          console.log("Scheduled feed poll completed", result);
          const health = pollHealth(
            result.diagnostics,
            Number(env.STALE_AFTER_MINUTES ?? DEFAULT_STALE_AFTER_MINUTES),
          );
          const notifications = notificationHealth(result.diagnostics);
          if (!health.healthy) {
            await notifyWatchdog(
              env,
              watchdogSummary(health.reason || "unknown", result.diagnostics),
            );
          }
          if (!notifications.healthy) {
            await notifyWatchdog(
              env,
              notificationWatchdogSummary(notifications, result.diagnostics),
            );
          }
        },
        (error) => {
          console.error("Scheduled feed poll failed", error);
          return notifyWatchdog(env, error).then(() => {
            throw error;
          });
        },
      ),
    );
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      try {
        const backendURL = (env.BACKEND_URL || "https://oshireader.onrender.com").replace(/\/+$/, "");
        const diagnostics = await fetchBackendDiagnostics(backendURL, requireAdminToken(env));
        const health = pollHealth(
          diagnostics,
          Number(env.STALE_AFTER_MINUTES ?? DEFAULT_STALE_AFTER_MINUTES),
        );
        const notifications = notificationHealth(diagnostics);
        const healthy = health.healthy && notifications.healthy;
        return json(
          {
            status: healthy ? "ok" : "degraded",
            scheduler: "cloudflare-cron",
            ...health,
            notifications,
            diagnostics,
          },
          healthy ? 200 : 503,
        );
      } catch (error) {
        await notifyWatchdog(env, error);
        return json({ status: "degraded", scheduler: "cloudflare-cron", reason: String(error) }, 503);
      }
    }

    if (request.method === "POST" && url.pathname === "/run") {
      const expected = `Bearer ${requireAdminToken(env)}`;
      if (request.headers.get("authorization") !== expected) {
        return json({ detail: "Unauthorized" }, 401);
      }
      try {
        return json(await triggerBackendPoll(env));
      } catch (error) {
        console.error("Manual feed poll failed", error);
        return json({ detail: String(error) }, 502);
      }
    }

    return json({ detail: "Not found" }, 404);
  },
};
