import assert from 'node:assert/strict';
import test from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { createCampaignHandlers } from '../src/handlers/campaigns.ts';
import { applyDeliveryFeedback } from '../src/lib/delivery-feedback.ts';
import type { Env } from '../src/types.ts';

function fixture() {
  const sqlite = new DatabaseSync(':memory:');
  const hooks: { beforeRun?: (query: string) => void } = {};
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
        async run() { hooks.beforeRun?.(query); const result = sqlite.prepare(query).run(...values as never[]); return { meta: { changes: Number(result.changes) } }; },
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
  return { sqlite, env, add, hooks };
}

function request(payload: unknown = {}) {
  return new Request('https://example.test/api/admin/notifications', { method: 'POST', headers: {
    'content-type': 'application/json', authorization: 'Bearer secret',
  }, body: JSON.stringify(payload) });
}
function deps(send: (message: { to: string; text: string; html: string }) => Promise<unknown>) {
  return {
    async readPayload(req: Request) { return await req.json() as Record<string, unknown>; },
    async createSignedToken() { return 'signed'; },
    smtpConfig() { return { host: 'invalid', port: 587, username: 'x', password: 'x', fromEmail: 'x@example.test', fromName: 'x', useTls: true }; },
    async sendSmtpEmail(_config: unknown, message: { to: string; text: string; html: string }) { await send(message); return { providerMessageId: 'provider-id' }; },
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

const personalizedIssue = {
  list_codes: ['research_digest'], frequency: 'weekly', target_locales: ['en', 'zh'],
  contents: { en: { subject: 'Research', markdown: 'Full issue' }, zh: { subject: '研究', markdown: '完整简报' } },
  personalized_templates: { en: 'Research\n\n{{GIDS_PERSONALIZED_ITEMS}}\n\nFull brief link', zh: '研究\n\n{{GIDS_PERSONALIZED_ITEMS}}\n\n完整简报链接' },
  content_items: [
    { id: 'one', countries: ['CN'], diseases: ['influenza'], localized_markdown: { en: 'China flu evidence', zh: '中国流感证据' } },
    { id: 'two', countries: ['US'], diseases: ['measles'], localized_markdown: { en: 'US measles evidence', zh: '美国麻疹证据' } },
  ],
};

test('personalized delivery uses same-item matching and current preferences in both languages', async () => {
  const { sqlite, env, add } = fixture();
  add('china', 'research_digest', [['country', 'CN'], ['disease', 'influenza']]);
  add('usa', 'research_digest', [['country', 'US']]);
  add('all', 'research_digest');
  add('historical-locale', 'research_digest', [['country', 'US']]);
  add('changed', 'research_digest', [['country', 'CN']]);
  sqlite.prepare("UPDATE subscribers SET locale='zh' WHERE id='sub-usa'").run();
  sqlite.prepare("UPDATE subscribers SET locale='fr' WHERE id='sub-historical-locale'").run();
  const messages = new Map<string, string>();
  const handlers = createCampaignHandlers(deps(async m => { messages.set(m.to, m.text); }));
  const id = await create(handlers, env, personalizedIssue);
  // The user changed preferences after the audience was captured.
  sqlite.prepare("UPDATE subscription_filters SET filter_value='US' WHERE subscription_id='subscription-changed'").run();
  sqlite.prepare("UPDATE subscriptions SET preference_revision='new' WHERE id='subscription-changed'").run();
  await handlers.process(request({ batch_size: 5 }), env, id);
  await handlers.process(request({ batch_size: 5 }), env, id);
  assert.match(messages.get('china@example.test')!, /China flu evidence/);
  assert.doesNotMatch(messages.get('china@example.test')!, /US measles evidence/);
  assert.match(messages.get('usa@example.test')!, /美国麻疹证据/);
  assert.doesNotMatch(messages.get('usa@example.test')!, /中国流感证据/);
  assert.match(messages.get('changed@example.test')!, /US measles evidence/);
  assert.match(messages.get('historical-locale@example.test')!, /US measles evidence/);
  assert.doesNotMatch(messages.get('changed@example.test')!, /China flu evidence/);
  assert.match(messages.get('all@example.test')!, /China flu evidence[\s\S]*US measles evidence/);
  sqlite.close();
});

test('personalized delivery skips when preferences no longer match and on revision races', async () => {
  const { sqlite, env, add } = fixture();
  add('missing', 'research_digest', [['country', 'CN']]);
  add('race', 'research_digest', [['country', 'CN']]);
  let sends = 0;
  const dependencies = deps(async () => { sends++; });
  const handlers = createCampaignHandlers({ ...dependencies, async createSignedToken(_env, purpose, id) {
    if (purpose === 'unsubscribe' && id === 'subscription-race')
      sqlite.prepare("UPDATE subscriptions SET preference_revision='changed' WHERE id=?").run(id);
    return 'signed';
  } });
  const id = await create(handlers, env, personalizedIssue);
  sqlite.prepare("UPDATE subscription_filters SET filter_value='GB' WHERE subscription_id='subscription-missing'").run();
  await handlers.process(request({ batch_size: 5 }), env, id);
  assert.equal(sends, 0);
  assert.deepEqual(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id=?').all(id).map(r => r.status).sort(), ['queued', 'skipped']);
  await handlers.process(request({ batch_size: 5 }), env, id);
  assert.equal(sends, 1);
  assert.deepEqual(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id=?').all(id).map(r => r.status).sort(), ['sent', 'skipped']);
  sqlite.close();
});

test('a racing preference change is rematched and repeated churn is bounded', async () => {
  const { sqlite, env, add } = fixture();
  add('lost-match', 'research_digest', [['country', 'CN']]);
  add('churn', 'research_digest', [['country', 'CN']]);
  let sends = 0, revision = 0;
  const dependencies = deps(async () => { sends++; });
  const handlers = createCampaignHandlers({ ...dependencies, async createSignedToken(_env, purpose, id) {
    if (purpose === 'unsubscribe' && id === 'subscription-lost-match') {
      sqlite.prepare("UPDATE subscription_filters SET filter_value='GB' WHERE subscription_id=?").run(id);
      sqlite.prepare("UPDATE subscriptions SET preference_revision='new' WHERE id=?").run(id);
    }
    if (purpose === 'unsubscribe' && id === 'subscription-churn') {
      sqlite.prepare('UPDATE subscriptions SET preference_revision=? WHERE id=?').run(String(++revision), id);
    }
    return 'signed';
  } });
  const id = await create(handlers, env, personalizedIssue);
  for (let i = 0; i < 6; i++) await handlers.process(request({ batch_size: 5 }), env, id);
  assert.equal(sends, 0);
  const rows = sqlite.prepare('SELECT subscription_id, status, last_error FROM message_deliveries WHERE campaign_id=? ORDER BY subscription_id').all(id);
  assert.deepEqual(rows.map(r => [r.subscription_id, r.status, r.last_error]), [
    ['subscription-churn', 'failed', 'preference_changed_too_often'],
    ['subscription-lost-match', 'skipped', 'subscription_filters_no_longer_match'],
  ]);
  sqlite.close();
});

test('a preference change after finding no matching items is rematched before skipping', async () => {
  const { sqlite, env, add, hooks } = fixture();
  add('race-empty', 'research_digest', [['country', 'CN']]);
  const messages: string[] = [];
  const handlers = createCampaignHandlers(deps(async m => { messages.push(m.text); }));
  const id = await create(handlers, env, personalizedIssue);
  // First become unmatched; then change again immediately before the guarded
  // skip update, after the consumer has already read the old preferences.
  sqlite.prepare("UPDATE subscription_filters SET filter_value='GB' WHERE subscription_id='subscription-race-empty'").run();
  sqlite.prepare("UPDATE subscriptions SET preference_revision='one' WHERE id='subscription-race-empty'").run();
  hooks.beforeRun = query => {
    if (!query.includes("last_error = 'subscription_filters_no_longer_match'")) return;
    hooks.beforeRun = undefined;
    sqlite.prepare("UPDATE subscription_filters SET filter_value='US' WHERE subscription_id='subscription-race-empty'").run();
    sqlite.prepare("UPDATE subscriptions SET preference_revision='two' WHERE id='subscription-race-empty'").run();
  };
  await handlers.process(request(), env, id);
  assert.equal(messages.length, 0);
  assert.equal(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id=?').get(id)?.status, 'queued');
  await handlers.process(request(), env, id);
  assert.equal(messages.length, 1);
  assert.match(messages[0], /US measles evidence/);
  assert.doesNotMatch(messages[0], /China flu evidence/);
  sqlite.close();
});

test('malformed personalization fails before creating a campaign', async () => {
  const { sqlite, env, add } = fixture(); add('first', 'research_digest');
  const handlers = createCampaignHandlers(deps(async () => {}));
  const response = await handlers.create(request({ ...personalizedIssue, personalized_templates: { en: 'missing placeholder', zh: '{{GIDS_PERSONALIZED_ITEMS}}' } }), env);
  assert.equal(response.status, 400);
  const missingFallback = await handlers.create(request({ ...personalizedIssue, default_locale: 'zh', target_locales: ['en'],
    personalized_templates: { en: '{{GIDS_PERSONALIZED_ITEMS}}' } }), env);
  assert.equal(missingFallback.status, 400);
  const missingSubject = await handlers.create(request({ ...personalizedIssue, contents: { en: { subject: 'News', markdown: 'Body' } } }), env);
  assert.equal(missingSubject.status, 400);
  assert.equal(sqlite.prepare('SELECT COUNT(*) AS n FROM message_campaigns').get()?.n, 0);
  sqlite.close();
});

test('personalized issue fits exactly within the markdown limit and rejects one extra character', async () => {
  const { sqlite, env, add } = fixture(); add('first', 'research_digest');
  const messages: string[] = [];
  const handlers = createCampaignHandlers(deps(async message => { messages.push(message.text); }));
  const issue = (firstLength: number) => ({
    list_codes: ['research_digest'], frequency: 'weekly', target_locales: ['en'],
    contents: { en: { subject: 'Limit', markdown: 'Summary' } },
    personalized_templates: { en: '{{GIDS_PERSONALIZED_ITEMS}}' },
    content_items: [firstLength, 16666, 16666].map((length, index) => ({
      id: String(index), localized_markdown: { en: 'x'.repeat(length) },
    })),
  });
  const tooLong = await handlers.create(request(issue(16665)), env);
  assert.equal(tooLong.status, 400);
  assert.equal(sqlite.prepare('SELECT COUNT(*) AS n FROM message_campaigns').get()?.n, 0);
  const exact = await handlers.create(request(issue(16664)), env);
  assert.equal(exact.status, 201);
  const body = await exact.json() as { campaign: { id: string } };
  await handlers.process(request(), env, body.campaign.id);
  assert.equal(messages.length, 1);
  assert.equal((messages[0].match(/x{100,}/g) || []).reduce((total, part) => total + part.length, 0), 49996);
  assert.match(messages[0], /Unsubscribe/);
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

test('integrated weekly campaign remains queued without the per-attempt source guard, including Cron recovery', async () => {
  const { sqlite, env, add } = fixture(); add('weekly-reader', 'weekly_digest');
  let sends = 0;
  const handlers = createCampaignHandlers(deps(async () => { sends++; }));
  const id = await create(handlers, env, {
    idempotency_key: 'weekly-digest:2026-W45:r1', list_codes: ['weekly_digest'], frequency: 'weekly',
  });
  const response = await handlers.process(request(), env, id);
  assert.equal(response.status, 503);
  assert.equal((await response.json() as { error: string }).error, 'weekly_digest_source_guard_unavailable');
  assert.equal(sqlite.prepare('SELECT processing_started_at FROM message_campaigns WHERE id=?').get(id)?.processing_started_at, null);
  // Simulate an issue started by older code before this guard was deployed.
  sqlite.prepare("UPDATE message_campaigns SET processing_started_at='2026-01-01T00:00:00Z' WHERE id=?").run(id);
  await handlers.drainDue(env);
  const row = sqlite.prepare('SELECT status, attempts FROM message_deliveries WHERE campaign_id=?').get(id);
  assert.deepEqual([row?.status, row?.attempts, sends], ['queued', 0, 0]);
  sqlite.close();
});

test('blocked oldest weekly campaign does not starve a later started ordinary campaign', async () => {
  const { sqlite, env, add } = fixture();
  add('weekly-reader', 'weekly_digest');
  add('ordinary-reader', 'reports');
  const delivered: string[] = [];
  const handlers = createCampaignHandlers(deps(async message => { delivered.push(message.to); }));
  const blocked = await create(handlers, env, {
    idempotency_key: 'weekly-digest:2026-W45:r1', list_codes: ['weekly_digest'], frequency: 'weekly',
  });
  const ordinary = await create(handlers, env, { list_codes: ['reports'] });
  sqlite.prepare("UPDATE message_campaigns SET processing_started_at='2026-01-01T00:00:00Z', created_at='2026-01-01T00:00:00Z' WHERE id=?").run(blocked);
  sqlite.prepare("UPDATE message_campaigns SET processing_started_at='2026-01-02T00:00:00Z', created_at='2026-01-02T00:00:00Z' WHERE id=?").run(ordinary);
  await handlers.drainDue(env);
  assert.deepEqual(delivered, ['ordinary-reader@example.test']);
  const blockedRow = sqlite.prepare('SELECT status, attempts FROM message_deliveries WHERE campaign_id=?').get(blocked);
  const ordinaryRow = sqlite.prepare('SELECT status, attempts FROM message_deliveries WHERE campaign_id=?').get(ordinary);
  assert.deepEqual([blockedRow?.status, blockedRow?.attempts], ['queued', 0]);
  assert.deepEqual([ordinaryRow?.status, ordinaryRow?.attempts], ['sent', 1]);
  sqlite.close();
});

test('Cron processes an uppercase custom key that is not the guarded producer prefix', async () => {
  const { sqlite, env, add } = fixture();
  add('reader', 'reports');
  const delivered: string[] = [];
  const handlers = createCampaignHandlers(deps(async message => { delivered.push(message.to); }));
  const id = await create(handlers, env, { idempotency_key: 'WEEKLY-DIGEST:2026-W45:r1', list_codes: ['reports'] });
  sqlite.prepare("UPDATE message_campaigns SET processing_started_at='2026-01-01T00:00:00Z' WHERE id=?").run(id);
  await handlers.drainDue(env);
  assert.deepEqual(delivered, ['reader@example.test']);
  assert.equal(sqlite.prepare('SELECT status FROM message_deliveries WHERE campaign_id=?').get(id)?.status, 'sent');
  sqlite.close();
});

test('legacy and integrated automated weekly producers receive a conflict for the same issue', async () => {
  const { sqlite, env, add } = fixture(); add('weekly-reader', 'weekly_digest');
  const handlers = createCampaignHandlers(deps(async () => {}));
  await create(handlers, env, { idempotency_key: 'reports-weekly:2026-W45:weekly_digest:r1',
    list_codes: ['weekly_digest'], frequency: 'weekly' });
  const response = await handlers.create(request({ idempotency_key: 'weekly-digest:2026-W45:r1',
    list_codes: ['weekly_digest'], frequency: 'weekly', subject: 'Weekly', markdown: 'Body' }), env);
  assert.equal(response.status, 409);
  assert.equal((await response.json() as { error: string }).error, 'weekly_digest_issue_already_owned');
  assert.equal(sqlite.prepare('SELECT COUNT(*) AS n FROM message_campaigns').get()?.n, 1);
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
