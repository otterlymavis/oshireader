import assert from "node:assert/strict";
import test from "node:test";

import worker, { triggerBackendPoll } from "../src/index.js";

test("health endpoint does not require the admin token", async () => {
  const originalFetch = globalThis.fetch;
  const now = new Date().toISOString();
  globalThis.fetch = async () => new Response(JSON.stringify({
    items_total: 10,
    matches_total: 12,
    recent_events: [{ id: 1, kind: "poll", status: "completed", created_at: now }],
  }), { status: 200 });
  const response = await worker.fetch(
    new Request("https://worker.example/health"),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );
  globalThis.fetch = originalFetch;

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.status, "ok");
  assert.equal(body.scheduler, "cloudflare-cron");
  assert.equal(body.healthy, true);
});

test("health reports stale polling as degraded", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    recent_events: [{
      id: 1,
      kind: "poll",
      status: "completed",
      created_at: new Date(Date.now() - 60 * 60_000).toISOString(),
    }],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health"),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 503);
  assert.equal((await response.json()).status, "degraded");
});

test("health uses dedicated latest successful poll outside recent events", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  const now = new Date().toISOString();
  globalThis.fetch = async () => new Response(JSON.stringify({
    latest_successful_poll: {
      id: 99,
      kind: "poll",
      status: "completed",
      created_at: now,
    },
    recent_events: [
      { id: 101, kind: "apns", status: "attempted" },
      { id: 100, kind: "apns", status: "skipped" },
    ],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health"),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 200);
  assert.equal((await response.json()).diagnostics.latest_successful_poll.id, 99);
});

test("health treats a fresh started poll as in progress", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    latest_poll: {
      id: 3,
      kind: "poll",
      status: "started",
      created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    },
    latest_successful_poll: null,
    recent_events: [],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health"),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.healthy, true);
  assert.equal(body.in_progress, true);
});

test("health treats a fresh request-timeout marker as in progress", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    latest_poll: {
      id: 4,
      kind: "poll",
      status: "running_past_request_timeout",
      created_at: new Date(Date.now() - 2 * 60_000).toISOString(),
    },
    latest_successful_poll: null,
    recent_events: [],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health"),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 200);
  assert.equal((await response.json()).in_progress, true);
});

test("manual run rejects callers without the token", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/run", { method: "POST" }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 401);
});

test("poll sends the backend bearer token", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (url, options) => {
    assert.equal(options.headers.authorization, "Bearer secret");
    if (url === "https://backend.example/api/admin/poll") {
      assert.equal(options.method, "POST");
      return new Response(JSON.stringify({ status: "poll completed" }), {
        status: 200,
      });
    }
    assert.equal(url, "https://backend.example/api/admin/stats");
    return new Response(JSON.stringify({
      items_total: 10,
      matches_total: 12,
      recent_events: [
        { id: 2, kind: "poll", status: "completed" },
        { id: 1, kind: "apns", status: "attempted", payload: { delivered_count: 1 } },
      ],
    }), { status: 200 });
  };

  const result = await triggerBackendPoll({
    ADMIN_API_TOKEN: "secret",
    BACKEND_URL: "https://backend.example/",
  });

  assert.equal(result.ok, true);
  assert.equal(result.backend_status, "poll completed");
  assert.equal(result.diagnostics.latest_poll.id, 2);
  assert.equal(result.diagnostics.latest_apns.payload.delivered_count, 1);
});

test("poll retries a transient backend failure", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  let pollAttempts = 0;
  globalThis.fetch = async (url) => {
    if (url.endsWith("/api/admin/poll")) {
      pollAttempts += 1;
      if (pollAttempts === 1) throw new Error("temporary network failure");
      return new Response(JSON.stringify({ status: "poll completed" }), { status: 200 });
    }
    return new Response(JSON.stringify({ recent_events: [] }), { status: 200 });
  };

  await triggerBackendPoll({
    ADMIN_API_TOKEN: "secret",
    BACKEND_URL: "https://backend.example",
    POLL_RETRIES: "2",
    POLL_RETRY_DELAY_MS: "0",
  });

  assert.equal(pollAttempts, 2);
});

test("scheduled poll notifies watchdog when diagnostics are degraded", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  const webhookPayloads = [];
  globalThis.fetch = async (url) => {
    if (url.endsWith("/api/admin/poll")) {
      return new Response(JSON.stringify({ status: "poll completed" }), { status: 200 });
    }
    if (url.endsWith("/api/admin/stats")) {
      return new Response(JSON.stringify({
        items_total: 10,
        matches_total: 12,
        latest_successful_poll: {
          id: 1,
          kind: "poll",
          status: "completed",
          created_at: new Date(Date.now() - 60 * 60_000).toISOString(),
        },
        recent_events: [],
      }), { status: 200 });
    }
    if (url === "https://hooks.example/watchdog") {
      webhookPayloads.push(true);
      return new Response("ok", { status: 200 });
    }
    throw new Error(`unexpected url ${url}`);
  };
  const pending = [];
  await worker.scheduled(null, {
    ADMIN_API_TOKEN: "secret",
    BACKEND_URL: "https://backend.example",
    ALERT_WEBHOOK_URL: "https://hooks.example/watchdog",
  }, {
    waitUntil(promise) {
      pending.push(promise);
    },
  });
  await Promise.all(pending);

  assert.equal(webhookPayloads.length, 1);
});
