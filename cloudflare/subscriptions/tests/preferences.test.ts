import assert from "node:assert/strict";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import { readFileSync, readdirSync } from "node:fs";
import { createPreferenceHandlers } from "../src/handlers/preferences.ts";
import { sha256Hex, signSubscriptionToken, verifySubscriptionToken } from "../src/lib/security.ts";
import type { Env } from "../src/types.ts";

async function fixture(options: { gate?: Promise<void>; onSend?: () => void } = {}) {
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
    smtpConfig: () => ({ host: "example.test", port: 587, username: "x", password: "x", fromEmail: "x@example.test", fromName: "GIDS", useTls: true }),
    sendSmtpEmail: async () => { deliveries += 1; options.onSend?.(); await options.gate; return { providerMessageId: "provider-id" }; },
  });
  return { sqlite, env, handlers, secret,
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

test("stale preference form cannot overwrite a newer save", async () => {
  const f = await fixture();
  const token = await f.token();
  const oldRevision = f.revision();
  await f.handlers.post(post({ token, action: "save", country: "CN", revision: oldRevision }), f.env);
  await assert.rejects(f.handlers.post(post({ token, action: "save", revision: oldRevision }), f.env), /subscription_changed_retry/);
  assert.equal(f.sqlite.prepare("SELECT filter_value FROM subscription_filters WHERE subscription_id='sub'").get()?.filter_value, "CN");
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
