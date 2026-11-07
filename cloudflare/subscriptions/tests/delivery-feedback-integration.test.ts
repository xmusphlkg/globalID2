import assert from "node:assert/strict";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import { readdirSync, readFileSync } from "node:fs";
import { applyDeliveryFeedback } from "../src/lib/delivery-feedback.ts";
import { updateEmailDelivery } from "../src/lib/db.ts";
import type { D1Database } from "../src/lib/db.ts";

test("feedback settles an in-flight alert and never reverses a later hard bounce", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const migrations = new URL("../migrations/", import.meta.url);
  for (const file of readdirSync(migrations).filter(name => name.endsWith(".sql")).sort()) sqlite.exec(readFileSync(new URL(file, migrations), "utf8"));
  const db = { prepare(query: string) {
    let values: unknown[] = [];
    const statement = {
      bind(...args: unknown[]) { values = args; return statement; },
      async first() { return sqlite.prepare(query).get(...values as never[]) || null; },
      async all() { return { results: sqlite.prepare(query).all(...values as never[]) }; },
      async run() { const result = sqlite.prepare(query).run(...values as never[]); return { meta: { changes: Number(result.changes) } }; },
    };
    return statement;
  } } as unknown as D1Database;
  const now = new Date().toISOString();
  sqlite.exec(`INSERT INTO subscribers (id,status,created_at,updated_at) VALUES ('user','active','${now}','${now}');
    INSERT INTO subscriber_contacts (id,subscriber_id,channel,address,address_hash,status,created_at,updated_at) VALUES ('contact','user','email','a@example.invalid','hash','active','${now}','${now}');
    INSERT INTO subscriptions (id,subscriber_id,contact_id,list_id,status,frequency,created_at,updated_at) VALUES ('sub','user','contact','list_alerts','active','instant','${now}','${now}');
    INSERT INTO transactional_email_deliveries (id,subscriber_id,contact_id,subscription_id,delivery_type,recipient,subject,status,created_at,updated_at) VALUES ('transaction','user','contact','sub','verified_situation_alert','a@example.invalid','Alert','queued','${now}','${now}');
    INSERT INTO situation_alert_events (id,idempotency_key,report_id,signal_id,verification_basis,payload_sha256,payload_json,status,received_at,updated_at) VALUES ('event','key','report','signal','analyst_review','hash','{}','sending','${now}','${now}');
    INSERT INTO situation_alert_deliveries (id,event_id,subscription_id,contact_id,status,attempts,next_attempt_at,claim_token,claim_expires_at,transaction_delivery_id,queued_at,updated_at) VALUES ('delivery','event','sub','contact','sending',1,'${now}','claim','${now}','transaction','${now}','${now}');`);
  const feedback = { provider: "smtp", providerMessageId: "", correlationId: "transaction", errorCode: "", occurredAt: now };
  await applyDeliveryFeedback(db, { ...feedback, providerEventId: "delivered-1", eventType: "delivered" }, now);
  assert.equal(sqlite.prepare("SELECT status FROM situation_alert_deliveries WHERE id='delivery'").get()?.status, "sent");
  assert.equal(sqlite.prepare("SELECT status FROM situation_alert_events WHERE id='event'").get()?.status, "sent");
  await updateEmailDelivery(db, { deliveryId: "transaction", status: "sent", sentAt: now, providerMessageId: "provider-after-callback" });
  assert.equal(sqlite.prepare("SELECT status FROM transactional_email_deliveries WHERE id='transaction'").get()?.status, "delivered");
  await applyDeliveryFeedback(db, { ...feedback, providerEventId: "bounce-1", eventType: "bounced" }, now);
  assert.equal(sqlite.prepare("SELECT status FROM situation_alert_deliveries WHERE id='delivery'").get()?.status, "dead_letter");
  assert.equal(sqlite.prepare("SELECT status FROM subscriber_contacts WHERE id='contact'").get()?.status, "suppressed");
  await updateEmailDelivery(db, { deliveryId: "transaction", status: "sent", sentAt: now });
  assert.equal(sqlite.prepare("SELECT status FROM transactional_email_deliveries WHERE id='transaction'").get()?.status, "failed");
  await applyDeliveryFeedback(db, { ...feedback, providerEventId: "delivered-2", eventType: "delivered" }, now);
  assert.equal(sqlite.prepare("SELECT status FROM situation_alert_deliveries WHERE id='delivery'").get()?.status, "dead_letter");
  sqlite.close();
});
