import assert from "node:assert/strict";
import test from "node:test";

import worker, { triggerBackendPoll } from "../src/index.js";

test("health endpoint does not require the admin token", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/health"),
    {},
  );

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    status: "ok",
    scheduler: "cloudflare-cron",
  });
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
    assert.equal(url, "https://backend.example/api/admin/poll");
    assert.equal(options.method, "POST");
    assert.equal(options.headers.authorization, "Bearer secret");
    return new Response(JSON.stringify({ status: "poll completed" }), {
      status: 200,
    });
  };

  const result = await triggerBackendPoll({
    ADMIN_API_TOKEN: "secret",
    BACKEND_URL: "https://backend.example/",
  });

  assert.equal(result.ok, true);
  assert.equal(result.backend_status, "poll completed");
});
