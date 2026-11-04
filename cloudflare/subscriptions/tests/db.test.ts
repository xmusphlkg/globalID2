import assert from "node:assert/strict";
import test from "node:test";

import { insertEmailDelivery, reserveConfirmationEmail, updateEmailDelivery, type D1Database } from "../src/lib/db.ts";

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

test("optional logging can fail, while delivery ledger updates fail closed", async () => {
  const failure = new Error("D1 unavailable");
  const { db } = recordingDb(async () => { throw failure; });
  await assert.doesNotReject(insertEmailDelivery(db, {
    deliveryId: "delivery-1", subscriberId: "subscriber-1", contactId: "contact-1",
    subscriptionId: null, recipient: "reader@example.test", subject: "Update",
    deliveryType: "confirmation", provider: "smtp", status: "queued", attempts: 1,
    source: "website", now: "2026-08-05T00:00:00.000Z",
  }));
  await assert.rejects(updateEmailDelivery(db, {
    deliveryId: "delivery-1", status: "failed", errorMessage: failure.message,
  }));
});

test("confirmation reservation fails closed when the delivery ledger is unavailable", async () => {
  const { db } = recordingDb();
  const failing: D1Database = { prepare(query) {
    const statement = db.prepare(query);
    return { ...statement, bind() { return this; }, async first<T>() { throw new Error("D1 unavailable"); } };
  } };
  await assert.rejects(reserveConfirmationEmail(failing, {
    deliveryId: "delivery-1", subscriberId: "subscriber-1", contactId: "contact-1", subscriptionId: null,
    recipient: "reader@example.test", subject: "Confirm", source: "website", metadata: {},
    now: "2026-08-05T00:00:00.000Z", cutoff: "2026-08-04T23:50:00.000Z", limit: 2,
  }), /D1 unavailable/);
});
