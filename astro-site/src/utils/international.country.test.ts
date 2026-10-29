import { strict as assert } from 'node:assert';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, test } from 'node:test';
import {
  INTERNATIONAL_SCHEMA_VERSION,
  isoWeekKey,
  isoWeekMonth,
  readAlertsByCountry,
  readAlertsForCountryPage,
  recentAlerts,
} from './international.ts';

// Fixture shards keep these tests independent of whichever alerts the last export held.
const dir = mkdtempSync(join(tmpdir(), 'intl-country-'));
after(() => rmSync(dir, { recursive: true, force: true }));

function alert(id: string, published: string) {
  return { alert_id: id, published_date: published } as any;
}

function shard(code: string, alerts: ReturnType<typeof alert>[]) {
  mkdirSync(join(dir, 'by-country'), { recursive: true });
  writeFileSync(
    join(dir, 'by-country', `${code}.json`),
    JSON.stringify({ schema_version: INTERNATIONAL_SCHEMA_VERSION, alert_count: alerts.length, alerts }),
  );
}

shard('UG', [alert('ug-1', '2026-10-01')]);
shard('US', [alert('us-1', '2026-09-01')]);
shard('CD', [alert('cd-1', '2026-08-01'), alert('cd-2', '2026-10-20'), alert('cd-3', '2026-09-15')]);

test('a country page slug resolves to the ISO-keyed shard', () => {
  assert.deepEqual(readAlertsForCountryPage('ug', dir).map(item => item.alert_id), ['ug-1']);
});

test('sub-national pages never inherit their parent country alerts', () => {
  assert.ok(readAlertsByCountry('US', dir).length > 0, 'fixture US shard exists');
  assert.equal(readAlertsForCountryPage('us', dir).length, 1);
  assert.deepEqual(readAlertsForCountryPage('us-ny', dir), []);
});

test('unknown slugs and traversal attempts resolve to nothing', () => {
  assert.deepEqual(readAlertsForCountryPage('zzz-nope', dir), []);
  assert.deepEqual(readAlertsForCountryPage('../../etc/passwd', dir), []);
  assert.deepEqual(readAlertsForCountryPage('', dir), []);
});

test('a foreign schema version reads as absent', () => {
  mkdirSync(join(dir, 'by-country'), { recursive: true });
  writeFileSync(join(dir, 'by-country', 'FR.json'), JSON.stringify({ schema_version: 'old', alerts: [alert('x', '2026-01-01')] }));
  assert.deepEqual(readAlertsByCountry('FR', dir), []);
});

test('recentAlerts caps the list and keeps newest first', () => {
  assert.deepEqual(recentAlerts(readAlertsForCountryPage('cd', dir), 2).map(item => item.alert_id), ['cd-2', 'cd-3']);
  assert.deepEqual(recentAlerts(null), []);
});

test('ISO week helpers agree with the export keys', () => {
  assert.equal(isoWeekKey('2026-10-21'), '2026-W43');
  assert.equal(isoWeekKey('2026-01-01'), '2026-W01');
  assert.equal(isoWeekKey('nonsense'), null);
  assert.equal(isoWeekMonth('2026-W43'), '2026-10');
});
