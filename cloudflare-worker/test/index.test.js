import assert from "node:assert/strict";
import test from "node:test";

import worker, { triggerBackendPoll } from "../src/index.js";

test("liveness endpoint is fast and does not call the backend", async () => {
  const originalFetch = globalThis.fetch;
  let backendCalled = false;
  globalThis.fetch = async () => {
    backendCalled = true;
    throw new Error("backend should not be queried");
  };
  const response = await worker.fetch(
    new Request("https://worker.example/liveness"),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );
  globalThis.fetch = originalFetch;

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    status: "ok",
    service: "oshireader-feed-poller",
  });
  assert.equal(backendCalled, false);
});

test("health endpoint does not require the admin token", async () => {
  const originalFetch = globalThis.fetch;
  const now = new Date().toISOString();
  globalThis.fetch = async () => new Response(JSON.stringify({
    items_total: 10,
    matches_total: 12,
    watch_terms: [],
    pending_notifications: [],
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

test("health endpoint omits diagnostics and notifications without the admin token", async () => {
  const originalFetch = globalThis.fetch;
  const now = new Date().toISOString();
  globalThis.fetch = async () => new Response(JSON.stringify({
    items_total: 10,
    matches_total: 12,
    watch_terms: [{ id: 1, keyword: "Aiko", is_active: true, notify_on_new: true, notification_verified_devices: 0 }],
    pending_notifications: [],
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
  assert.equal(body.diagnostics, undefined);
  assert.equal(body.notifications, undefined);
  assert.equal(JSON.stringify(body).includes("Aiko"), false);
});

test("health endpoint includes diagnostics and notifications with the admin token", async () => {
  const originalFetch = globalThis.fetch;
  const now = new Date().toISOString();
  globalThis.fetch = async () => new Response(JSON.stringify({
    items_total: 10,
    matches_total: 12,
    watch_terms: [{ id: 1, keyword: "Aiko", is_active: true, notify_on_new: true, notification_verified_devices: 0 }],
    pending_notifications: [],
    recent_events: [{ id: 1, kind: "poll", status: "completed", created_at: now }],
  }), { status: 200 });
  const response = await worker.fetch(
    new Request("https://worker.example/health", { headers: { authorization: "Bearer secret" } }),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );
  globalThis.fetch = originalFetch;

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.status, "ok");
  assert.ok(body.diagnostics);
  assert.ok(body.notifications);
});

test("rss proxy requires the admin token", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/rss-proxy?target=google&query=Aiko"),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 401);
});

test("rss proxy rejects a same-length near-miss token", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/rss-proxy?target=google&query=Aiko", {
      headers: { authorization: "Bearer secreu" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 401);
});

test("rss proxy fetches only supported search targets", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  let upstreamURL = "";
  globalThis.fetch = async (url) => {
    upstreamURL = String(url);
    return new Response("<rss><channel /></rss>", { status: 200 });
  };

  const response = await worker.fetch(
    new Request("https://worker.example/rss-proxy?target=google&query=Aiko%20site%3Aexample.com", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/xml; charset=utf-8");
  assert.match(upstreamURL, /^https:\/\/news\.google\.com\/rss\/search\?/);
  assert.match(upstreamURL, /Aiko%20site%3Aexample\.com/);
});

test("rss proxy forwards google news locale parameters", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  let upstreamURL = "";
  let upstreamHeaders = {};
  globalThis.fetch = async (url, init) => {
    upstreamURL = String(url);
    upstreamHeaders = init.headers;
    return new Response("<rss><channel /></rss>", { status: 200 });
  };

  const response = await worker.fetch(
    new Request("https://worker.example/rss-proxy?target=google&query=BTS%20site%3Aallkpop.com&hl=en&gl=US&ceid=US%3Aen&accept_language=en%2Cko%3Bq%3D0.9%2Cja%3Bq%3D0.7", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 200);
  assert.match(upstreamURL, /hl=en/);
  assert.match(upstreamURL, /gl=US/);
  assert.match(upstreamURL, /ceid=US%3Aen/);
  assert.equal(upstreamHeaders["accept-language"], "en,ko;q=0.9,ja;q=0.7");
});

test("rss proxy forwards bing market parameter", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  let upstreamURL = "";
  let upstreamHeaders = {};
  globalThis.fetch = async (url, init) => {
    upstreamURL = String(url);
    upstreamHeaders = init.headers;
    return new Response("<rss><channel /></rss>", { status: 200 });
  };

  const response = await worker.fetch(
    new Request("https://worker.example/rss-proxy?target=bing&query=BTS%20site%3Aallkpop.com&mkt=en-US&accept_language=en%2Cko%3Bq%3D0.9%2Cja%3Bq%3D0.7", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 200);
  assert.match(upstreamURL, /^https:\/\/www\.bing\.com\/news\/search\?/);
  assert.match(upstreamURL, /mkt=en-US/);
  assert.equal(upstreamHeaders["accept-language"], "en,ko;q=0.9,ja;q=0.7");
});

test("rss proxy normalizes bearer token formatting", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response("<rss><channel /></rss>", { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/rss-proxy?target=google&query=Aiko", {
      headers: { authorization: " Bearer secret " },
    }),
    { ADMIN_API_TOKEN: " Bearer secret " },
  );

  assert.equal(response.status, 200);
});

test("rss proxy rejects unsupported targets", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/rss-proxy?target=other&query=Aiko", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 400);
});

test("5ch proxy fetches only whitelisted board resources", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  let upstreamURL = "";
  globalThis.fetch = async (url) => {
    upstreamURL = String(url);
    return new Response("123.dat<>Aiko thread (4)\n", { status: 200 });
  };

  const response = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=subject&board_url=http%3A%2F%2Ftoro.2ch.sc%2Fnogizaka%2F", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "123.dat<>Aiko thread (4)\n");
  assert.equal(upstreamURL, "http://toro.2ch.sc/nogizaka/subject.txt");
});

test("5ch proxy allows actor board resources used by backend priority scan", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  let upstreamURL = "";
  globalThis.fetch = async (url) => {
    upstreamURL = String(url);
    return new Response("1779315692.dat<>吉沢亮48 (485)\n", { status: 200 });
  };

  const response = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=subject&board_url=http%3A%2F%2Fanago.2ch.sc%2Factor%2F", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "1779315692.dat<>吉沢亮48 (485)\n");
  assert.equal(upstreamURL, "http://anago.2ch.sc/actor/subject.txt");
});

test("5ch proxy allows extra bbsmenu boards with numeric and hobby paths", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  const upstreamURLs = [];
  globalThis.fetch = async (url) => {
    upstreamURLs.push(String(url));
    return new Response("1711500623.dat<>吉沢亮 extra thread (12)\n", { status: 200 });
  };

  const numeric = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=subject&board_url=http%3A%2F%2Fnozomi.2ch.sc%2F4649%2F", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );
  const hobby = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=subject&board_url=http%3A%2F%2Fsweet.2ch.sc%2Fpatisserie%2F", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(numeric.status, 200);
  assert.equal(hobby.status, 200);
  assert.deepEqual(upstreamURLs, [
    "http://nozomi.2ch.sc/4649/subject.txt",
    "http://sweet.2ch.sc/patisserie/subject.txt",
  ]);
});

test("5ch proxy allows news5plus board used by backend priority scan", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  let upstreamURL = "";
  globalThis.fetch = async (url) => {
    upstreamURL = String(url);
    return new Response("1764163919.dat<>吉沢亮 NY and LA screening (8)\n", { status: 200 });
  };

  const response = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=subject&board_url=http%3A%2F%2Fanago.2ch.sc%2Fnews5plus%2F", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 200);
  assert.equal(upstreamURL, "http://anago.2ch.sc/news5plus/subject.txt");
});

test("5ch proxy rejects non-whitelisted boards", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=subject&board_url=http%3A%2F%2Fexample.com%2Fnews%2F", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 400);
});

test("5ch proxy validates dat thread ids", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=dat&board_url=http%3A%2F%2Ftoro.2ch.sc%2Fnogizaka%2F&thread_id=../../secret", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 400);
});

test("5ch proxy fetches whitelisted real itest subback pages", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  let upstreamURL = "";
  globalThis.fetch = async (url) => {
    upstreamURL = String(url);
    return new Response("* [2026年6月25日 17時59分 話題度:43 13レス Aiko thread](https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369)", {
      status: 200,
    });
  };

  const response = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=itest_subback&board_key=nogizaka", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 200);
  assert.equal(upstreamURL, "https://r.jina.ai/http://https://itest.5ch.io/subback/nogizaka");
  assert.match(await response.text(), /Aiko thread/);
});

test("5ch proxy fetches whitelisted real itest dat pages", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  let upstreamURL = "";
  globalThis.fetch = async (url) => {
    upstreamURL = String(url);
    return new Response("name<>sage<>2026/06/26(金) 20:00:21.58 ID:x<>latest<>\n", {
      status: 200,
    });
  };

  const response = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=itest_dat&server=mevius&board_key=nogizaka&thread_id=1782471621", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 200);
  assert.equal(upstreamURL, "https://r.jina.ai/http://http://mevius.5ch.net/nogizaka/dat/1782471621.dat");
  assert.match(await response.text(), /2026\/06\/26/);
});

test("5ch proxy rejects invalid real itest dat server", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=itest_dat&server=../secret&board_key=nogizaka&thread_id=1782471621", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 400);
});

test("5ch proxy retries flaky real itest subback upstream failures", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  let attempts = 0;
  globalThis.fetch = async () => {
    attempts += 1;
    if (attempts === 1) {
      return new Response("bad gateway", { status: 502 });
    }
    return new Response("* [2026年6月25日 17時59分 話題度:43 13レス Aiko retry](https://itest.5ch.io/mevius/test/read.cgi/nogizaka/1782410369)", {
      status: 200,
    });
  };

  const response = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=itest_subback&board_key=nogizaka", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 200);
  assert.equal(attempts, 2);
  assert.match(await response.text(), /Aiko retry/);
});

test("5ch proxy rejects non-whitelisted itest board keys", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/fivech-proxy?resource=itest_subback&board_key=../../secret", {
      headers: { authorization: "Bearer secret" },
    }),
    { ADMIN_API_TOKEN: "secret" },
  );

  assert.equal(response.status, 400);
});

test("health reports stale polling as degraded", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    watch_terms: [{ id: 1, keyword: "Aiko", is_active: true }],
    pending_notifications: [],
    recent_events: [{
      id: 1,
      kind: "poll",
      status: "completed",
      created_at: new Date(Date.now() - 7 * 60 * 60_000).toISOString(),
    }],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health"),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 503);
  assert.equal((await response.json()).status, "degraded");
});

test("health reports missing polling inputs as degraded", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    latest_successful_poll: {
      id: 99,
      kind: "poll",
      status: "completed",
      created_at: new Date().toISOString(),
    },
    recent_events: [],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health"),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 503);
  const body = await response.json();
  assert.equal(body.status, "degraded");
  assert.match(body.reason, /missing or malformed watch_terms/);
});

test("health reports malformed watch term inputs as degraded", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    watch_terms: [{ id: 1, keyword: "Aiko" }],
    pending_notifications: [],
    latest_successful_poll: {
      id: 99,
      kind: "poll",
      status: "completed",
      created_at: new Date().toISOString(),
    },
    recent_events: [],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health"),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 503);
  const body = await response.json();
  assert.equal(body.status, "degraded");
  assert.match(body.reason, /missing or malformed watch_terms/);
});

test("health uses dedicated latest successful poll outside recent events", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  const now = new Date().toISOString();
  globalThis.fetch = async () => new Response(JSON.stringify({
    watch_terms: [],
    pending_notifications: [],
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
    new Request("https://worker.example/health", { headers: { authorization: "Bearer secret" } }),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 200);
  assert.equal((await response.json()).diagnostics.latest_successful_poll.id, 99);
});

test("health exposes latest relevant APNs separately from stale history", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  const now = new Date().toISOString();
  globalThis.fetch = async () => new Response(JSON.stringify({
    watch_terms: [],
    pending_notifications: [],
    latest_successful_poll: {
      id: 99,
      kind: "poll",
      status: "completed",
      created_at: now,
    },
    latest_apns: {
      id: 7,
      kind: "apns",
      status: "skipped",
      message: "Watch term notifications are disabled",
      payload: { term_id: 16 },
      created_at: now,
    },
    latest_relevant_apns: {
      id: 6,
      kind: "apns",
      status: "attempted",
      payload: { delivered_count: 1 },
      created_at: now,
    },
    recent_events: [],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health", { headers: { authorization: "Bearer secret" } }),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.diagnostics.latest_apns.id, 7);
  assert.equal(body.diagnostics.latest_relevant_apns.id, 6);
});

test("health treats a fresh started poll as in progress", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    watch_terms: [{ id: 1, keyword: "Aiko", is_active: true }],
    pending_notifications: [],
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
    watch_terms: [{ id: 1, keyword: "Aiko", is_active: true }],
    pending_notifications: [],
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

test("health reports an old request-timeout marker as degraded", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    watch_terms: [{ id: 1, keyword: "Aiko", is_active: true }],
    pending_notifications: [],
    latest_poll: {
      id: 5,
      kind: "poll",
      status: "running_past_request_timeout",
      created_at: new Date(Date.now() - 31 * 60_000).toISOString(),
    },
    latest_successful_poll: {
      id: 4,
      kind: "poll",
      status: "completed",
      created_at: new Date(Date.now() - 60 * 60_000).toISOString(),
    },
    recent_events: [],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health"),
    {
      ADMIN_API_TOKEN: "secret",
      BACKEND_URL: "https://backend.example",
      ACTIVE_POLL_TIMEOUT_MINUTES: "30",
    },
  );

  assert.equal(response.status, 503);
  const body = await response.json();
  assert.equal(body.status, "degraded");
  assert.equal(body.in_progress, true);
  assert.match(body.reason, /Active backend poll has been running/);
});

test("health keeps polling healthy when active notification terms lack devices", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    apns: {
      configured: true,
      device_tokens_by_environment_and_verification: {
        production: { verified: 1, unverified: 0 },
      },
    },
    watch_terms: [
      {
        keyword: "Aiko",
        is_active: true,
        notify_on_new: true,
        notification_verified_devices: 0,
      },
    ],
    pending_notifications: [],
    latest_successful_poll: {
      id: 1,
      kind: "poll",
      status: "completed",
      created_at: new Date().toISOString(),
    },
    recent_events: [],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health", { headers: { authorization: "Bearer secret" } }),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.status, "ok");
  assert.equal(body.healthy, true);
  assert.equal(body.notifications.healthy, false);
  assert.equal(body.notifications.at_risk_terms, 1);
  assert.deepEqual(body.notifications.at_risk_keywords, ["Aiko"]);
});

test("health trusts backend notification grace period", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    apns: {
      configured: true,
      device_tokens_by_environment_and_verification: {
        production: { verified: 1, unverified: 0 },
      },
    },
    notification_health: {
      healthy: true,
      active_notify_terms: 2,
      active_silent_orphan_terms: 0,
      active_notify_terms_without_verified_devices: 0,
      orphaned_notification_grace_minutes: 60,
      active_silent_orphan_term_ids: [],
      active_notify_term_ids_without_verified_devices: [],
    },
    watch_terms: [
      {
        keyword: "Aiko",
        is_active: true,
        notify_on_new: true,
        notification_verified_devices: 0,
      },
    ],
    pending_notifications: [],
    latest_successful_poll: {
      id: 1,
      kind: "poll",
      status: "completed",
      created_at: new Date().toISOString(),
    },
    recent_events: [],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health", { headers: { authorization: "Bearer secret" } }),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.status, "ok");
  assert.equal(body.notifications.healthy, true);
  assert.equal(body.notifications.active_notify_terms, 2);
});

test("health keeps polling healthy when APNs is missing", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    apns: {
      configured: false,
      device_tokens_by_environment_and_verification: {
        production: { verified: 1, unverified: 0 },
      },
    },
    notification_health: {
      healthy: true,
      active_notify_terms: 1,
      active_silent_orphan_terms: 0,
      active_notify_terms_without_verified_devices: 0,
      orphaned_notification_grace_minutes: 60,
      active_silent_orphan_term_ids: [],
      active_notify_term_ids_without_verified_devices: [],
    },
    watch_terms: [
      {
        keyword: "Aiko",
        is_active: true,
        notify_on_new: true,
        notification_verified_devices: 1,
      },
    ],
    pending_notifications: [],
    latest_successful_poll: {
      id: 1,
      kind: "poll",
      status: "completed",
      created_at: new Date().toISOString(),
    },
    recent_events: [],
  }), { status: 200 });

  const response = await worker.fetch(
    new Request("https://worker.example/health", { headers: { authorization: "Bearer secret" } }),
    { ADMIN_API_TOKEN: "secret", BACKEND_URL: "https://backend.example" },
  );

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.status, "ok");
  assert.equal(body.healthy, true);
  assert.equal(body.notifications.healthy, false);
  assert.match(body.notifications.reason, /APNs is not configured/);
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
    if (url === "https://backend.example/api/health") {
      assert.equal(options.headers["user-agent"], "oshireader-cloudflare-poller/1.0");
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    }
    assert.equal(options.headers.authorization, "Bearer secret");
    if (url === "https://backend.example/api/admin/poll") {
      assert.equal(options.method, "POST");
      return new Response(JSON.stringify({ status: "poll completed" }), {
        status: 200,
      });
    }
    assert.equal(url, "https://backend.example/api/admin/poller-health");
    return new Response(JSON.stringify({
      items_total: 10,
      matches_total: 12,
      latest_relevant_apns: {
        id: 3,
        kind: "apns",
        status: "attempted",
        payload: { delivered_count: 1 },
      },
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
  assert.equal(result.diagnostics.latest_relevant_apns.id, 3);
});

test("scheduled poll skips backend poll when no active work exists", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  const requestedURLs = [];
  globalThis.fetch = async (url) => {
    requestedURLs.push(String(url));
    if (String(url).endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    }
    assert.equal(url, "https://backend.example/api/admin/poller-health");
    return new Response(JSON.stringify({
      watch_terms: [],
      pending_notifications: [],
      latest_successful_poll: {
        id: 1,
        kind: "poll",
        status: "completed",
        created_at: new Date(Date.now() - 3 * 60 * 60_000).toISOString(),
      },
      recent_events: [],
    }), { status: 200 });
  };

  const result = await triggerBackendPoll({
    ADMIN_API_TOKEN: "secret",
    BACKEND_URL: "https://backend.example",
  }, { respectDueWindow: true });

  assert.equal(result.ok, true);
  assert.equal(result.skipped, true);
  assert.equal(result.backend_status, "poll skipped");
  assert.match(result.reason, /no active watch terms/);
  assert.deepEqual(requestedURLs, [
    "https://backend.example/api/health",
    "https://backend.example/api/admin/poller-health",
  ]);
});

test("scheduled poll does not treat missing polling inputs as idle", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  const requestedURLs = [];
  globalThis.fetch = async (url) => {
    requestedURLs.push(String(url));
    if (String(url).endsWith("/api/admin/poll")) {
      return new Response(JSON.stringify({ status: "poll completed" }), { status: 200 });
    }
    if (String(url).endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    }
    assert.equal(url, "https://backend.example/api/admin/poller-health");
    return new Response(JSON.stringify({
      latest_successful_poll: {
        id: 1,
        kind: "poll",
        status: "completed",
        created_at: new Date(Date.now() - 3 * 60 * 60_000).toISOString(),
      },
      recent_events: [],
    }), { status: 200 });
  };

  const result = await triggerBackendPoll({
    ADMIN_API_TOKEN: "secret",
    BACKEND_URL: "https://backend.example",
  }, { respectDueWindow: true });

  assert.equal(result.ok, true);
  assert.equal(result.skipped, undefined);
  assert.equal(result.backend_status, "poll completed");
  assert.deepEqual(requestedURLs, [
    "https://backend.example/api/health",
    "https://backend.example/api/admin/poller-health",
    "https://backend.example/api/admin/poll",
    "https://backend.example/api/admin/poller-health",
  ]);
});

test("scheduled poll does not treat malformed watch terms as idle", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  const requestedURLs = [];
  globalThis.fetch = async (url) => {
    requestedURLs.push(String(url));
    if (String(url).endsWith("/api/admin/poll")) {
      return new Response(JSON.stringify({ status: "poll completed" }), { status: 200 });
    }
    if (String(url).endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    }
    assert.equal(url, "https://backend.example/api/admin/poller-health");
    return new Response(JSON.stringify({
      watch_terms: [{ id: 1, keyword: "Aiko" }],
      pending_notifications: [],
      latest_successful_poll: {
        id: 1,
        kind: "poll",
        status: "completed",
        created_at: new Date(Date.now() - 3 * 60 * 60_000).toISOString(),
      },
      recent_events: [],
    }), { status: 200 });
  };

  const result = await triggerBackendPoll({
    ADMIN_API_TOKEN: "secret",
    BACKEND_URL: "https://backend.example",
  }, { respectDueWindow: true });

  assert.equal(result.ok, true);
  assert.equal(result.skipped, undefined);
  assert.equal(result.backend_status, "poll completed");
  assert.deepEqual(requestedURLs, [
    "https://backend.example/api/health",
    "https://backend.example/api/admin/poller-health",
    "https://backend.example/api/admin/poll",
    "https://backend.example/api/admin/poller-health",
  ]);
});

test("scheduled poll skips backend poll when latest success is fresh", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  const requestedURLs = [];
  globalThis.fetch = async (url) => {
    requestedURLs.push(String(url));
    if (String(url).endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    }
    assert.equal(url, "https://backend.example/api/admin/poller-health");
    return new Response(JSON.stringify({
      watch_terms: [{ id: 1, keyword: "Aiko", is_active: true }],
      pending_notifications: [],
      latest_successful_poll: {
        id: 1,
        kind: "poll",
        status: "completed",
        created_at: new Date(Date.now() - 10 * 60_000).toISOString(),
      },
      recent_events: [],
    }), { status: 200 });
  };

  const result = await triggerBackendPoll({
    ADMIN_API_TOKEN: "secret",
    BACKEND_URL: "https://backend.example",
    MIN_POLL_INTERVAL_MINUTES: "55",
  }, { respectDueWindow: true });

  assert.equal(result.ok, true);
  assert.equal(result.skipped, true);
  assert.match(result.reason, /still fresh/);
  assert.deepEqual(requestedURLs, [
    "https://backend.example/api/health",
    "https://backend.example/api/admin/poller-health",
  ]);
});

test("scheduled poll skips backend poll when poll is already in progress", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  const requestedURLs = [];
  globalThis.fetch = async (url) => {
    requestedURLs.push(String(url));
    if (String(url).endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    }
    assert.equal(url, "https://backend.example/api/admin/poller-health");
    return new Response(JSON.stringify({
      watch_terms: [{ id: 1, keyword: "Aiko", is_active: true }],
      pending_notifications: [],
      latest_poll: {
        id: 2,
        kind: "poll",
        status: "started",
        created_at: new Date(Date.now() - 10 * 60_000).toISOString(),
      },
      recent_events: [],
    }), { status: 200 });
  };

  const result = await triggerBackendPoll({
    ADMIN_API_TOKEN: "secret",
    BACKEND_URL: "https://backend.example",
    STALE_AFTER_MINUTES: "240",
  }, { respectDueWindow: true });

  assert.equal(result.ok, true);
  assert.equal(result.skipped, true);
  assert.match(result.reason, /already in progress/);
  assert.deepEqual(requestedURLs, [
    "https://backend.example/api/health",
    "https://backend.example/api/admin/poller-health",
  ]);
});

test("scheduled poll skips when an active poll exceeds the timeout", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  const requestedURLs = [];
  let diagnosticsCalls = 0;
  globalThis.fetch = async (url) => {
    requestedURLs.push(String(url));
    if (String(url).endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    }
    assert.equal(url, "https://backend.example/api/admin/poller-health");
    diagnosticsCalls += 1;
    return new Response(JSON.stringify({
      watch_terms: [{ id: 1, keyword: "Aiko", is_active: true }],
      pending_notifications: [],
      latest_poll: {
        id: 2,
        kind: "poll",
        status: "running_past_request_timeout",
        created_at: new Date(Date.now() - 31 * 60_000).toISOString(),
      },
      latest_successful_poll: {
        id: 1,
        kind: "poll",
        status: "completed",
        created_at: new Date(Date.now() - 90 * 60_000).toISOString(),
      },
      recent_events: [],
    }), { status: 200 });
  };

  const result = await triggerBackendPoll({
    ADMIN_API_TOKEN: "secret",
    BACKEND_URL: "https://backend.example",
    ACTIVE_POLL_TIMEOUT_MINUTES: "30",
    POLL_RETRIES: "0",
  }, { respectDueWindow: true });

  assert.equal(result.ok, true);
  assert.equal(result.skipped, true);
  assert.equal(result.backend_status, "poll skipped");
  assert.equal(diagnosticsCalls, 1);
  assert.deepEqual(requestedURLs, [
    "https://backend.example/api/health",
    "https://backend.example/api/admin/poller-health",
  ]);
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
    if (url.endsWith("/api/admin/poller-health")) {
      return new Response(JSON.stringify({
        items_total: 10,
        matches_total: 12,
        watch_terms: [{ id: 1, keyword: "Aiko", is_active: true }],
        pending_notifications: [],
        latest_successful_poll: {
          id: 1,
          kind: "poll",
          status: "completed",
          created_at: new Date(Date.now() - 7 * 60 * 60_000).toISOString(),
        },
        recent_events: [],
      }), { status: 200 });
    }
    if (url === "https://hooks.example/watchdog") {
      webhookPayloads.push(true);
      return new Response("ok", { status: 200 });
    }
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
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

test("scheduled poll notifies watchdog when backend-graced notification health is degraded", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  const webhookPayloads = [];
  globalThis.fetch = async (url, options) => {
    if (url.endsWith("/api/admin/poll")) {
      return new Response(JSON.stringify({ status: "poll completed" }), { status: 200 });
    }
    if (url.endsWith("/api/admin/poller-health")) {
      return new Response(JSON.stringify({
        apns: {
          configured: true,
          device_tokens_by_environment_and_verification: {
            production: { verified: 1, unverified: 0 },
          },
        },
        watch_terms: [
          {
            id: 7,
            keyword: "Aiko",
            is_active: true,
            notify_on_new: true,
            notification_verified_devices: 0,
          },
        ],
        // notification_health is the backend's grace-period-vetted computation
        // (see ORPHANED_NOTIFICATION_GRACE_MINUTES) — only this shape should
        // trigger a watchdog alert.
        notification_health: {
          healthy: false,
          active_notify_terms: 1,
          active_notify_terms_without_verified_devices: 1,
          active_silent_orphan_terms: 0,
          active_notify_term_ids_without_verified_devices: [7],
        },
        pending_notifications: [],
        latest_successful_poll: {
          id: 1,
          kind: "poll",
          status: "completed",
          created_at: new Date().toISOString(),
        },
        recent_events: [],
      }), { status: 200 });
    }
    if (url === "https://hooks.example/watchdog") {
      const payload = await options.json?.() || JSON.parse(options.body);
      webhookPayloads.push(payload.text);
      return new Response("ok", { status: 200 });
    }
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
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
  assert.match(webhookPayloads[0], /notification watchdog degraded/);
  assert.match(webhookPayloads[0], /Aiko/);
});

test("scheduled poll does not alert on the ungraced legacy notification fallback", async (context) => {
  // When the backend response is missing notification_health (older/degraded
  // backend), notificationHealth() falls back to computing from watch_terms
  // directly — with no way to tell a brand-new term from one broken for hours.
  // That result must not reach the webhook (graced: false).
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  const webhookPayloads = [];
  globalThis.fetch = async (url, options) => {
    if (url.endsWith("/api/admin/poll")) {
      return new Response(JSON.stringify({ status: "poll completed" }), { status: 200 });
    }
    if (url.endsWith("/api/admin/poller-health")) {
      return new Response(JSON.stringify({
        apns: {
          configured: true,
          device_tokens_by_environment_and_verification: {
            production: { verified: 1, unverified: 0 },
          },
        },
        watch_terms: [
          {
            keyword: "Aiko",
            is_active: true,
            notify_on_new: true,
            notification_verified_devices: 0,
          },
        ],
        pending_notifications: [],
        latest_successful_poll: {
          id: 1,
          kind: "poll",
          status: "completed",
          created_at: new Date().toISOString(),
        },
        recent_events: [],
      }), { status: 200 });
    }
    if (url === "https://hooks.example/watchdog") {
      const payload = await options.json?.() || JSON.parse(options.body);
      webhookPayloads.push(payload.text);
      return new Response("ok", { status: 200 });
    }
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
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

  assert.equal(webhookPayloads.length, 0);
});
