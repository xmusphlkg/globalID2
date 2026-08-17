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

test("manual campaigns cannot bypass verified Situation ingestion for the alerts list", async () => {
  let queried = false;
  const env: Env = {
    ADMIN_API_TOKEN: "secret",
    DB: database(() => { queried = true; return null; }),
  };
  const handlers = createCampaignHandlers(deps);
  const response = await handlers.create(new Request("https://example.test/api/admin/notifications", {
    method: "POST",
    headers: { authorization: "Bearer secret", "content-type": "application/json" },
    body: JSON.stringify({
      list_codes: ["alerts"],
      subject: "Unverified alert",
      markdown: "This must not reach priority-alert subscribers.",
    }),
  }), env);
  assert.equal(response.status, 422);
  assert.deepEqual(await response.json(), { error: "verified_situation_alert_endpoint_required" });
  assert.equal(queried, false);
});

test("Research Radar campaigns honor subscriber topic and study preferences", async () => {
  const calls: Array<{ query: string; values: unknown[] }> = [];
  const db: D1Database = { prepare(query) { const call = { query, values: [] as unknown[] }; calls.push(call); const statement = {
    bind(...values: unknown[]) { call.values = values; return statement; },
    async first<T>() {
      if (query.includes("SELECT id FROM subscription_lists")) return { id: "list-research" } as T;
      if (query.includes("FROM message_campaigns")) return {
        id: "campaign-1", subject: "Research update", status: "sent", created_at: "2026-08-17T00:00:00Z",
        metadata_json: "{}",
      } as T;
      return null;
    },
    async all<T>() { return { results: [] as T[] }; },
    async run() { return {}; },
  }; return statement; } };
  const env: Env = { ADMIN_API_TOKEN: "secret", DB: db };
  const handlers = createCampaignHandlers(deps);
  const response = await handlers.create(new Request("https://example.test/api/admin/notifications", {
    method: "POST",
    headers: { authorization: "Bearer secret", "content-type": "application/json" },
    body: JSON.stringify({
      list_codes: ["research_digest"],
      subject: "Research update",
      markdown: "Published evidence update.",
      research_topics: ["Vaccination", "Surveillance"],
      study_types: ["Systematic-Review"],
      peer_review_statuses: ["Peer-Reviewed"],
      frequency: "weekly",
    }),
  }), env);
  assert.equal(response.status, 201);
  const responseBody = await response.json() as Record<string, unknown>;
  assert.equal(JSON.stringify(responseBody).includes("email"), false);
  assert.equal(JSON.stringify(responseBody).includes("deliveries"), false);
  const audienceCall = calls.find((call) => call.query.includes("FROM subscriptions s") && call.query.includes("GROUP BY s.contact_id"));
  assert.ok(audienceCall);
  assert.match(audienceCall.query, /subscription_filters/);
  assert.ok(audienceCall.values.includes("research_topic"));
  assert.ok(audienceCall.values.includes("vaccination"));
  assert.ok(audienceCall.values.includes("surveillance"));
  assert.ok(audienceCall.values.includes("study_type"));
  assert.ok(audienceCall.values.includes("systematic-review"));
  assert.ok(audienceCall.values.includes("peer_review_status"));
  assert.ok(audienceCall.values.includes("peer-reviewed"));
  assert.match(audienceCall.query, /s\.frequency = \?/);
  assert.ok(audienceCall.values.includes("weekly"));
  const insertCampaign = calls.find((call) => call.query.includes("INSERT INTO message_campaigns"));
  assert.ok(insertCampaign);
  assert.match(String(insertCampaign.values[3]), /"audience_filters"/);
});

test("campaign creation fails closed when the requested list does not exist", async () => {
  const queries: string[] = [];
  const env: Env = {
    ADMIN_API_TOKEN: "secret",
    DB: database((query, operation) => {
      queries.push(query);
      return operation === "all" ? { results: [] } : null;
    }),
  };
  const response = await createCampaignHandlers(deps).create(new Request("https://example.test/api/admin/notifications", {
    method: "POST",
    headers: { authorization: "Bearer secret", "content-type": "application/json" },
    body: JSON.stringify({ list_codes: ["missing-list"], subject: "Update", markdown: "Body" }),
  }), env);
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "notification_list_not_found" });
  assert.equal(queries.some((query) => query.includes("ORDER BY sort_order")), false);
});

test("idempotent campaign replay returns the original campaign without rebuilding its audience", async () => {
  let audienceQueries = 0;
  const metadata = JSON.stringify({
    content_fingerprint: await (async () => {
      const { campaignContentFingerprint } = await import("../src/lib/campaign.ts");
      return campaignContentFingerprint({
        audience_filters: [],
        contents: { en: { subject: "Weekly brief", markdown: "Cited source." } },
        default_locale: "en",
        frequency: "weekly",
        list_codes: ["research_digest"],
        max_recipients: 10000,
        source_locale: "en",
        source_ref: "https://globalinfectiousdisease.com/research/weekly/2026-W33/",
        target_locales: ["en"],
      });
    })(),
    default_locale: "en",
    contents: { en: { subject: "Weekly brief", markdown: "Cited source." } },
  });
  const env: Env = {
    ADMIN_API_TOKEN: "secret",
    DB: database((query, operation) => {
      if (operation === "first" && query.includes("SELECT id FROM subscription_lists")) return { id: "list-research" };
      if (operation === "first" && query.includes("content_ref = ?")) {
        return { id: "campaign-original", subject: "Weekly brief", status: "queued", created_at: "now", metadata_json: metadata };
      }
      if (operation === "first" && query.includes("WHERE id = ?")) {
        return { id: "campaign-original", subject: "Weekly brief", status: "queued", created_at: "now", metadata_json: metadata };
      }
      if (operation === "all" && query.includes("FROM subscriptions s")) audienceQueries += 1;
      return operation === "all" ? { results: [] } : null;
    }),
  };
  const response = await createCampaignHandlers(deps).create(new Request("https://example.test/api/admin/notifications", {
    method: "POST",
    headers: { authorization: "Bearer secret", "content-type": "application/json" },
    body: JSON.stringify({
      idempotency_key: "research-digest:2026-W33:r1",
      list_codes: ["research_digest"],
      frequency: "weekly",
      source_ref: "https://globalinfectiousdisease.com/research/weekly/2026-W33/",
      subject: "Weekly brief",
      markdown: "Cited source.",
    }),
  }), env);
  assert.equal(response.status, 200);
  assert.equal((await response.json() as { duplicate: boolean }).duplicate, true);
  assert.equal(audienceQueries, 0);
});

test("an idempotency key cannot be reused for changed campaign content", async () => {
  const env: Env = {
    ADMIN_API_TOKEN: "secret",
    DB: database((query, operation) => {
      if (operation === "first" && query.includes("SELECT id FROM subscription_lists")) return { id: "list-research" };
      if (operation === "first" && query.includes("content_ref = ?")) {
        return { id: "campaign-original", subject: "Old", status: "queued", created_at: "now",
          metadata_json: JSON.stringify({ content_fingerprint: "different" }) };
      }
      return operation === "all" ? { results: [] } : null;
    }),
  };
  const response = await createCampaignHandlers(deps).create(new Request("https://example.test/api/admin/notifications", {
    method: "POST",
    headers: { authorization: "Bearer secret", "content-type": "application/json" },
    body: JSON.stringify({
      idempotency_key: "research-digest:2026-W33:r1",
      list_codes: ["research_digest"], subject: "Changed", markdown: "Changed body.",
    }),
  }), env);
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { error: "notification_idempotency_conflict" });
});
