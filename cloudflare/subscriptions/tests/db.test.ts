import assert from "node:assert/strict";
import test from "node:test";

import { insertEmailDelivery, updateEmailDelivery, type D1Database } from "../src/lib/db.ts";

function recordingDb(run: () => Promise<unknown> = async () => ({})) {
  const calls: Array<{ query: string; values: unknown[] }> = [];
  const db: D1Database = {
    prepare(query) {
      const call = { query, values: [] as unknown[] };
      calls.push(call);
      const statement = {
        bind(...values: unknown[]) { call.values = values; return statement; },
        async first<T>() { return null as T | null; },
        async all<T>() { return { results: [] as T[] }; },
        run,
      };
      return statement;
    },
  };
  return { db, calls };
}

test("writes delivery metadata through the isolated D1 boundary", async () => {
  const { db, calls } = recordingDb();
  await insertEmailDelivery(db, {
    deliveryId: "delivery-1", subscriberId: "subscriber-1", contactId: "contact-1",
    subscriptionId: null, recipient: "reader@example.test", subject: "Update",
    deliveryType: "confirmation", provider: "smtp", status: "queued", attempts: 1,
    source: "website", metadata: { lists: ["reports"] }, now: "2026-08-05T00:00:00.000Z",
  });
  assert.equal(calls.length, 1);
  assert.match(calls[0].query, /INSERT INTO transactional_email_deliveries/);
  assert.equal(calls[0].values[0], "delivery-1");
  assert.equal(calls[0].values[13], JSON.stringify({ lists: ["reports"] }));
});

test("delivery logging failures remain non-blocking", async () => {
  const failure = new Error("D1 unavailable");
  const { db } = recordingDb(async () => { throw failure; });
  await assert.doesNotReject(insertEmailDelivery(db, {
    deliveryId: "delivery-1", subscriberId: "subscriber-1", contactId: "contact-1",
    subscriptionId: null, recipient: "reader@example.test", subject: "Update",
    deliveryType: "confirmation", provider: "smtp", status: "queued", attempts: 1,
    source: "website", now: "2026-08-05T00:00:00.000Z",
  }));
  await assert.doesNotReject(updateEmailDelivery(db, {
    deliveryId: "delivery-1", status: "failed", errorMessage: failure.message,
  }));
});
