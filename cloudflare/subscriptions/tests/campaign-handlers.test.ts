import assert from "node:assert/strict";
import test from "node:test";

import { createCampaignHandlers } from "../src/handlers/campaigns.ts";
import type { D1Database } from "../src/lib/db.ts";
import type { Env } from "../src/types.ts";

function database(respond: (query: string, operation: "first" | "all" | "run") => unknown): D1Database {
  return {
    prepare(query) {
      const statement = {
        bind(..._values: unknown[]) { return statement; },
        async first<T>() { return respond(query, "first") as T | null; },
        async all<T>() { return respond(query, "all") as { results: T[] }; },
        async run() { return respond(query, "run"); },
      };
      return statement;
    },
  };
}

const deps = {
  async readPayload(request: Request) {
    return request.headers.get("content-type")?.includes("json")
      ? await request.json() as Record<string, unknown>
      : {};
  },
  async createSignedToken() { return "signed-token"; },
  smtpConfig() { return null; },
  async sendSmtpEmail() {},
};

test("admin campaign routes enforce authorization before querying D1", async () => {
  let queried = false;
  const env: Env = {
    ADMIN_API_TOKEN: "secret",
    DB: database(() => { queried = true; return null; }),
  };
  const handlers = createCampaignHandlers(deps);
  await assert.rejects(
    handlers.list(new Request("https://example.test/api/admin/notifications"), env),
    (error: unknown) => error instanceof Error && error.message === "unauthorized",
  );
  assert.equal(queried, false);
});

test("a routed campaign detail request reaches its handler", async () => {
  const env: Env = { ADMIN_API_TOKEN: "secret", DB: database(() => null) };
  const handlers = createCampaignHandlers(deps);
  const response = await handlers.get(new Request("https://example.test/api/admin/notifications/missing", {
    headers: { authorization: "Bearer secret" },
  }), env);
  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), { error: "notification_campaign_not_found" });
});

test("D1 failures propagate to the worker error boundary", async () => {
  const env: Env = {
    ADMIN_API_TOKEN: "secret",
    DB: database(() => { throw new Error("D1 unavailable"); }),
  };
  const handlers = createCampaignHandlers(deps);
  await assert.rejects(handlers.list(new Request("https://example.test/api/admin/notifications", {
    headers: { authorization: "Bearer secret" },
  }), env), /D1 unavailable/);
});

test("processing without SMTP marks queued deliveries failed and closes campaign", async () => {
  const updates: string[] = [];
  const env: Env = {
    ADMIN_API_TOKEN: "secret",
    DB: database((query, operation) => {
      if (operation === "first" && query.includes("FROM message_campaigns")) {
        return { id: "campaign-1", subject: "Alert", metadata_json: "{}", status: "queued", created_at: "now" };
      }
      if (operation === "all" && query.includes("GROUP BY status")) {
        return { results: [{ status: "failed", count: 2 }] };
      }
      if (operation === "run") { updates.push(query); return {}; }
      return operation === "all" ? { results: [] } : null;
    }),
  };
  const handlers = createCampaignHandlers(deps);
  const response = await handlers.process(new Request("https://example.test/api/admin/notifications/campaign-1/process", {
    method: "POST", headers: { authorization: "Bearer secret", "content-type": "application/json" }, body: "{}",
  }), env);
  assert.equal(response.status, 200);
  const body = await response.json() as { status: string; reason: string; progress: { failed: number } };
  assert.equal(body.status, "failed");
  assert.equal(body.reason, "smtp_not_configured");
  assert.equal(body.progress.failed, 2);
  assert.ok(updates.some((query) => query.includes("UPDATE message_deliveries")));
  assert.ok(updates.some((query) => query.includes("UPDATE message_campaigns")));
});
