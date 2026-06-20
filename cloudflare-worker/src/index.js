const POLL_TIMEOUT_MS = 4 * 60 * 1000;

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

export async function triggerBackendPoll(env) {
  const adminToken = requireAdminToken(env);
  const backendURL = (env.BACKEND_URL || "https://oshireader.onrender.com").replace(/\/+$/, "");
  const startedAt = Date.now();

  const response = await fetch(`${backendURL}/api/admin/poll`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${adminToken}`,
      "content-type": "application/json",
      "user-agent": "oshireader-cloudflare-poller/1.0",
    },
    signal: AbortSignal.timeout(POLL_TIMEOUT_MS),
  });
  const responseBody = await response.text();
  const durationMs = Date.now() - startedAt;

  if (!response.ok) {
    throw new Error(
      `Backend poll failed: HTTP ${response.status}, duration=${durationMs}ms, body=${responseBody.slice(0, 500)}`,
    );
  }

  let result;
  try {
    result = JSON.parse(responseBody);
  } catch {
    result = { status: responseBody || "poll completed" };
  }

  return {
    ok: true,
    backend_status: result.status,
    duration_ms: durationMs,
  };
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(
      triggerBackendPoll(env).then(
        (result) => console.log("Scheduled feed poll completed", result),
        (error) => {
          console.error("Scheduled feed poll failed", error);
          throw error;
        },
      ),
    );
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ status: "ok", scheduler: "cloudflare-cron" });
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
