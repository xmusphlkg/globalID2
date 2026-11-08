import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { createSituationAlertHandlers, maintainSituationAlerts, processSituationAlertOutbox } from "../src/handlers/situation-alerts.ts";
import { parseOfficialNoticeAlert } from "../src/lib/official-notice-alert.ts";
import type { Env } from "../src/types.ts";

const notice = {
  schema_version: "official-notice-alert.v1", idempotency_key: "official-notice:a1:r1",
  release_id: "release-1", public_url: "https://globalinfectiousdisease.com/situation/",
  alert_id: "a1", notice_id: "who-don-1", outbreak_id: "OB-1", notice_status: "new", published_date: "2026-11-04",
  source_url: "https://who.int/example", title: "Influenza notice",
  summary_en: "Published official information.", summary_zh: "已发布的官方信息。",
  countries: ["CN"], diseases: ["influenza"], review_verdict: "accept", review_mode: "cross_model", publication_status: "published",
};

function fixture() {
  const sqlite = new DatabaseSync(":memory:");
  const dir = new URL("../migrations/", import.meta.url);
  for (const name of readdirSync(dir).filter(name => name.endsWith(".sql")).sort()) {
    sqlite.exec(readFileSync(new URL(name, dir), "utf8"));
  }
  const db = {
    prepare(query: string) {
      let values: unknown[] = [];
      const statement = {
        bind(...params: unknown[]) { values = params; return statement; },
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
  function add(id: string) {
    const now = "2026-01-01T00:00:00Z";
    sqlite.prepare("INSERT INTO subscribers (id, locale, timezone, status, created_at, updated_at) VALUES (?, 'en', 'UTC', 'active', ?, ?)")
      .run(`sub-${id}`, now, now);
    sqlite.prepare("INSERT INTO subscriber_contacts (id, subscriber_id, channel, address, address_hash, status, created_at, updated_at) VALUES (?, ?, 'email', ?, ?, 'active', ?, ?)")
      .run(`contact-${id}`, `sub-${id}`, `${id}@example.test`, `hash-${id}`, now, now);
    sqlite.prepare("INSERT INTO subscriptions (id, subscriber_id, contact_id, list_id, status, frequency, created_at, updated_at) VALUES (?, ?, ?, 'list_alerts', 'active', 'instant', ?, ?)")
      .run(`subscription-${id}`, `sub-${id}`, `contact-${id}`, now, now);
  }
  return { sqlite, add, env: { DELIVERY_MODE: "full", DB: db, PUBLIC_BASE_URL: "https://worker.example.test", SITUATION_ALERT_INGEST_TOKEN: "secret" } as unknown as Env };
}

function deps(send: () => Promise<void>) {
  return {
    async createSignedToken() { return "signed"; },
    smtpConfig() { return { host: "invalid", port: 587, username: "x", password: "x", fromEmail: "x@example.test", fromName: "x", useTls: true }; },
    async sendSmtpEmail() { await send(); return { providerMessageId: "provider-1" }; },
  };
}
function req(payload: unknown = notice) {
  return new Request("https://worker.example.test/api/internal/situation-alerts", { method: "POST", headers: {
    authorization: "Bearer secret", "content-type": "application/json",
  }, body: JSON.stringify(payload) });
}

test("official contract requires published reviewed content without fabricating statistical data", () => {
  const parsed = parseOfficialNoticeAlert(notice);
  assert.equal(parsed.alert_id, "a1");
  assert.throws(() => parseOfficialNoticeAlert({ ...notice, review_verdict: "revise" }));
  assert.throws(() => parseOfficialNoticeAlert({ ...notice, publication_status: "draft" }));
  assert.throws(() => parseOfficialNoticeAlert({ ...notice, source_url: "http://who.int/example" }));
  assert.throws(() => parseOfficialNoticeAlert({ ...notice, idempotency_key: "official-notice:other:r1" }));
});

test("replayed accepted notice never expands audience after first fanout", async () => {
  const { sqlite, add, env } = fixture();
  add("first");
  const handlers = createSituationAlertHandlers(deps(async () => {}));
  assert.equal((await handlers.ingest(req(), env)).status, 202);
  add("later");
  assert.equal((await handlers.ingest(req(), env)).status, 200);
  assert.equal(sqlite.prepare("SELECT count(*) AS n FROM situation_alert_deliveries").get()?.n, 1);
  sqlite.close();
});

test("recovering a partially fanned-out event excludes later confirmations", async () => {
  const { sqlite, add, env } = fixture();
  add("first");
  const handlers = createSituationAlertHandlers(deps(async () => {}));
  await handlers.ingest(req(), env);
  sqlite.exec("UPDATE situation_alert_events SET status = 'received'");
  add("later");
  sqlite.exec("UPDATE subscriptions SET confirmed_at = '9999-01-01T00:00:00.000Z' WHERE id = 'subscription-later'");
  assert.equal((await handlers.ingest(req(), env)).status, 200);
  const rows = sqlite.prepare("SELECT subscription_id FROM situation_alert_deliveries").all();
  assert.deepEqual(rows.map(row => row.subscription_id), ["subscription-first"]);
  sqlite.close();
});

test("temporary storage failure during recovery does not permanently fail a valid event", async () => {
  const { sqlite, add, env } = fixture();
  add("first");
  await createSituationAlertHandlers(deps(async () => {})).ingest(req(), env);
  sqlite.exec("UPDATE situation_alert_events SET status = 'received'");
  const original = env.DB.prepare.bind(env.DB);
  const failing = { ...env.DB, prepare(query: string) {
    if (query.includes("INSERT INTO situation_alert_deliveries")) throw new Error("temporary D1 failure");
    return original(query);
  } };
  await assert.rejects(maintainSituationAlerts({ ...env, DB: failing }), /temporary D1 failure/);
  assert.equal(sqlite.prepare("SELECT status FROM situation_alert_events").get()?.status, "received");
  sqlite.close();
});

test("retention keeps old mail ledgers while an alert delivery still refers to them", async () => {
  const { sqlite, add, env } = fixture(); add("first");
  await createSituationAlertHandlers(deps(async () => {})).ingest(req(), env);
  sqlite.exec(`PRAGMA foreign_keys = ON;
    INSERT INTO transactional_email_deliveries (id, subscriber_id, contact_id, subscription_id, delivery_type, recipient, subject, status, created_at, updated_at)
    VALUES ('old-transaction', 'sub-first', 'contact-first', 'subscription-first', 'verified_situation_alert', 'first@example.test', 'Alert', 'unknown', '2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z');
    UPDATE situation_alert_deliveries SET status = 'unknown', transaction_delivery_id = 'old-transaction';
    UPDATE situation_alert_events SET status = 'failed', completed_at = '9999-01-01T00:00:00Z'`);
  await maintainSituationAlerts(env);
  assert.equal(sqlite.prepare("SELECT count(*) AS n FROM transactional_email_deliveries WHERE id='old-transaction'").get()?.n, 1);
  sqlite.close();
});

test("ambiguous SMTP outcome is unknown and is not sent again", async () => {
  const { sqlite, add, env } = fixture(); add("first");
  let sends = 0;
  const dep = deps(async () => { sends++; throw new Error("connection_reset"); });
  await createSituationAlertHandlers(dep).ingest(req(), env);
  await processSituationAlertOutbox(env, dep);
  await processSituationAlertOutbox(env, dep);
  assert.equal(sends, 1);
  assert.equal(sqlite.prepare("SELECT status FROM situation_alert_deliveries").get()?.status, "unknown");
  assert.equal(sqlite.prepare("SELECT status FROM transactional_email_deliveries").get()?.status, "unknown");
  sqlite.close();
});

test("concurrent consumers send a claimed notice once", async () => {
  const { sqlite, add, env } = fixture(); add("first");
  let sends = 0;
  let release!: () => void;
  let entered!: () => void;
  const barrier = new Promise<void>(resolve => { release = resolve; });
  const started = new Promise<void>(resolve => { entered = resolve; });
  const dep = deps(async () => { sends++; entered(); await barrier; });
  await createSituationAlertHandlers(dep).ingest(req(), env);
  const first = processSituationAlertOutbox(env, dep);
  await started;
  await processSituationAlertOutbox(env, dep);
  release();
  await first;
  assert.equal(sends, 1);
  assert.equal(sqlite.prepare("SELECT status FROM situation_alert_deliveries").get()?.status, "sent");
  sqlite.close();
});

test("expired sending lease remains unknown rather than being retried", async () => {
  const { sqlite, add, env } = fixture(); add("first");
  let sends = 0;
  const dep = deps(async () => { sends++; });
  await createSituationAlertHandlers(dep).ingest(req(), env);
  sqlite.exec(`INSERT INTO transactional_email_deliveries (id, subscriber_id, contact_id, subscription_id, delivery_type, recipient, subject, status, created_at, updated_at)
    VALUES ('transaction', 'sub-first', 'contact-first', 'subscription-first', 'verified_situation_alert', 'first@example.test', 'Alert', 'queued', '2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z');
    UPDATE situation_alert_deliveries SET status = 'sending', transaction_delivery_id = 'transaction', claim_token = 'old', claim_expires_at = '2000-01-01T00:00:00Z'`);
  await processSituationAlertOutbox(env, dep);
  assert.equal(sends, 0);
  assert.equal(sqlite.prepare("SELECT status FROM situation_alert_deliveries").get()?.status, "unknown");
  sqlite.close();
});

test("expired claim before creating a mail ledger is safely retried", async () => {
  const { sqlite, add, env } = fixture(); add("first");
  let sends = 0;
  const dep = deps(async () => { sends++; });
  await createSituationAlertHandlers(dep).ingest(req(), env);
  sqlite.exec("UPDATE situation_alert_deliveries SET status = 'sending', claim_token = 'old', claim_expires_at = '2000-01-01T00:00:00Z'");
  await processSituationAlertOutbox(env, dep);
  assert.equal(sends, 1);
  assert.equal(sqlite.prepare("SELECT status FROM situation_alert_deliveries").get()?.status, "sent");
  sqlite.close();
});

test("permanent SMTP rejection records dead letter without null next_attempt_at", async () => {
  const { sqlite, add, env } = fixture(); add("first");
  const dep = deps(async () => { throw new Error("RCPT_TO_rejected:550"); });
  await createSituationAlertHandlers(dep).ingest(req(), env);
  await processSituationAlertOutbox(env, dep);
  const row = sqlite.prepare("SELECT status, next_attempt_at FROM situation_alert_deliveries").get();
  assert.equal(row?.status, "dead_letter");
  assert.ok(row?.next_attempt_at);
  sqlite.close();
});

test('pilot alert fanout is isolated from general readers even after release', async () => {
  const { sqlite, add, env } = fixture();
  add('admin'); add('reader');
  env.DELIVERY_MODE = 'pilot'; env.PILOT_RECIPIENTS = 'admin@example.test';
  const recipients: string[] = [];
  const dependencies = { ...deps(async () => {}), async sendSmtpEmail(_config: unknown, message: { to: string }) { recipients.push(message.to); return { providerMessageId: 'id' }; } };
  const handlers = createSituationAlertHandlers(dependencies);
  await assert.rejects(handlers.ingest(req(notice), env), /alert_delivery_mode_mismatch/);
  const pilotNotice = { ...notice, idempotency_key: 'pilot:official-notice:a1:r1' };
  assert.equal((await handlers.ingest(req(pilotNotice), env)).status, 202);
  env.DELIVERY_MODE = 'full';
  await processSituationAlertOutbox(env, dependencies);
  assert.deepEqual(recipients, ['admin@example.test']);
  await assert.rejects(handlers.ingest(req(pilotNotice), env), /alert_delivery_mode_mismatch/);
  sqlite.close();
});
