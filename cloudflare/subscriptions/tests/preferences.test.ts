import assert from "node:assert/strict";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import { readFileSync, readdirSync } from "node:fs";
import { createPreferenceHandlers } from "../src/handlers/preferences.ts";
import { applyDeliveryFeedback } from "../src/lib/delivery-feedback.ts";
import type { D1Database } from "../src/lib/db.ts";
import { sha256Hex, signSubscriptionToken, verifySubscriptionToken } from "../src/lib/security.ts";
import type { Env } from "../src/types.ts";

async function fixture(options: { gate?: Promise<void>; onSend?: () => void; sendError?: string; noConfig?: boolean } = {}) {
  const sqlite = new DatabaseSync(":memory:");
  const migrations = new URL("../migrations/", import.meta.url);
  for (const file of readdirSync(migrations).filter(name => name.endsWith(".sql")).sort()) {
    sqlite.exec(readFileSync(new URL(file, migrations), "utf8"));
  }
  const db = {
    prepare(query: string) {
      let values: unknown[] = [];
      const statement = {
        bind(...args: unknown[]) { values = args; return statement; },
        async first() { return sqlite.prepare(query).get(...values as never[]) || null; },
        async all() { return { results: sqlite.prepare(query).all(...values as never[]) }; },
        async run() { const result = sqlite.prepare(query).run(...values as never[]); return { meta: { changes: Number(result.changes) } }; },
      };
      return statement;
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      sqlite.exec("BEGIN");
      try { const results = []; for (const stmt of statements) results.push(await stmt.run()); sqlite.exec("COMMIT"); return results; }
      catch (error) { sqlite.exec("ROLLBACK"); throw error; }
    },
  };
  const secret = "local-test-signing-secret";
  const env = { DELIVERY_MODE: "full", DB: db, TOKEN_SIGNING_SECRET: secret, PUBLIC_BASE_URL: "https://example.test" } as unknown as Env;
  const now = new Date().toISOString();
  const hash = await sha256Hex(`${secret}:email:reader@example.test`);
  sqlite.prepare("INSERT INTO subscribers (id,locale,timezone,status,created_at,updated_at) VALUES ('user','en','UTC','active',?,?)").run(now, now);
  sqlite.prepare("INSERT INTO subscriber_contacts (id,subscriber_id,channel,address,address_hash,status,created_at,updated_at) VALUES ('contact','user','email','reader@example.test',?,'active',?,?)").run(hash, now, now);
  sqlite.prepare("INSERT INTO subscriptions (id,subscriber_id,contact_id,list_id,status,frequency,created_at,updated_at) VALUES ('sub','user','contact','list_reports','active','weekly',?,?)").run(now, now);
  sqlite.exec("UPDATE subscriptions SET management_generation='initial-generation' WHERE id='sub'");
  sqlite.prepare("INSERT INTO subscription_filter_options (id,filter_type,filter_value,label_en,is_public,created_at,updated_at) VALUES ('cn','country','CN','China',1,?,?)").run(now, now);
  let deliveries = 0;
  const handlers = createPreferenceHandlers({
    createSignedToken: async (_env, purpose, id, ttl) => signSubscriptionToken(secret, purpose, id, ttl, Date.now(),
      String(sqlite.prepare("SELECT management_generation FROM subscriptions WHERE id=?").get(id)?.management_generation)),
    verifySignedToken: async (_env, token, purpose) => verifySubscriptionToken(secret, token, purpose),
    smtpConfig: () => options.noConfig ? null : ({ host: "example.test", port: 587, username: "x", password: "x", fromEmail: "x@example.test", fromName: "GIDS", useTls: true }),
    sendSmtpEmail: async () => { deliveries += 1; options.onSend?.(); await options.gate; if (options.sendError) throw new Error(options.sendError); return { providerMessageId: "provider-id" }; },
  });
  return { sqlite, db, env, handlers, secret,
    async token() { return signSubscriptionToken(secret, "manage", "sub", 1800, Date.now(),
      String(sqlite.prepare("SELECT management_generation FROM subscriptions WHERE id='sub'").get()?.management_generation)); },
    revision() { return String(sqlite.prepare("SELECT preference_revision FROM subscriptions WHERE id='sub'").get()?.preference_revision || ""); },
    get deliveries() { return deliveries; } };
}

function post(data: Record<string, string>) {
  return new Request("https://example.test/api/subscriptions/manage", {
    method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", "CF-Connecting-IP": "192.0.2.1" },
    body: new URLSearchParams(data),
  });
}

test("management tokens cannot be replaced by unsubscribe links, and preferences can be saved, paused and resumed", async () => {
  const f = await fixture();
  const { sqlite, env, handlers, secret } = f;
  const token = await f.token();
  const unsubscribe = await signSubscriptionToken(secret, "unsubscribe", "sub", 1800);
  const wrongPurpose = await handlers.get(new Request(`https://example.test/api/subscriptions/manage?token=${unsubscribe}`), env);
  assert.match(await wrongPurpose.text(), /invalid or expired/);
  const page = await handlers.get(new Request(`https://example.test/api/subscriptions/manage?token=${token}`), env);
  assert.equal(page.headers.get("referrer-policy"), "no-referrer");
  assert.match(await page.text(), /Manage subscription preferences/);
  assert.equal((await handlers.post(post({ token, action: "save", country: "CN", revision: f.revision() }), env)).status, 200);
  assert.equal(sqlite.prepare("SELECT filter_value FROM subscription_filters WHERE subscription_id='sub'").get()?.filter_value, "CN");
  await handlers.post(post({ token, action: "pause", revision: f.revision() }), env);
  assert.equal(sqlite.prepare("SELECT status FROM subscriptions WHERE id='sub'").get()?.status, "paused");
  await handlers.post(post({ token, action: "resume", revision: f.revision() }), env);
  assert.equal(sqlite.prepare("SELECT status FROM subscriptions WHERE id='sub'").get()?.status, "active");
  sqlite.exec("UPDATE subscriptions SET status='unsubscribed' WHERE id='sub'");
  await assert.rejects(handlers.post(post({ token, action: "resume", revision: f.revision() }), env), /management_link_no_longer_available/);
  sqlite.close();
});

test("management link requests return the same response before SMTP is complete", async () => {
  let release!: () => void;
  let entered!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const sending = new Promise<void>(resolve => { entered = resolve; });
  const f = await fixture({ gate, onSend: entered });
  const pending: Promise<unknown>[] = [];
  const ctx = { waitUntil(promise: Promise<unknown>) { pending.push(promise); } } as unknown as ExecutionContext;
  const response = await f.handlers.post(post({ email: "reader@example.test" }), f.env, ctx);
  assert.equal(response.status, 200);
  assert.equal(pending.length, 1);
  await sending;
  assert.equal(f.deliveries, 1);
  release();
  await Promise.all(pending);
  assert.equal(f.sqlite.prepare("SELECT status FROM transactional_email_deliveries WHERE delivery_type='subscription_management'").get()?.status, "sent");
  f.sqlite.close();
});

test("management link requests are non-enumerating, rate-limited, and never mail suppressed contacts", async () => {
  const f = await fixture();
  const unknown = await f.handlers.post(post({ email: "unknown@example.test" }), f.env);
  const known = await f.handlers.post(post({ email: "reader@example.test" }), f.env);
  assert.equal(await unknown.text(), await known.text());
  assert.equal(f.deliveries, 1);
  await f.handlers.post(post({ email: "reader@example.test" }), f.env);
  await f.handlers.post(post({ email: "reader@example.test" }), f.env);
  assert.equal(f.deliveries, 2);
  f.sqlite.exec("UPDATE subscriber_contacts SET status='suppressed'");
  await f.handlers.post(post({ email: "reader@example.test" }), f.env);
  assert.equal(f.deliveries, 2);
  f.sqlite.close();
});

test("management requests survive interruption and retry only known temporary SMTP rejections", async () => {
  const options = { sendError: "RCPT_TO_rejected:450" };
  const f = await fixture(options);
  await f.handlers.post(post({ email: "reader@example.test" }), f.env);
  assert.deepEqual({ ...f.sqlite.prepare("SELECT status,attempts,last_error FROM management_email_outbox").get() },
    { status: "retry", attempts: 1, last_error: "smtp_temporary_rejection" });
  assert.equal(f.deliveries, 1);
  options.sendError = "";
  f.sqlite.exec("UPDATE management_email_outbox SET next_attempt_at='2000-01-01T00:00:00.000Z'");
  await f.handlers.drainManagementOutbox(f.env);
  assert.equal(f.sqlite.prepare("SELECT status FROM management_email_outbox").get()?.status, "sent");
  assert.equal(f.deliveries, 2);
  await f.handlers.drainManagementOutbox(f.env);
  assert.equal(f.deliveries, 2);
  f.sqlite.close();
});

test("ambiguous or interrupted management sends are not retried, but feedback resolves them", async () => {
  const f = await fixture({ sendError: "smtp_timeout" });
  await f.handlers.post(post({ email: "reader@example.test" }), f.env);
  const row = f.sqlite.prepare("SELECT id,status,transaction_delivery_id FROM management_email_outbox").get()!;
  assert.equal(row.status, "unknown");
  await f.handlers.drainManagementOutbox(f.env);
  assert.equal(f.deliveries, 1);
  const now = new Date().toISOString();
  await applyDeliveryFeedback(f.db as unknown as D1Database, { provider: "smtp", providerEventId: "event-1",
    providerMessageId: "", correlationId: String(row.transaction_delivery_id), eventType: "delivered", errorCode: "", occurredAt: now }, now);
  assert.equal(f.sqlite.prepare("SELECT status FROM management_email_outbox WHERE id=?").get(row.id as string)?.status, "sent");
  f.sqlite.exec(`UPDATE management_email_outbox SET status='sending', claim_token='expired', claim_expires_at='2000-01-01T00:00:00.000Z'`);
  await f.handlers.drainManagementOutbox(f.env);
  assert.equal(f.sqlite.prepare("SELECT status FROM management_email_outbox").get()?.status, "unknown");
  assert.equal(f.deliveries, 1);
  f.sqlite.close();
});

test("provider feedback during an in-flight send wins over a late SMTP receipt", async () => {
  let release!: () => void;
  let entered!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const sending = new Promise<void>(resolve => { entered = resolve; });
  const f = await fixture({ gate, onSend: entered });
  const pending: Promise<unknown>[] = [];
  const ctx = { waitUntil(promise: Promise<unknown>) { pending.push(promise); } } as unknown as ExecutionContext;
  await f.handlers.post(post({ email: "reader@example.test" }), f.env, ctx);
  await sending;
  await f.handlers.drainManagementOutbox(f.env);
  assert.equal(f.deliveries, 1);
  const row = f.sqlite.prepare("SELECT transaction_delivery_id FROM management_email_outbox").get()!;
  const now = new Date().toISOString();
  await applyDeliveryFeedback(f.db as unknown as D1Database, { provider: "smtp", providerEventId: "event-in-flight",
    providerMessageId: "", correlationId: String(row.transaction_delivery_id), eventType: "delivered", errorCode: "", occurredAt: now }, now);
  release();
  await Promise.all(pending);
  assert.equal(f.sqlite.prepare("SELECT status FROM management_email_outbox").get()?.status, "sent");
  assert.equal(f.sqlite.prepare("SELECT status FROM transactional_email_deliveries").get()?.status, "delivered");
  f.sqlite.close();
});

test("permanent SMTP rejections do not requeue management mail", async () => {
  const f = await fixture({ sendError: "RCPT_TO_rejected:550" });
  await f.handlers.post(post({ email: "reader@example.test" }), f.env);
  assert.equal(f.sqlite.prepare("SELECT status FROM management_email_outbox").get()?.status, "failed");
  await f.handlers.drainManagementOutbox(f.env);
  assert.equal(f.deliveries, 1);
  f.sqlite.close();
});

test("management queue does not send after unsubscribe or suppression", async () => {
  const f = await fixture({ noConfig: true });
  await f.handlers.post(post({ email: "reader@example.test" }), f.env);
  assert.equal(f.sqlite.prepare("SELECT status FROM management_email_outbox").get()?.status, "retry");
  f.sqlite.exec("UPDATE subscriber_contacts SET status='suppressed'; UPDATE management_email_outbox SET next_attempt_at='2000-01-01T00:00:00.000Z'");
  await f.handlers.drainManagementOutbox(f.env);
  assert.equal(f.sqlite.prepare("SELECT status FROM management_email_outbox").get()?.status, "cancelled");
  assert.equal(f.deliveries, 0);
  f.sqlite.close();
});

test("stale preference form cannot overwrite a newer save", async () => {
  const f = await fixture();
  const token = await f.token();
  const oldRevision = f.revision();
  await f.handlers.post(post({ token, action: "save", country: "CN", revision: oldRevision }), f.env);
  await assert.rejects(f.handlers.post(post({ token, action: "save", revision: oldRevision }), f.env), /subscription_changed_retry/);
  assert.equal(f.sqlite.prepare("SELECT filter_value FROM subscription_filters WHERE subscription_id='sub'").get()?.filter_value, "CN");
  f.sqlite.close();
});

test("historical report frequency remains unchanged until its owner explicitly selects weekly", async () => {
  const f = await fixture();
  f.sqlite.exec(`UPDATE subscriptions SET frequency='instant' WHERE id='sub';
    INSERT INTO subscription_change_requests (id,subscription_id,subscriber_id,contact_id,locale,timezone,frequency,source,filters_json,consent_version,status,created_at,expires_at)
    VALUES ('old','sub','user','contact','en','UTC','instant','website','[]','v1','pending','2026-01-01','2030-01-01')`);
  const token = await f.token();
  const render = async () => (await f.handlers.get(new Request(`https://example.test/api/subscriptions/manage?token=${token}`), f.env)).text();
  const page = await render();
  assert.match(page, /Current frequency: Instant/);
  assert.match(page, /Automatic delivery does not support your current frequency/);
  assert.match(page, /<option value="">Keep current frequency<\/option><option value="weekly">Weekly<\/option>/);
  await f.handlers.post(post({ token, action: "save", country: "CN", revision: f.revision() }), f.env);
  assert.equal(f.sqlite.prepare("SELECT frequency FROM subscriptions WHERE id='sub'").get()?.frequency, "instant");
  assert.equal(f.sqlite.prepare("SELECT status FROM subscription_change_requests WHERE id='old'").get()?.status, "pending");
  await f.handlers.post(post({ token, action: "save", country: "CN", frequency: "weekly", revision: f.revision() }), f.env);
  assert.equal(f.sqlite.prepare("SELECT frequency FROM subscriptions WHERE id='sub'").get()?.frequency, "weekly");
  assert.equal(f.sqlite.prepare("SELECT status FROM subscription_change_requests WHERE id='old'").get()?.status, "invalidated");
  assert.equal(f.sqlite.prepare("SELECT filter_value FROM subscription_filters WHERE subscription_id='sub'").get()?.filter_value, "CN");
  assert.deepEqual(JSON.parse(String(f.sqlite.prepare("SELECT metadata_json FROM subscription_events WHERE event_type='preferences_save' ORDER BY rowid DESC").get()?.metadata_json)),
    { action: "save", frequency_from: "instant", frequency_to: "weekly" });
  f.sqlite.close();
});

test("historical weekly alerts offer instant in Chinese without changing on pause or resume", async () => {
  const f = await fixture();
  f.sqlite.exec("UPDATE subscribers SET locale='zh' WHERE id='user'; UPDATE subscriptions SET list_id='list_alerts',frequency='weekly' WHERE id='sub'");
  const token = await f.token();
  const page = await (await f.handlers.get(new Request(`https://example.test/api/subscriptions/manage?token=${token}`), f.env)).text();
  assert.match(page, /当前频率: 每周/);
  assert.match(page, /当前频率暂不支持自动投递/);
  assert.match(page, /<option value="">保留当前频率<\/option><option value="instant">即时<\/option>/);
  await f.handlers.post(post({ token, action: "pause", frequency: "instant", revision: f.revision() }), f.env);
  assert.deepEqual({ ...f.sqlite.prepare("SELECT status,frequency FROM subscriptions WHERE id='sub'").get() }, { status: "paused", frequency: "weekly" });
  await f.handlers.post(post({ token, action: "resume", frequency: "instant", revision: f.revision() }), f.env);
  assert.equal(f.sqlite.prepare("SELECT frequency FROM subscriptions WHERE id='sub'").get()?.frequency, "weekly");
  await f.handlers.post(post({ token, action: "save", frequency: "instant", revision: f.revision() }), f.env);
  assert.equal(f.sqlite.prepare("SELECT frequency FROM subscriptions WHERE id='sub'").get()?.frequency, "instant");
  f.sqlite.close();
});

test("unsupported or stale frequency changes cannot change the subscription", async () => {
  const f = await fixture();
  f.sqlite.exec("UPDATE subscriptions SET frequency='monthly' WHERE id='sub'");
  const token = await f.token();
  const stale = f.revision();
  await assert.rejects(f.handlers.post(post({ token, action: "save", frequency: "instant", revision: stale }), f.env), /invalid_frequency/);
  assert.equal(f.sqlite.prepare("SELECT frequency FROM subscriptions WHERE id='sub'").get()?.frequency, "monthly");
  await f.handlers.post(post({ token, action: "save", revision: stale }), f.env);
  await assert.rejects(f.handlers.post(post({ token, action: "save", frequency: "weekly", revision: stale }), f.env), /subscription_changed_retry/);
  assert.equal(f.sqlite.prepare("SELECT frequency FROM subscriptions WHERE id='sub'").get()?.frequency, "monthly");
  const duplicate = new URLSearchParams({ token, action: "save", frequency: "weekly", revision: f.revision() });
  duplicate.append("frequency", "weekly");
  await assert.rejects(f.handlers.post(new Request("https://example.test/api/subscriptions/manage", {
    method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: duplicate,
  }), f.env), /invalid_frequency/);
  f.sqlite.exec("UPDATE subscriptions SET status='unsubscribed' WHERE id='sub'");
  await assert.rejects(f.handlers.post(post({ token, action: "save", frequency: "weekly", revision: f.revision() }), f.env), /management_link_no_longer_available/);
  assert.equal(f.sqlite.prepare("SELECT frequency FROM subscriptions WHERE id='sub'").get()?.frequency, "monthly");
  f.sqlite.close();
});

test("unsubscribe all revokes every list, pending request and unsent queue for the email", async () => {
  const f = await fixture();
  const token = await f.token();
  f.sqlite.exec(`
    INSERT INTO subscriptions (id,subscriber_id,contact_id,list_id,status,frequency,created_at,updated_at,management_generation)
      VALUES ('sub-alert','user','contact','list_alerts','paused','instant','2026-01-01','2026-01-01','alert-generation');
    INSERT INTO subscription_change_requests (id,subscription_id,subscriber_id,contact_id,locale,timezone,frequency,source,filters_json,consent_version,status,created_at,expires_at)
      VALUES ('change','sub-alert','user','contact','en','UTC','instant','website','[]','v1','pending','2026-01-01','2030-01-01');
    INSERT INTO message_campaigns (id,list_id,trigger_type,subject,status,created_at)
      VALUES ('camp','list_reports','admin_notification','subject','queued','2026-01-01');
    INSERT INTO message_deliveries (id,campaign_id,subscription_id,contact_id,status,queued_at)
      VALUES ('queued','camp','sub','contact','queued','2026-01-01'), ('sent','camp','sub','contact','sent','2026-01-01');
    INSERT INTO situation_alert_events (id,idempotency_key,report_id,signal_id,verification_basis,payload_sha256,payload_json,status,received_at,updated_at)
      VALUES ('event','key','report','signal','analyst_review','hash','{}','queued','2026-01-01','2026-01-01');
    INSERT INTO situation_alert_deliveries (id,event_id,subscription_id,contact_id,status,next_attempt_at,queued_at,updated_at)
      VALUES ('alert','event','sub-alert','contact','retry','2026-01-01','2026-01-01','2026-01-01');
  `);
  const result = await f.handlers.post(post({ token, action: "unsubscribe_all", revision: f.revision() }), f.env);
  assert.equal(result.status, 200);
  const subscriptions = f.sqlite.prepare("SELECT status,management_generation FROM subscriptions WHERE contact_id='contact'").all();
  assert.ok(subscriptions.every(row => row.status === 'unsubscribed'));
  assert.ok(subscriptions.every(row => !['initial-generation', 'alert-generation'].includes(String(row.management_generation))));
  assert.equal(f.sqlite.prepare("SELECT status FROM subscription_change_requests WHERE id='change'").get()?.status, 'invalidated');
  assert.equal(f.sqlite.prepare("SELECT status FROM message_deliveries WHERE id='queued'").get()?.status, 'skipped');
  assert.equal(f.sqlite.prepare("SELECT status FROM message_deliveries WHERE id='sent'").get()?.status, 'sent');
  assert.equal(f.sqlite.prepare("SELECT status FROM situation_alert_deliveries WHERE id='alert'").get()?.status, 'skipped');
  assert.equal(f.sqlite.prepare("SELECT event_type FROM subscription_events WHERE subscription_id='sub'").get()?.event_type, 'preferences_unsubscribe_all');
  f.sqlite.exec("UPDATE subscriptions SET status='active' WHERE id='sub'");
  await assert.rejects(f.handlers.post(post({ token, action: "resume", revision: f.revision() }), f.env), /management_link_no_longer_available/);
  f.sqlite.close();
});
