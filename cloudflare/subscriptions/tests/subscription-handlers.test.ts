import assert from "node:assert/strict";
import test from "node:test";

import { listAudience, listSubscriptionOptions, subscriptionStats } from "../src/handlers/subscriptions.ts";
import type { D1Database } from "../src/lib/db.ts";
import type { Env } from "../src/types.ts";

function database(respond: (query: string, operation: "first" | "all" | "run") => unknown): D1Database {
  return { prepare(query) { const statement = {
    bind(..._values: unknown[]) { return statement; },
    async first<T>() { return respond(query, "first") as T | null; },
    async all<T>() { return respond(query, "all") as { results: T[] }; },
    async run() { return respond(query, "run"); },
  }; return statement; } };
}

test("subscription options propagate D1 failures to the worker boundary", async () => {
  const env: Env = { DB: database(() => { throw new Error("D1 unavailable"); }) };
  await assert.rejects(listSubscriptionOptions(new Request("https://example.test/api/subscriptions/options"), env), /D1 unavailable/);
});

test("subscription stats authorize before any database query", async () => {
  let queried = false;
  const env: Env = { ADMIN_API_TOKEN: "secret", DB: database(() => { queried = true; return null; }) };
  await assert.rejects(
    subscriptionStats(new Request("https://example.test/api/admin/stats"), env),
    (error: unknown) => error instanceof Error && error.message === "unauthorized",
  );
  assert.equal(queried, false);
});

test("audience handler preserves recipient projection and unsubscribe token routing", async () => {
  const env: Env = { ADMIN_API_TOKEN: "secret", DB: database((query, operation) => {
    if (operation === "first" && query.includes("subscription_lists")) return { id: "list-1" };
    if (operation === "all") return { results: [{ subscription_id: "sub-1", frequency: "weekly", locale: "en",
      timezone: "UTC", email: "reader@example.test", list_code: "reports" }] };
    return null;
  }) };
  const response = await listAudience(new Request("https://example.test/api/admin/audience", {
    method: "POST", headers: { authorization: "Bearer secret", "content-type": "application/json" },
    body: JSON.stringify({ list_code: "reports" }),
  }), env, {
    async readPayload(request) { return await request.json() as Record<string, unknown>; },
    async createSignedToken(_env, purpose, id) { return `${purpose}-${id}`; },
  });
  const body = await response.json() as { recipients: Array<{ unsubscribe_url: string }> };
  assert.equal(body.recipients[0].unsubscribe_url,
    "https://example.test/api/subscriptions/unsubscribe?token=unsubscribe-sub-1");
});
