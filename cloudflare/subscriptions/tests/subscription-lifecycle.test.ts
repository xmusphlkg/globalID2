import assert from 'node:assert/strict';
import test from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { createSubscriptionLifecycleHandlers } from '../src/handlers/subscription-lifecycle.ts';
import { signSubscriptionToken, verifySubscriptionToken } from '../src/lib/security.ts';
import { reserveConfirmationEmail } from '../src/lib/db.ts';
import type { Env } from '../src/types.ts';

function fixture(options: { send?: () => Promise<Record<string, string | boolean>> } = {}) {
  const sqlite = new DatabaseSync(':memory:');
  const dir = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(dir).filter(name => name.endsWith('.sql')).sort()) sqlite.exec(readFileSync(new URL(file, dir), 'utf8'));
  const DB = { prepare(query: string) {
    let values: unknown[] = [];
    const stmt = { bind(...v: unknown[]) { values = v; return stmt; },
      async first() { return sqlite.prepare(query).get(...values as never[]) || null; },
      async all() { return { results: sqlite.prepare(query).all(...values as never[]) }; },
      async run() { return { meta: { changes: Number(sqlite.prepare(query).run(...values as never[]).changes) } }; } };
    return stmt;
  }, async batch(statements: Array<{ run(): Promise<unknown> }>) {
    sqlite.exec('BEGIN');
    try { const result = []; for (const s of statements) result.push(await s.run()); sqlite.exec('COMMIT'); return result; }
    catch (error) { sqlite.exec('ROLLBACK'); throw error; }
  } };
  const env = { DELIVERY_MODE: "full", DB, TOKEN_SIGNING_SECRET: 'test-secret', PUBLIC_BASE_URL: 'https://example.test', DEBUG_RETURN_TOKENS: 'true',
    REPORT_DIGEST_ENABLED: 'true', RESEARCH_DIGEST_ENABLED: 'true', NOTICE_ALERTS_ENABLED: 'true' } as unknown as Env;
  const handlers = createSubscriptionLifecycleHandlers({
    async readPayload(req) { return await req.json() as Record<string, unknown>; },
    async verifyTurnstileIfConfigured() {}, async enforceSubmissionRateLimit() {},
    signingSecret() { return 'test-secret'; },
    async createSignedToken(_env, kind, id, ttl) { return signSubscriptionToken('test-secret', kind, id, ttl); },
    async verifySignedToken(_env, token, kind) { return verifySubscriptionToken('test-secret', token, kind); },
    async sendAndRecordConfirmationEmail() { return options.send ? await options.send() : { status: 'sent', provider: 'smtp' }; },
    async recordEvent() {}, async skipPendingSituationAlertsForSubscription() {},
  });
  const state = () => sqlite.prepare(`SELECT s.id, s.status, s.frequency, sub.locale, c.status AS contact_status
    FROM subscriptions s JOIN subscribers sub ON sub.id = s.subscriber_id JOIN subscriber_contacts c ON c.id = s.contact_id`).get();
  return { sqlite, env, handlers, state };
}
const make = (payload: unknown) => new Request('https://example.test/api/subscriptions', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) });
const payload = { email: 'reader@example.test', consent: true, locale: 'en', list_codes: ['reports'], frequency: 'weekly' };
async function submit(f: ReturnType<typeof fixture>, body = payload) {
  const response = await f.handlers.createSubscription(make(body), f.env);
  return await response.json() as { subscriptions: Array<{ id: string; confirm_url: string }> };
}
function req(url: string, method = 'GET') { return new Request(url, { method }); }

test('changes are staged, old confirmations survive resend, and confirmation is single-use', async () => {
  const f = fixture();
  const first = await submit(f);
  await f.handlers.confirmSubscription(req(first.subscriptions[0].confirm_url), f.env);
  assert.equal(f.state()?.status, 'pending');
  await f.handlers.confirmSubscription(req(first.subscriptions[0].confirm_url, 'POST'), f.env);
  assert.equal(f.state()?.status, 'active');
  const second = await submit(f, { ...payload, locale: 'zh' });
  const third = await submit(f, { ...payload, locale: 'en' });
  assert.equal(f.state()?.status, 'active');
  assert.equal(f.state()?.locale, 'en');
  await f.handlers.confirmSubscription(req(second.subscriptions[0].confirm_url, 'POST'), f.env);
  assert.equal(f.state()?.locale, 'zh');
  await assert.rejects(f.handlers.confirmSubscription(req(second.subscriptions[0].confirm_url, 'POST'), f.env), /invalid_or_expired_token/);
  await assert.rejects(f.handlers.confirmSubscription(req(third.subscriptions[0].confirm_url, 'POST'), f.env), /invalid_or_expired_token/);
  f.sqlite.close();
});

test('unsubscribe invalidates outstanding confirmations and GET does not modify state', async () => {
  const f = fixture();
  const first = await submit(f);
  await f.handlers.confirmSubscription(req(first.subscriptions[0].confirm_url, 'POST'), f.env);
  const pending = await submit(f, { ...payload, locale: 'zh' });
  const token = await signSubscriptionToken('test-secret', 'unsubscribe', first.subscriptions[0].id, 3600);
  const url = 'https://example.test/api/subscriptions/unsubscribe?token=' + token;
  await f.handlers.unsubscribe(req(url), f.env);
  assert.equal(f.state()?.status, 'active');
  await f.handlers.unsubscribe(req(url, 'POST'), f.env);
  assert.equal(f.state()?.status, 'unsubscribed');
  await assert.rejects(f.handlers.confirmSubscription(req(pending.subscriptions[0].confirm_url, 'POST'), f.env), /invalid_or_expired_token/);
  f.sqlite.close();
});

test('suppression blocks a previously issued confirmation', async () => {
  const f = fixture(); const pending = await submit(f);
  f.sqlite.exec("UPDATE subscriber_contacts SET status='suppressed'");
  await assert.rejects(f.handlers.confirmSubscription(req(pending.subscriptions[0].confirm_url, 'POST'), f.env), /invalid_or_expired_token/);
  assert.equal(f.state()?.contact_status, 'suppressed');
  f.sqlite.close();
});

test('legacy tokens cannot reactivate an unsubscribed subscription', async () => {
  const f = fixture(); const pending = await submit(f);
  const legacy = await signSubscriptionToken('test-secret', 'confirm', pending.subscriptions[0].id, 3600);
  const url = 'https://example.test/api/subscriptions/confirm?token=' + legacy;
  await f.handlers.confirmSubscription(req(url, 'POST'), f.env);
  const unsub = await signSubscriptionToken('test-secret', 'unsubscribe', pending.subscriptions[0].id, 3600);
  await f.handlers.unsubscribe(req('https://example.test/api/subscriptions/unsubscribe?token=' + unsub, 'POST'), f.env);
  await assert.rejects(f.handlers.confirmSubscription(req(url, 'POST'), f.env), /invalid_or_expired_token/);
  f.sqlite.close();
});

test('consent and list-specific delivery frequencies are validated before writes', async () => {
  const f = fixture();
  await assert.rejects(f.handlers.createSubscription(make({ ...payload, consent: false }), f.env), /consent_required/);
  await assert.rejects(f.handlers.createSubscription(make({ ...payload, list_codes: ['alerts'] }), f.env), /unsupported_frequency:alerts/);
  assert.equal(f.sqlite.prepare('SELECT COUNT(*) AS n FROM subscribers').get()?.n, 0);
  await submit(f, { ...payload, list_codes: ['reports', 'alerts'], list_frequencies: { reports: 'weekly', alerts: 'instant' } } as typeof payload);
  assert.equal(f.sqlite.prepare('SELECT COUNT(*) AS n FROM subscriptions').get()?.n, 2);
  f.sqlite.close();
});

test('confirmation reservations enforce the email limit under concurrent submissions', async () => {
  const f = fixture();
  const pending = await submit(f);
  const contact = f.sqlite.prepare('SELECT id, subscriber_id FROM subscriber_contacts').get() as { id: string; subscriber_id: string };
  const now = new Date().toISOString();
  const reservations = await Promise.all(Array.from({ length: 8 }, (_, i) => reserveConfirmationEmail(f.env.DB, {
    deliveryId: `attempt-${i}`, subscriberId: contact.subscriber_id, contactId: contact.id,
    subscriptionId: pending.subscriptions[0].id, recipient: 'reader@example.test', subject: 'Confirm', source: 'test', metadata: {},
    now, cutoff: new Date(Date.now() - 600_000).toISOString(), limit: 2,
  })));
  assert.equal(reservations.filter(Boolean).length, 2);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM transactional_email_deliveries WHERE status = 'queued'").get()?.n, 2);
  f.sqlite.close();
});

test('maintenance keeps an old pending record alive while its new request is valid', async () => {
  const f = fixture();
  await submit(f);
  f.sqlite.exec("UPDATE subscriptions SET created_at='2000-01-01T00:00:00.000Z'");
  await f.handlers.expirePending(f.env);
  assert.equal(f.state()?.status, 'pending');
  f.sqlite.exec("UPDATE subscription_change_requests SET expires_at='2000-01-01T00:00:00.000Z'");
  await f.handlers.expirePending(f.env);
  assert.equal(f.state()?.status, 'expired');
  f.sqlite.close();
});

test('confirmation and unsubscribe rotate management generations', async () => {
  const f = fixture();
  const first = await submit(f);
  const generation = () => f.sqlite.prepare('SELECT management_generation FROM subscriptions').get()?.management_generation;
  const initial = generation();
  await f.handlers.confirmSubscription(req(first.subscriptions[0].confirm_url, 'POST'), f.env);
  const confirmed = generation();
  assert.notEqual(confirmed, initial);
  const unsub = await signSubscriptionToken('test-secret', 'unsubscribe', first.subscriptions[0].id, 3600);
  await f.handlers.unsubscribe(req('https://example.test/api/subscriptions/unsubscribe?token=' + unsub, 'POST'), f.env);
  const afterUnsubscribe = generation();
  assert.notEqual(afterUnsubscribe, confirmed);
  const next = await submit(f);
  await f.handlers.confirmSubscription(req(next.subscriptions[0].confirm_url, 'POST'), f.env);
  assert.notEqual(generation(), afterUnsubscribe);
  f.sqlite.close();
});

test('confirmation outbox drains one job at a time and skips invalidated requests', async () => {
  let count = 0;
  const f = fixture({ async send() { count += 1; return { status: 'sent', provider: 'smtp' }; } });
  const first = await submit(f);
  await submit(f);
  assert.equal(count, 0);
  await f.handlers.drainConfirmationOutbox(f.env);
  assert.equal(count, 1);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM confirmation_email_outbox WHERE status='queued'").get()?.n, 1);
  const token = await signSubscriptionToken('test-secret', 'unsubscribe', first.subscriptions[0].id, 3600);
  await f.handlers.unsubscribe(req('https://example.test/api/subscriptions/unsubscribe?token=' + token, 'POST'), f.env);
  await f.handlers.drainConfirmationOutbox(f.env);
  assert.equal(count, 1);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM confirmation_email_outbox WHERE status='cancelled'").get()?.n, 1);
  f.sqlite.close();
});

test('confirmation outbox retries explicit temporary failures but does not retry ambiguous sends', async () => {
  let count = 0;
  const f = fixture({ async send() {
    count += 1;
    return count === 1 ? { status: 'failed', reason: 'smtp_delivery_failed', retryable: true }
      : { status: 'failed', reason: 'smtp_delivery_failed' };
  } });
  await submit(f);
  await f.handlers.drainConfirmationOutbox(f.env);
  assert.equal(f.sqlite.prepare('SELECT status FROM confirmation_email_outbox').get()?.status, 'retry');
  await f.handlers.drainConfirmationOutbox(f.env);
  assert.equal(count, 1);
  f.sqlite.exec("UPDATE confirmation_email_outbox SET next_attempt_at='2000-01-01T00:00:00.000Z'");
  await f.handlers.drainConfirmationOutbox(f.env);
  assert.equal(f.sqlite.prepare('SELECT status FROM confirmation_email_outbox').get()?.status, 'unknown');
  await f.handlers.drainConfirmationOutbox(f.env);
  assert.equal(count, 2);
  f.sqlite.close();
});

test('pilot blocks new non-admin subscriptions and holds pre-existing confirmation jobs', async () => {
  let sends = 0;
  const f = fixture({ send: async () => { sends++; return { status: 'sent', provider: 'smtp' }; } });
  await submit(f);
  f.env.DELIVERY_MODE = 'pilot'; f.env.PILOT_RECIPIENTS = 'admin@example.test';
  await assert.rejects(f.handlers.createSubscription(make(payload), f.env), /subscription_pilot_only/);
  await f.handlers.drainConfirmationOutbox(f.env);
  assert.equal(sends, 0);
  assert.equal(f.sqlite.prepare('SELECT status FROM confirmation_email_outbox').get()?.status, 'queued');
  f.env.DELIVERY_MODE = 'full';
  await f.handlers.drainConfirmationOutbox(f.env);
  assert.equal(sends, 1);
  f.sqlite.close();
});
