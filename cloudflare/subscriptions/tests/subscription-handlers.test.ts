import assert from "node:assert/strict";
import test from "node:test";

import { listAudience, listSubscriptionOptions, subscriptionStats } from "../src/handlers/subscriptions.ts";
import type { D1Database } from "../src/lib/db.ts";
import { HttpError } from "../src/lib/http.ts";
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

test("subscription options expose Research Radar lists and filter dimensions", async () => {
  const env: Env = { DB: database((query, operation) => {
    if (operation !== "all") return null;
    if (query.includes("FROM subscription_lists")) return { results: [{
      code: "research_digest", name: "Research Radar digest", name_zh: "Research Radar 研究摘要",
      description: "Research updates", description_zh: "研究更新", default_frequency: "weekly",
    }] };
    if (query.includes("FROM subscription_filter_options")) return { results: [
      { filter_type: "research_topic", filter_value: "vaccination", label_en: "Vaccination", label_zh: "疫苗接种" },
      { filter_type: "study_type", filter_value: "systematic-review", label_en: "Systematic review", label_zh: "系统综述" },
      { filter_type: "peer_review_status", filter_value: "preprint", label_en: "Approved preprints", label_zh: "已审核预印本" },
    ] };
    return { results: [] };
  }) };
  const response = await listSubscriptionOptions(
    new Request("https://example.test/api/subscriptions/options"), env,
  );
  const body = await response.json() as {
    lists: Array<{ code: string }>;
    filters: Record<string, Array<{ value: string }>>;
  };
  assert.equal(body.lists[0].code, "research_digest");
  assert.deepEqual(body.filters.research_topic.map((item) => item.value), ["vaccination"]);
  assert.deepEqual(body.filters.study_type.map((item) => item.value), ["systematic-review"]);
  assert.deepEqual(body.filters.peer_review_status.map((item) => item.value), ["preprint"]);
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

test("audience handler applies Research Radar topic, study, and review-status preferences", async () => {
  const boundValues: unknown[][] = [];
  const queries: string[] = [];
  const db: D1Database = { prepare(query) { queries.push(query); const statement = {
    bind(...values: unknown[]) { boundValues.push(values); return statement; },
    async first<T>() { return { id: "list-research" } as T; },
    async all<T>() { return { results: [] as T[] }; },
    async run() { return null; },
  }; return statement; } };
  const env: Env = { ADMIN_API_TOKEN: "secret", DB: db };
  await listAudience(new Request("https://example.test/api/admin/audience", {
    method: "POST", headers: { authorization: "Bearer secret", "content-type": "application/json" },
    body: JSON.stringify({
      list_code: "research_digest",
      research_topic: "Vaccination",
      study_type: "Systematic-Review",
      peer_review_status: "Peer-Reviewed",
    }),
  }), env, {
    async readPayload(request) { return await request.json() as Record<string, unknown>; },
    async createSignedToken() { return "unused"; },
  });
  const audienceQuery = queries.find((query) => query.includes("FROM subscriptions s"));
  assert.ok(audienceQuery);
  assert.equal((audienceQuery.match(/subscription_filters/g) || []).length, 6);
  const audienceBinds = boundValues.at(-1) || [];
  assert.ok(audienceBinds.includes("research_topic"));
  assert.ok(audienceBinds.includes("vaccination"));
  assert.ok(audienceBinds.includes("study_type"));
  assert.ok(audienceBinds.includes("systematic-review"));
  assert.ok(audienceBinds.includes("peer_review_status"));
  assert.ok(audienceBinds.includes("peer-reviewed"));
});

test("audience export cannot bypass the verified Situation alert pipeline", async () => {
  let queried = false;
  const env: Env = {
    ADMIN_API_TOKEN: "secret",
    DB: database(() => { queried = true; return null; }),
  };
  await assert.rejects(
    listAudience(new Request("https://example.test/api/admin/audience", {
      method: "POST",
      headers: { authorization: "Bearer secret", "content-type": "application/json" },
      body: JSON.stringify({ list_code: "alerts" }),
    }), env, {
      async readPayload(request) { return await request.json() as Record<string, unknown>; },
      async createSignedToken() { return "unused"; },
    }),
    (error: unknown) => error instanceof HttpError && error.status === 422 &&
      error.message === "verified_situation_alert_endpoint_required",
  );
  assert.equal(queried, false);
});
