import assert from 'node:assert/strict';
import test from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { createCampaignHandlers } from '../src/handlers/campaigns.ts';
import { applyDeliveryFeedback } from '../src/lib/delivery-feedback.ts';
import type { Env } from '../src/types.ts';

function fixture() {
  const sqlite = new DatabaseSync(':memory:');
  const dir = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(dir).filter(name => name.endsWith('.sql')).sort()) {
    sqlite.exec(readFileSync(new URL(file, dir), 'utf8'));
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
      sqlite.exec('BEGIN');
      try { const results = []; for (const stmt of statements) results.push(await stmt.run()); sqlite.exec('COMMIT'); return results; }
      catch (error) { sqlite.exec('ROLLBACK'); throw error; }
    },
  };
  const env = { DELIVERY_MODE: "full", DB: db, ADMIN_API_TOKEN: 'secret', PUBLIC_BASE_URL: 'https://example.test' } as unknown as Env;
  function add(id: string, list = 'reports', filters: Array<[string, string]> = []) {
    const now = '2026-01-01T00:00:00Z';
    sqlite.prepare('INSERT INTO subscribers (id,locale,timezone,status,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)')
      .run('sub-' + id, 'en', 'UTC', 'active', now, now);
    sqlite.prepare('INSERT INTO subscriber_contacts (id,subscriber_id,channel,address,address_hash,status,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
      .run('contact-' + id, 'sub-' + id, 'email', id + '@example.test', 'hash-' + id, 'active', now, now);
    sqlite.prepare('INSERT INTO subscriptions (id,subscriber_id,contact_id,list_id,status,frequency,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
      .run('subscription-' + id, 'sub-' + id, 'contact-' + id, 'list_' + list, 'active', 'weekly', now, now);
    for (const [type, value] of filters) sqlite.prepare('INSERT INTO subscription_filters (id,subscription_id,filter_type,filter_value,created_at) VALUES (?, ?, ?, ?, ?)')
      .run(crypto.randomUUID(), 'subscription-' + id, type, value, now);
  }
  return { sqlite, env, add };
}

function request(payload: unknown = {}) {
  return new Request('https://example.test/api/admin/notifications', { method: 'POST', headers: {
    'content-type': 'application/json', authorization: 'Bearer secret',
  }, body: JSON.stringify(payload) });
}
function deps(send: (message: { to: string }) => Promise<unknown>) {
  return {
    async readPayload(req: Request) { return await req.json() as Record<string, unknown>; },
    async createSignedToken() { return 'signed'; },
    smtpConfig() { return { host: 'invalid', port: 587, username: 'x', password: 'x', fromEmail: 'x@example.test', fromName: 'x', useTls: true }; },
    async sendSmtpEmail(_config: unknown, message: { to: string }) { await send(message); return { providerMessageId: 'provider-id' }; },
  };
}
async function create(handlers: ReturnType<typeof createCampaignHandlers>, env: Env, extra: Record<string, unknown> = {}) {
  const response = await handlers.create(request({ subject: 'Subject', markdown: 'Body', list_codes: ['reports'], ...extra }), env);
  assert.equal(response.status, 201);
  return (await response.json() as { campaign: { id: string } }).campaign.id;
}

test('concurrent consumers claim once and skip unsubscribed recipients', async () => {
  const { sqlite, env, add } = fixture();
  add('first'); add('second');
  let sends = 0;
  let release!: () => void;
  const barrier = new Promise<void>(resolve => { release = resolve; });
  let entered!: () => void;
  const sending = new Promise<void>(resolve => { entered = resolve; });
  const handlers = createCampaignHandlers(deps(async () => { sends += 1; entered(); await barrier; }));
  const id = await create(handlers, env);
  sqlite.prepare("UPDATE subscriptions SET status = 'unsubscribed' WHERE id = ?").run('subscription-second');
  const first = handlers.process(request({ batch_size: 1 }), env, id);
  await sending;
  const second = handlers.process(request({ batch_size: 5 }), env, id);
  await second;
  release();
  await first;
  assert.equal(sends, 1);
  const rows = sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id = ? ORDER BY status').all(id);
  assert.deepEqual(rows.map(row => row.status), ['sent', 'skipped']);
  sqlite.close();
});

test('ambiguous SMTP failure becomes unknown and is not automatically resent', async () => {
  const { sqlite, env, add } = fixture(); add('first');
  let sends = 0;
  const handlers = createCampaignHandlers(deps(async () => { sends += 1; throw new Error('connection_reset'); }));
  const id = await create(handlers, env);
  await handlers.process(request(), env, id);
  await handlers.process(request(), env, id);
  assert.equal(sends, 1);
  assert.equal(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id = ?').get(id)?.status, 'unknown');
  sqlite.close();
});

test('4xx SMTP rejection is retried only when due', async () => {
  const { sqlite, env, add } = fixture(); add('first');
  let sends = 0;
  const handlers = createCampaignHandlers(deps(async () => { if (++sends === 1) throw new Error('DATA_END_rejected:450'); }));
  const id = await create(handlers, env);
  await handlers.process(request(), env, id);
  await handlers.process(request(), env, id);
  assert.equal(sends, 1);
  assert.equal(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id = ?').get(id)?.status, 'queued');
  sqlite.prepare('UPDATE message_deliveries SET next_attempt_at = ? WHERE campaign_id = ?').run('2000-01-01T00:00:00Z', id);
  await handlers.process(request(), env, id);
  assert.equal(sends, 2);
  assert.equal(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id = ?').get(id)?.status, 'sent');
  sqlite.close();
});

test('content items require one article to satisfy all selected dimensions', async () => {
  const { sqlite, env, add } = fixture();
  add('wrong', 'research_digest', [['country', 'CN'], ['disease', 'measles']]);
  add('matching', 'research_digest', [['country', 'CN'], ['disease', 'influenza']]);
  add('all', 'research_digest');
  add('missing', 'research_digest', [['country', 'CN'], ['research_topic', 'vaccination']]);
  const handlers = createCampaignHandlers(deps(async () => {}));
  const id = await create(handlers, env, {
    list_codes: ['research_digest'], frequency: 'weekly', content_items: [
      { id: 'one', countries: ['CN'], diseases: ['influenza'] },
      { id: 'two', countries: ['US'], diseases: ['measles'] },
    ],
  });
  const recipients = sqlite.prepare('SELECT contact_id FROM message_deliveries WHERE campaign_id = ? ORDER BY contact_id').all(id);
  assert.deepEqual(recipients.map(row => row.contact_id), ['contact-all', 'contact-matching']);
  sqlite.close();
});

test('scheduled recovery processes only campaigns explicitly started by an operator', async () => {
  const { sqlite, env, add } = fixture(); add('first');
  let sends = 0;
  const handlers = createCampaignHandlers(deps(async () => { sends += 1; }));
  const id = await create(handlers, env);
  await handlers.drainDue(env);
  assert.equal(sends, 0);
  sqlite.prepare('UPDATE message_campaigns SET processing_started_at = ? WHERE id = ?').run('2026-01-01T00:00:00Z', id);
  await handlers.drainDue(env);
  assert.equal(sends, 1);
  assert.equal(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id = ?').get(id)?.status, 'sent');
  sqlite.close();
});

test('expired in-flight campaign claim becomes unknown without a resend', async () => {
  const { sqlite, env, add } = fixture(); add('first');
  let sends = 0;
  const handlers = createCampaignHandlers(deps(async () => { sends += 1; }));
  const id = await create(handlers, env);
  sqlite.prepare('UPDATE message_campaigns SET processing_started_at = ? WHERE id = ?').run('2026-01-01T00:00:00Z', id);
  sqlite.prepare("UPDATE message_deliveries SET status = 'sending', claim_token = 'old', claim_expires_at = ? WHERE campaign_id = ?")
    .run('2026-01-01T00:00:00Z', id);
  await handlers.drainDue(env);
  assert.equal(sends, 0);
  assert.equal(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id = ?').get(id)?.status, 'unknown');
  sqlite.close();
});

test('provider feedback arriving before the SMTP response updates the campaign and is not downgraded', async () => {
  const { sqlite, env, add } = fixture(); add('first');
  const handlers = createCampaignHandlers(deps(async () => {
    const row = sqlite.prepare('SELECT transaction_delivery_id FROM message_deliveries').get();
    assert.ok(row?.transaction_delivery_id);
    const result = await applyDeliveryFeedback(env.DB, {
      provider: 'smtp', providerEventId: 'early-delivery', providerMessageId: 'provider-id',
      correlationId: String(row.transaction_delivery_id), eventType: 'delivered',
      errorCode: '', occurredAt: new Date().toISOString(),
    });
    assert.equal(result.campaignRows, 1);
    assert.equal(result.transactionalRows, 1);
  }));
  const id = await create(handlers, env);
  await handlers.process(request(), env, id);
  assert.equal(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id = ?').get(id)?.status, 'delivered');
  assert.equal(sqlite.prepare('SELECT status FROM transactional_email_deliveries').get()?.status, 'delivered');
  sqlite.close();
});

test('campaign and its audience roll back together if fan-out fails', async () => {
  const { sqlite, env, add } = fixture(); add('first');
  sqlite.exec("CREATE TRIGGER fail_fanout BEFORE INSERT ON message_deliveries BEGIN SELECT RAISE(ABORT, 'injected failure'); END;");
  const handlers = createCampaignHandlers(deps(async () => {}));
  await assert.rejects(handlers.create(request({
    idempotency_key: 'atomic-campaign', list_codes: ['reports'], subject: 'Subject', markdown: 'Body',
  }), env), /injected failure/);
  assert.equal(sqlite.prepare('SELECT COUNT(*) AS n FROM message_campaigns').get()?.n, 0);
  assert.equal(sqlite.prepare('SELECT COUNT(*) AS n FROM message_deliveries').get()?.n, 0);
  sqlite.close();
});

test('pilot only delivers to administrators, holds existing jobs and resumes them after full release', async () => {
  const { sqlite, env, add } = fixture();
  add('admin'); add('reader');
  const delivered: string[] = [];
  const handlers = createCampaignHandlers(deps(async message => { delivered.push(message.to); }));
  const oldCampaign = await create(handlers, env);
  env.DELIVERY_MODE = 'pilot'; env.PILOT_RECIPIENTS = ' ADMIN@example.test ';
  await handlers.process(request(), env, oldCampaign);
  assert.deepEqual(delivered, ['admin@example.test']);
  assert.deepEqual(sqlite.prepare('SELECT status, attempts FROM message_deliveries WHERE campaign_id=? ORDER BY status').all(oldCampaign).map(r => [r.status, r.attempts]), [['queued', 0], ['sent', 1]]);
  const pilotCampaign = await create(handlers, env);
  assert.equal(sqlite.prepare('SELECT COUNT(*) AS n FROM message_deliveries WHERE campaign_id=?').get(pilotCampaign)?.n, 1);
  env.DELIVERY_MODE = 'full';
  await handlers.process(request(), env, oldCampaign);
  await handlers.process(request(), env, pilotCampaign);
  assert.deepEqual(delivered, ['admin@example.test', 'reader@example.test', 'admin@example.test']);
  sqlite.close();
});

test('an absent pilot allowlist sends nothing and preserves an existing campaign', async () => {
  const { sqlite, env, add } = fixture();
  add('reader');
  let sent = 0;
  const handlers = createCampaignHandlers(deps(async () => { sent++; }));
  const id = await create(handlers, env);
  env.DELIVERY_MODE = 'pilot';
  await handlers.process(request(), env, id);
  assert.equal(sent, 0);
  assert.equal(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id=?').get(id)?.status, 'queued');
  sqlite.close();
});

test('authenticated audience export cannot bypass pilot restriction', async () => {
  const { listAudience } = await import('../src/handlers/subscriptions.ts');
  const { sqlite, env, add } = fixture();
  add('admin'); add('reader');
  env.DELIVERY_MODE = 'pilot'; env.PILOT_RECIPIENTS = 'ADMIN@example.test';
  const response = await listAudience(request({ list_code: 'reports' }), env, {
    async readPayload(req) { return await req.json() as Record<string, unknown>; },
    async createSignedToken() { return 'signed'; },
  });
  const body = await response.json() as { recipients: Array<{ email: string }> };
  assert.deepEqual(body.recipients.map(row => row.email), ['admin@example.test']);
  sqlite.close();
});
