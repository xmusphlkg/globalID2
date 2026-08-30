import assert from "node:assert/strict";
import test from "node:test";

import { applyDeliveryFeedback, normalizeDeliveryFeedback } from "../src/lib/delivery-feedback.ts";
import type { D1Database, D1PreparedStatement } from "../src/lib/db.ts";


test("normalizes only bounded identifiable delivery feedback", () => {
  const feedback = normalizeDeliveryFeedback({
    provider: " Approved-SMTP ", event_id: "event-1", correlation_id: "delivery-1",
    event_type: "BOUNCED", occurred_at: new Date().toISOString(), error_code: "hard_bounce",
  });
  assert.equal(feedback?.provider, "approved-smtp");
  assert.equal(feedback?.eventType, "bounced");
  assert.equal(normalizeDeliveryFeedback({ event_id: "event-1" }), null);
  assert.equal(normalizeDeliveryFeedback({
    provider: "smtp", event_id: "event-1", event_type: "opened",
    correlation_id: "delivery-1", occurred_at: new Date().toISOString(),
  }), null);
});

test("applies an idempotent bounce and suppresses the matched contact", async () => {
  const calls: Array<{ query: string; values: unknown[] }> = [];
  const db: D1Database = {
    prepare(query: string): D1PreparedStatement {
      const call = { query, values: [] as unknown[] };
      calls.push(call);
      const statement: D1PreparedStatement = {
        bind(...values: unknown[]) { call.values = values; return statement; },
        async first<T>() { return null as T | null; },
        async all<T>() { return { results: [] as T[] }; },
        async run() { return { meta: { changes: query.includes("subscriber_contacts") ? 1 : 2 } }; },
      };
      return statement;
    },
  };
  const feedback = normalizeDeliveryFeedback({
    provider: "smtp", event_id: "event-1", correlation_id: "delivery-1",
    event_type: "bounced", occurred_at: new Date().toISOString(), error_code: "hard_bounce",
  });
  assert.ok(feedback);
  const result = await applyDeliveryFeedback(db, feedback, "2026-08-27T10:00:00Z");
  assert.deepEqual(result, { duplicate: false, campaignRows: 2, transactionalRows: 2, suppressedContacts: 1 });
  assert.ok(calls.some((call) => call.query.includes("INSERT INTO email_delivery_events")));
  assert.ok(calls.some((call) => call.query.includes("status = 'suppressed'")));
  assert.ok(calls.some((call) => call.query.includes("SELECT contact_id FROM message_deliveries")));
  assert.ok(calls.at(-1)?.query.includes("INSERT INTO email_delivery_events"));
  assert.equal(JSON.stringify(calls).includes("reader@example"), false);
});

test("late non-terminal feedback cannot downgrade a terminal delivery", async () => {
  const updates: string[] = [];
  const db: D1Database = {
    prepare(query: string): D1PreparedStatement {
      const statement: D1PreparedStatement = {
        bind() { return statement; },
        async first<T>() { return null as T | null; },
        async all<T>() { return { results: [] as T[] }; },
        async run() { updates.push(query); return { meta: { changes: 0 } }; },
      };
      return statement;
    },
  };
  const feedback = normalizeDeliveryFeedback({
    provider: "smtp", event_id: "event-deferred", correlation_id: "delivery-1",
    event_type: "deferred", occurred_at: new Date().toISOString(),
  });
  assert.ok(feedback);
  await applyDeliveryFeedback(db, feedback);
  const deliveryUpdates = updates.filter((query) => query.startsWith("UPDATE"));
  assert.equal(deliveryUpdates.length, 2);
  assert.ok(deliveryUpdates.every((query) => query.includes("status NOT IN ('delivered', 'failed', 'skipped')")));
});

test("delivery feedback refreshes the affected parent campaign", async () => {
  const calls: Array<{ query: string; values: unknown[] }> = [];
  const db: D1Database = {
    prepare(query: string): D1PreparedStatement {
      const call = { query, values: [] as unknown[] };
      calls.push(call);
      const statement: D1PreparedStatement = {
        bind(...values: unknown[]) { call.values = values; return statement; },
        async first<T>() { return null as T | null; },
        async all<T>() {
          if (query.includes("SELECT DISTINCT campaign_id")) {
            return { results: [{ campaign_id: "campaign-1" }] as T[] };
          }
          if (query.includes("GROUP BY status")) {
            return { results: [{ status: "delivered", count: 1 }] as T[] };
          }
          return { results: [] as T[] };
        },
        async run() { return { meta: { changes: 1 } }; },
      };
      return statement;
    },
  };
  const feedback = normalizeDeliveryFeedback({
    provider: "smtp", event_id: "event-delivered", correlation_id: "delivery-1",
    event_type: "delivered", occurred_at: new Date().toISOString(),
  });
  assert.ok(feedback);
  await applyDeliveryFeedback(db, feedback, "2026-08-27T11:00:00Z");
  const parentUpdate = calls.find((call) => call.query.includes("UPDATE message_campaigns"));
  assert.deepEqual(parentUpdate?.values, [
    "sent", "2026-08-27T11:00:00Z", "2026-08-27T11:00:00Z", "campaign-1",
  ]);
});

test("duplicate provider events never update delivery state twice", async () => {
  let writes = 0;
  const db: D1Database = {
    prepare(): D1PreparedStatement {
      const statement: D1PreparedStatement = {
        bind() { return statement; },
        async first<T>() { return { id: "existing" } as T; },
        async all<T>() { return { results: [] as T[] }; },
        async run() { writes += 1; return {}; },
      };
      return statement;
    },
  };
  const feedback = normalizeDeliveryFeedback({
    provider: "smtp", event_id: "event-1", provider_message_id: "message-1",
    event_type: "delivered", occurred_at: new Date().toISOString(),
  });
  assert.ok(feedback);
  assert.equal((await applyDeliveryFeedback(db, feedback)).duplicate, true);
  assert.equal(writes, 0);
});
