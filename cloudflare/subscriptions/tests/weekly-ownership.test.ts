import assert from 'node:assert/strict';
import test from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';

const migration = readFileSync(new URL('../migrations/0017_weekly_issue_ownership.sql', import.meta.url), 'utf8');
function fixture() {
  const db = new DatabaseSync(':memory:');
  db.exec('CREATE TABLE message_campaigns(id TEXT PRIMARY KEY, trigger_type TEXT NOT NULL, content_ref TEXT)');
  return db;
}
function insert(db: DatabaseSync, id: string, key: string) {
  db.prepare('INSERT INTO message_campaigns(id,trigger_type,content_ref) VALUES (?, ?, ?)')
    .run(id, 'admin_notification', 'idempotency:' + key);
}

test('ownership migration uses GLOB patterns within the remote D1 limit', () => {
  const patterns = [...migration.matchAll(/\bGLOB\s+'([^']*)'/g)].map(match => match[1]);
  assert.ok(patterns.length > 0);
  assert.ok(patterns.every(pattern => pattern.length <= 50));
});

test('D1 unique ownership blocks both legacy/integrated orders for the same week and mode', () => {
  for (const order of ['legacy-first', 'integrated-first']) {
    const db = fixture(); db.exec(migration);
    const legacy = 'reports-weekly:2026-W45:weekly_digest:r1';
    const integrated = 'weekly-digest:2026-W45:r1';
    insert(db, 'first', order === 'legacy-first' ? legacy : integrated);
    assert.throws(() => insert(db, 'second', order === 'legacy-first' ? integrated : legacy), /UNIQUE/);
    insert(db, 'pilot', 'pilot:weekly-digest:2026-W45:r1');
    insert(db, 'next-week', 'weekly-digest:2026-W46:r1');
    insert(db, 'reports-list', 'reports-weekly:2026-W45:reports:r1');
    db.close();
  }
});

test('conflicting historical issues stop migration for reconciliation', () => {
  const db = fixture();
  insert(db, 'legacy', 'reports-weekly:2026-W45:weekly_digest:r1');
  insert(db, 'integrated', 'weekly-digest:2026-W45:r1');
  assert.throws(() => db.exec(migration), /UNIQUE/);
  db.close();
});
