const POLL_TIMEOUT_MS = 4 * 60 * 1000;
const DEFAULT_POLL_RETRIES = 2;
const DEFAULT_RETRY_DELAY_MS = 5_000;
const DEFAULT_STALE_AFTER_MINUTES = 360;
const DEFAULT_ACTIVE_POLL_TIMEOUT_MINUTES = 30;
const DEFAULT_MIN_POLL_INTERVAL_MINUTES = 120;
const RSS_PROXY_TIMEOUT_MS = 20_000;
const FIVECH_PROXY_TIMEOUT_MS = 20_000;
const FIVECH_ITEST_UPSTREAM_ATTEMPTS = 3;
const FIVECH_ITEST_CACHE_SECONDS = 10 * 60;
const FIVECH_ALLOWED_BOARDS = new Set([
  "sweet.2ch.sc/headline",
  "ai.2ch.sc/newsalpha",
  "hayabusa3.2ch.sc/mnewsalpha",
  "ai.2ch.sc/newsplus",
  "hayabusa3.2ch.sc/mnewsplus",
  "anago.2ch.sc/news5plus",
  "nozomi.2ch.sc/snsplus",
  "hayabusa3.2ch.sc/news",
  "hayabusa3.2ch.sc/news4viptasu",
  "ikura.2ch.sc/musicnews",
  "anago.2ch.sc/geino",
  "awabi.2ch.sc/drama",
  "awabi.2ch.sc/cinema",
  "anago.2ch.sc/tvsaloon",
  "toro.2ch.sc/tv",
  "awabi.2ch.sc/tvd",
  "nozomi.2ch.sc/nhkdrama",
  "anago.2ch.sc/cm",
  "anago.2ch.sc/actor",
  "anago.2ch.sc/mendol",
  "toro.2ch.sc/sfx",
  "maguro.2ch.sc/fortune",
  "sweet.2ch.sc/patisserie",
  "anago.2ch.sc/mass",
  "ai.2ch.sc/kokusai",
  "nozomi.2ch.sc/4649",
  "anago.2ch.sc/am",
  "nozomi.2ch.sc/idol",
  "awabi.2ch.sc/akb",
  "toro.2ch.sc/nogizaka",
  "tarte.2ch.sc/keyakizaka46",
  "awabi.2ch.sc/uraidol",
  "anago.2ch.sc/indieidol",
  "anago.2ch.sc/netidol",
  "tarte.2ch.sc/akbsaloon",
  "tarte.2ch.sc/idolplus",
  "tarte.2ch.sc/world48",
  "awabi.2ch.sc/musicj",
  "awabi.2ch.sc/musicjm",
  "awabi.2ch.sc/musicjf",
  "toro.2ch.sc/musicjg",
  "awabi.2ch.sc/music",
  "anago.2ch.sc/streaming",
  "anago.2ch.sc/sns",
]);
const FIVECH_ITEST_ALLOWED_BOARDS = new Set([
  "nogizaka",
  "keyakizaka46",
  "akb",
  "akbsaloon",
  "world48",
  "idol",
  "uraidol",
  "indieidol",
  "netidol",
  "idolplus",
  "geino",
  "mnewsplus",
  "mnewsalpha",
  "news",
  "newsplus",
  "musicnews",
  "drama",
  "tvsaloon",
  "tv",
  "tvd",
  "am",
  "musicj",
  "musicjm",
  "musicjf",
  "musicjg",
  "music",
  "streaming",
  "sns",
]);

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

function unauthorized() {
  return json({ detail: "Unauthorized" }, 401);
}

function rssResponse(body, status = 200) {
  return new Response(body, {
    status,
    headers: {
      "content-type": "application/xml; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function textResponse(body, status = 200) {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/plain; charset=shift_jis",
      "cache-control": "no-store",
    },
  });
}

function utf8TextResponse(body, status = 200) {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fiveChItestCacheRequest(boardKey) {
  return new Request(`https://oshireader.internal-cache/fivech-itest-io/${encodeURIComponent(boardKey)}`);
}

function fiveChItestDatCacheRequest(server, boardKey, threadID) {
  return new Request(
    `https://oshireader.internal-cache/fivech-itest-dat/${encodeURIComponent(server)}/${encodeURIComponent(boardKey)}/${encodeURIComponent(threadID)}`,
  );
}

function hasDefaultCache() {
  return typeof caches !== "undefined" && caches.default;
}

function requireBearer(request, env) {
  const expected = normalizeBearerToken(requireAdminToken(env));
  const actual = normalizeBearerToken(request.headers.get("authorization") || "");
  return actual !== "" && actual === expected;
}

function normalizeBearerToken(value) {
  const raw = String(value || "").trim();
  if (raw.toLowerCase().startsWith("bearer ")) {
    return raw.slice(7).trim();
  }
  return raw;
}

async function proxySearchRss(request, env) {
  if (!requireBearer(request, env)) return unauthorized();

  const url = new URL(request.url);
  const target = url.searchParams.get("target") || "google";
  const query = (url.searchParams.get("query") || "").trim();
  if (!query || query.length > 200) {
    return json({ detail: "Invalid query" }, 400);
  }
  const paramOrDefault = (name, fallback) => {
    const value = (url.searchParams.get(name) || "").trim();
    return value && value.length <= 80 ? value : fallback;
  };
  const hl = paramOrDefault("hl", "ja");
  const gl = paramOrDefault("gl", "JP");
  const ceid = paramOrDefault("ceid", "JP:ja");
  const mkt = paramOrDefault("mkt", "ja-JP");
  const acceptLanguage = paramOrDefault("accept_language", "ja,en;q=0.9");

  let upstream;
  if (target === "google") {
    upstream = `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&hl=${encodeURIComponent(hl)}&gl=${encodeURIComponent(gl)}&ceid=${encodeURIComponent(ceid)}`;
  } else if (target === "bing") {
    upstream = `https://www.bing.com/news/search?q=${encodeURIComponent(query)}&format=rss&mkt=${encodeURIComponent(mkt)}`;
  } else {
    return json({ detail: "Unsupported target" }, 400);
  }

  const response = await fetch(upstream, {
    headers: {
      "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
      "accept-language": acceptLanguage,
    },
    signal: AbortSignal.timeout(RSS_PROXY_TIMEOUT_MS),
  });
  const body = await response.text();
  if (!response.ok) {
    return json({ detail: `Upstream ${target} failed`, status: response.status, bytes: body.length }, 502);
  }
  return rssResponse(body);
}

function normalizeFiveChBoard(boardURL) {
  let parsed;
  try {
    parsed = new URL(boardURL);
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(parsed.protocol)) return null;
  const boardKey = `${parsed.hostname}${parsed.pathname}`.replace(/\/+$/, "");
  if (!FIVECH_ALLOWED_BOARDS.has(boardKey)) return null;
  return `${parsed.protocol}//${boardKey}/`;
}

async function proxyFiveCh(request, env) {
  if (!requireBearer(request, env)) return unauthorized();

  const url = new URL(request.url);
  const resource = url.searchParams.get("resource") || "subject";

  let upstream;
  let itestBoardKey = "";
  let itestServer = "";
  let itestThreadID = "";
  if (resource === "itest_subback") {
    const boardKey = url.searchParams.get("board_key") || "";
    if (!FIVECH_ITEST_ALLOWED_BOARDS.has(boardKey)) {
      return json({ detail: "Unsupported board_key" }, 400);
    }
    itestBoardKey = boardKey;
    upstream = `https://r.jina.ai/http://https://itest.5ch.io/subback/${boardKey}`;
  } else if (resource === "itest_dat") {
    const boardKey = url.searchParams.get("board_key") || "";
    const server = url.searchParams.get("server") || "";
    const threadID = url.searchParams.get("thread_id") || "";
    if (!FIVECH_ITEST_ALLOWED_BOARDS.has(boardKey)) {
      return json({ detail: "Unsupported board_key" }, 400);
    }
    if (!/^[a-z0-9-]{2,32}$/.test(server)) {
      return json({ detail: "Invalid server" }, 400);
    }
    if (!/^\d{9,12}$/.test(threadID)) {
      return json({ detail: "Invalid thread_id" }, 400);
    }
    itestBoardKey = boardKey;
    itestServer = server;
    itestThreadID = threadID;
    upstream = `https://r.jina.ai/http://http://${server}.5ch.net/${boardKey}/dat/${threadID}.dat`;
  } else {
    const board = normalizeFiveChBoard(url.searchParams.get("board_url") || "");
    if (!board) {
      return json({ detail: "Unsupported board" }, 400);
    }
    if (resource === "subject") {
      upstream = `${board}subject.txt`;
    } else if (resource === "dat") {
      const threadID = url.searchParams.get("thread_id") || "";
      if (!/^\d{9,12}$/.test(threadID)) {
        return json({ detail: "Invalid thread_id" }, 400);
      }
      upstream = `${board}dat/${threadID}.dat`;
    } else {
      return json({ detail: "Unsupported resource" }, 400);
    }
  }

  let cacheRequest = null;
  if (resource === "itest_subback" && hasDefaultCache()) {
    cacheRequest = fiveChItestCacheRequest(itestBoardKey);
    const cached = await caches.default.match(cacheRequest);
    if (cached) {
      return cached;
    }
  } else if (resource === "itest_dat" && hasDefaultCache()) {
    cacheRequest = fiveChItestDatCacheRequest(itestServer, itestBoardKey, itestThreadID);
    const cached = await caches.default.match(cacheRequest);
    if (cached) {
      return cached;
    }
  }

  let lastStatus = 0;
  let lastBytes = 0;
  let body = null;
  for (let attempt = 1; attempt <= FIVECH_ITEST_UPSTREAM_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(upstream, {
        headers: {
          "user-agent": "Monazilla/1.00 OshiReader/1.0",
          "accept": "text/plain,text/html;q=0.8,*/*;q=0.5",
          "accept-language": "ja,en;q=0.9",
        },
        signal: AbortSignal.timeout(FIVECH_PROXY_TIMEOUT_MS),
      });
      body = await response.arrayBuffer();
      lastStatus = response.status;
      lastBytes = body.byteLength;
      if (response.ok && body.byteLength > 0) {
        break;
      }
      body = null;
    } catch (error) {
      lastStatus = 0;
      lastBytes = 0;
    }
    if (attempt < FIVECH_ITEST_UPSTREAM_ATTEMPTS) {
      await sleep(500 * attempt);
    }
  }

  if (!body) {
    return json({ detail: "Upstream 5ch failed", status: lastStatus, bytes: lastBytes }, 502);
  }

  if (resource === "itest_subback") {
    const response = utf8TextResponse(body);
    response.headers.set("cache-control", `public, max-age=${FIVECH_ITEST_CACHE_SECONDS}`);
    if (cacheRequest && hasDefaultCache()) {
      await caches.default.put(cacheRequest, response.clone());
    }
    return response;
  }
  if (resource === "itest_dat") {
    const response = utf8TextResponse(body);
    response.headers.set("cache-control", `public, max-age=${FIVECH_ITEST_CACHE_SECONDS}`);
    if (cacheRequest && hasDefaultCache()) {
      await caches.default.put(cacheRequest, response.clone());
    }
    return response;
  }
  return textResponse(body);
}

async function fetchBackendDiagnostics(backendURL, adminToken) {
  const response = await fetch(`${backendURL}/api/admin/poller-health`, {
    headers: {
      authorization: `Bearer ${adminToken}`,
      "user-agent": "oshireader-cloudflare-poller/1.0",
    },
    signal: AbortSignal.timeout(90_000),
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
    notification_health: stats.notification_health || null,
    watch_terms: stats.watch_terms,
    latest_poll: stats.latest_poll || recentEvents.find((event) => event.kind === "poll") || null,
    latest_successful_poll: successfulPoll,
    latest_apns: stats.latest_apns || recentEvents.find((event) => event.kind === "apns") || null,
    latest_relevant_apns: stats.latest_relevant_apns || null,
    pending_notifications: stats.pending_notifications,
  };
}

function notificationHealth(diagnostics) {
  const backendHealth = diagnostics.notification_health;
  if (backendHealth && typeof backendHealth.healthy === "boolean") {
    const activeNotifyTerms = backendHealth.active_notify_terms ?? 0;
    const atRiskTerms = backendHealth.active_notify_terms_without_verified_devices ?? 0;
    if (backendHealth.healthy) {
      const apns = diagnostics.apns || {};
      if (activeNotifyTerms > 0 && apns.configured !== true) {
        return {
          healthy: false,
          reason: "Backend APNs is not configured",
          active_notify_terms: activeNotifyTerms,
          at_risk_terms: activeNotifyTerms,
          active_silent_orphan_terms: backendHealth.active_silent_orphan_terms ?? 0,
        };
      }
      return {
        healthy: true,
        active_notify_terms: activeNotifyTerms,
        at_risk_terms: atRiskTerms,
        active_silent_orphan_terms: backendHealth.active_silent_orphan_terms ?? 0,
      };
    }
    return {
      healthy: false,
      reason: "Backend notification health is degraded",
      active_notify_terms: activeNotifyTerms,
      at_risk_terms: atRiskTerms,
      active_silent_orphan_terms: backendHealth.active_silent_orphan_terms ?? 0,
      at_risk_term_ids: backendHealth.active_notify_term_ids_without_verified_devices || [],
    };
  }

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

function pollHealth(
  diagnostics,
  staleAfterMinutes = DEFAULT_STALE_AFTER_MINUTES,
  activePollTimeoutMinutes = DEFAULT_ACTIVE_POLL_TIMEOUT_MINUTES,
) {
  const inputs = activePollingInputs(diagnostics);
  if (!inputs.available) {
    return {
      healthy: false,
      reason: "Polling diagnostics are missing or malformed watch_terms or pending_notifications",
    };
  }
  if (inputs.activeTerms <= 0 && inputs.pendingNotifications <= 0) {
    return {
      healthy: true,
      idle: true,
      reason: "no active watch terms or pending notifications",
    };
  }

  const event = diagnostics.latest_successful_poll;
  const completedAt = event?.created_at ? Date.parse(event.created_at) : Number.NaN;
  const activePollAgeMinutes = activeBackendPollAgeMinutes(diagnostics, completedAt);
  if (Number.isFinite(activePollAgeMinutes) && activePollAgeMinutes > activePollTimeoutMinutes) {
    return {
      healthy: false,
      in_progress: true,
      age_minutes: Math.max(0, Math.floor(activePollAgeMinutes)),
      reason: `Active backend poll has been running for ${Math.floor(activePollAgeMinutes)} minutes`,
    };
  }
  if (!Number.isFinite(completedAt)) {
    if (Number.isFinite(activePollAgeMinutes) && activePollAgeMinutes <= staleAfterMinutes) {
      return {
        healthy: true,
        in_progress: true,
        age_minutes: Math.max(0, Math.floor(activePollAgeMinutes)),
      };
    }
    return { healthy: false, reason: "No successful backend poll recorded" };
  }
  if (Number.isFinite(activePollAgeMinutes) && activePollAgeMinutes <= staleAfterMinutes) {
    return {
      healthy: true,
      in_progress: true,
      age_minutes: Math.max(0, Math.floor(activePollAgeMinutes)),
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
  const latestApns = diagnostics.latest_relevant_apns || diagnostics.latest_apns;
  return [
    `OshiReader poll watchdog degraded: ${reason}`,
    `latest_poll=${latestPoll?.status || "none"} at ${latestPoll?.created_at || "n/a"}`,
    `latest_successful_poll=${latestSuccessfulPoll?.status || "none"} at ${latestSuccessfulPoll?.created_at || "n/a"}`,
    `latest_apns=${latestApns?.status || "none"} at ${latestApns?.created_at || "n/a"}`,
    `pending_notifications=${(diagnostics.pending_notifications || []).length}`,
    `items_total=${diagnostics.items_total ?? "n/a"} matches_total=${diagnostics.matches_total ?? "n/a"}`,
  ].join("\n");
}

function notificationWatchdogSummary(health, diagnostics = {}) {
  const latestApns = diagnostics.latest_relevant_apns || diagnostics.latest_apns;
  return [
    `OshiReader notification watchdog degraded: ${health.reason || "unknown"}`,
    `active_notify_terms=${health.active_notify_terms ?? "n/a"} at_risk_terms=${health.at_risk_terms ?? "n/a"}`,
    `at_risk_keywords=${(health.at_risk_keywords || []).join(", ") || "n/a"}`,
    `verified_devices=${health.verified_devices ?? "n/a"}`,
    `latest_apns=${latestApns?.status || "none"} at ${latestApns?.created_at || "n/a"}`,
    `pending_notifications=${(diagnostics.pending_notifications || []).length}`,
  ].join("\n");
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function activePollingInputs(diagnostics) {
  const watchTerms = diagnostics.watch_terms;
  const pendingNotifications = diagnostics.pending_notifications;
  const available = Array.isArray(watchTerms) &&
    Array.isArray(pendingNotifications) &&
    watchTerms.every((term) => typeof term?.is_active === "boolean");
  if (!available) {
    return {
      available: false,
      activeTerms: 0,
      pendingNotifications: 0,
    };
  }
  const activeTerms = watchTerms.filter((term) => term.is_active);
  return {
    available: true,
    activeTerms: activeTerms.length,
    pendingNotifications: pendingNotifications.length,
  };
}

function activeBackendPollAgeMinutes(diagnostics, completedAt = Number.NaN) {
  const latestPoll = diagnostics.latest_poll;
  const latestPollStartedAt = latestPoll?.created_at ? Date.parse(latestPoll.created_at) : Number.NaN;
  const latestPollIsActive = latestPoll &&
    ["started", "running_past_request_timeout"].includes(latestPoll.status);
  if (!latestPollIsActive || !Number.isFinite(latestPollStartedAt)) {
    return Number.NaN;
  }
  if (Number.isFinite(completedAt) && latestPollStartedAt <= completedAt) {
    return Number.NaN;
  }
  return (Date.now() - latestPollStartedAt) / 60_000;
}

function scheduledPollDecision(
  diagnostics,
  minIntervalMinutes = DEFAULT_MIN_POLL_INTERVAL_MINUTES,
  staleAfterMinutes = DEFAULT_STALE_AFTER_MINUTES,
  activePollTimeoutMinutes = DEFAULT_ACTIVE_POLL_TIMEOUT_MINUTES,
) {
  const inputs = activePollingInputs(diagnostics);
  if (!inputs.available) {
    return {
      due: true,
      reason: "polling diagnostics are missing or malformed watch_terms or pending_notifications",
      ...inputs,
    };
  }
  if (inputs.activeTerms <= 0 && inputs.pendingNotifications <= 0) {
    return {
      due: false,
      reason: "no active watch terms or pending notifications",
      ...inputs,
    };
  }

  const event = diagnostics.latest_successful_poll;
  const completedAt = event?.created_at ? Date.parse(event.created_at) : Number.NaN;
  const activePollAgeMinutes = activeBackendPollAgeMinutes(diagnostics, completedAt);
  if (Number.isFinite(activePollAgeMinutes) && activePollAgeMinutes > activePollTimeoutMinutes) {
    return {
      due: false,
      reason: `active backend poll exceeded ${activePollTimeoutMinutes} minutes (${Math.max(0, Math.floor(activePollAgeMinutes))} minutes old)`,
      in_progress: true,
      stuck: true,
      age_minutes: Math.max(0, Math.floor(activePollAgeMinutes)),
      ...inputs,
    };
  }
  if (Number.isFinite(activePollAgeMinutes) && activePollAgeMinutes <= staleAfterMinutes) {
    return {
      due: false,
      reason: `backend poll is already in progress (${Math.max(0, Math.floor(activePollAgeMinutes))} minutes old)`,
      in_progress: true,
      age_minutes: Math.max(0, Math.floor(activePollAgeMinutes)),
      ...inputs,
    };
  }
  if (!Number.isFinite(completedAt)) {
    return { due: true, reason: "no successful backend poll recorded", ...inputs };
  }

  const ageMinutes = (Date.now() - completedAt) / 60_000;
  if (ageMinutes < minIntervalMinutes) {
    return {
      due: false,
      reason: `latest successful poll is still fresh (${Math.max(0, Math.floor(ageMinutes))} minutes old)`,
      age_minutes: Math.max(0, Math.floor(ageMinutes)),
      ...inputs,
    };
  }
  return {
    due: true,
    reason: `latest successful poll is ${Math.max(0, Math.floor(ageMinutes))} minutes old`,
    age_minutes: Math.max(0, Math.floor(ageMinutes)),
    ...inputs,
  };
}

export async function triggerBackendPoll(env, options = {}) {
  const adminToken = requireAdminToken(env);
  const backendURL = (env.BACKEND_URL || "https://oshireader.onrender.com").replace(/\/+$/, "");
  const startedAt = Date.now();
  const respectDueWindow = options.respectDueWindow === true;

  // Wake up the Render backend before any request. The free tier spins down
  // after inactivity and a cold start can take 30-60 s. This must run first —
  // before fetchBackendDiagnostics — so a cold instance is warm by the time
  // we call /api/admin/poller-health or /api/admin/poll.
  try {
    await fetch(`${backendURL}/api/health`, {
      signal: AbortSignal.timeout(60_000),
      headers: { "user-agent": "oshireader-cloudflare-poller/1.0" },
    });
  } catch (wakeError) {
    console.warn("Backend wake-up ping failed (continuing):", String(wakeError));
  }

  if (respectDueWindow) {
    const diagnostics = await fetchBackendDiagnostics(backendURL, adminToken);
    const decision = scheduledPollDecision(
      diagnostics,
      Number(env.MIN_POLL_INTERVAL_MINUTES ?? DEFAULT_MIN_POLL_INTERVAL_MINUTES),
      Number(env.STALE_AFTER_MINUTES ?? DEFAULT_STALE_AFTER_MINUTES),
      Number(env.ACTIVE_POLL_TIMEOUT_MINUTES ?? DEFAULT_ACTIVE_POLL_TIMEOUT_MINUTES),
    );
    if (!decision.due) {
      return {
        ok: true,
        skipped: true,
        backend_status: "poll skipped",
        reason: decision.reason,
        duration_ms: Date.now() - startedAt,
        diagnostics,
      };
    }
  }

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
      triggerBackendPoll(env, { respectDueWindow: true }).then(
        async (result) => {
          console.log("Scheduled feed poll completed", result);
          const health = pollHealth(
            result.diagnostics,
            Number(env.STALE_AFTER_MINUTES ?? DEFAULT_STALE_AFTER_MINUTES),
            Number(env.ACTIVE_POLL_TIMEOUT_MINUTES ?? DEFAULT_ACTIVE_POLL_TIMEOUT_MINUTES),
          );
          const notifications = notificationHealth(result.diagnostics);
          if (!health.healthy && !health.in_progress) {
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
    if ((request.method === "GET" || request.method === "HEAD") && url.pathname === "/liveness") {
      return json({ status: "ok", service: "oshireader-feed-poller" }, 200);
    }
    if (request.method === "GET" && url.pathname === "/health") {
      try {
        const backendURL = (env.BACKEND_URL || "https://oshireader.onrender.com").replace(/\/+$/, "");
        // Wake up a cold Render instance before fetching diagnostics. Without
        // this, a cold start (~50 s) causes fetchBackendDiagnostics to time out
        // and the health endpoint returns 503, making the monitor alarm even
        // when the last poll was recent.
        try {
          await fetch(`${backendURL}/api/health`, {
            signal: AbortSignal.timeout(90_000),
            headers: { "user-agent": "oshireader-cloudflare-poller/1.0" },
          });
        } catch (wakeError) {
          console.warn("Health endpoint wake-up ping failed:", String(wakeError));
        }
        const diagnostics = await fetchBackendDiagnostics(backendURL, requireAdminToken(env));
        const health = pollHealth(
          diagnostics,
          Number(env.STALE_AFTER_MINUTES ?? DEFAULT_STALE_AFTER_MINUTES),
          Number(env.ACTIVE_POLL_TIMEOUT_MINUTES ?? DEFAULT_ACTIVE_POLL_TIMEOUT_MINUTES),
        );
        const notifications = notificationHealth(diagnostics);
        // Feed freshness and notification readiness are separate operational
        // concerns. A missing device must not make ingestion look unhealthy;
        // scheduled polling still reports notification degradation through the
        // watchdog and this response's `notifications` field.
        const healthy = health.healthy;
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

    if (request.method === "GET" && url.pathname === "/rss-proxy") {
      try {
        return await proxySearchRss(request, env);
      } catch (error) {
        console.error("RSS proxy failed", error);
        return json({ detail: String(error) }, 502);
      }
    }

    if (request.method === "GET" && url.pathname === "/fivech-proxy") {
      try {
        return await proxyFiveCh(request, env);
      } catch (error) {
        console.error("5ch proxy failed", error);
        return json({ detail: String(error) }, 502);
      }
    }

    if (request.method === "POST" && url.pathname === "/run") {
      if (!requireBearer(request, env)) return unauthorized();
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
