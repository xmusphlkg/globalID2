import assert from "node:assert/strict";
import test from "node:test";
import { dispatchScheduledTasks } from "../src/lib/scheduled.ts";
import { matchWorkerRoute } from "../src/lib/router.ts";
import type { Env } from "../src/types.ts";

test("scheduled tasks use independent same-origin authenticated requests and continue after a failure", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const env = { PUBLIC_BASE_URL: "https://subscriptions.example.test", ADMIN_API_TOKEN: "secret", ENVIRONMENT: "production" } as unknown as Env;
  await dispatchScheduledTasks(env, async (url, init) => {
    calls.push({ url: String(url), init: init as RequestInit });
    return new Response(null, { status: calls.length === 2 ? 503 : 204 });
  });
  assert.deepEqual(calls.map(call => new URL(call.url).pathname), [
    "/api/internal/scheduled/maintenance", "/api/internal/scheduled/confirmation",
    "/api/internal/scheduled/campaign", "/api/internal/scheduled/situation", "/api/internal/scheduled/feedback",
  ]);
  for (const call of calls) {
    assert.equal(call.init.method, "POST");
    assert.equal(call.init.redirect, "error");
    assert.equal(new Headers(call.init.headers).get("authorization"), "Bearer secret");
    assert.equal(new Headers(call.init.headers).get("user-agent"), "GIDS-Release-Verifier/1.0");
    assert.equal(matchWorkerRoute("POST", new URL(call.url).pathname).name, "internal_scheduled_task");
    assert.equal(matchWorkerRoute("GET", new URL(call.url).pathname).name, "not_found");
  }
});

test("scheduled dispatch refuses unsafe origins and missing bearer credentials", async () => {
  for (const raw of ["http://example.test", "https://user@example.test", "https://example.test/prefix", "https://example.test/?query=1"]) {
    await assert.rejects(dispatchScheduledTasks({ PUBLIC_BASE_URL: raw, ADMIN_API_TOKEN: "secret" } as Env), /invalid_scheduled_dispatch_origin/);
  }
  await assert.rejects(dispatchScheduledTasks({ PUBLIC_BASE_URL: "https://example.test" } as Env), /scheduled_dispatch_not_configured/);
});
