import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import type { MapPoint, SituationAlertRecord } from '../../utils/situationTypes.ts';
import { loadOverview, overviewPaths } from './overview-data.ts';

const filters = { disease: '', country: '', source: '', window: '30' } as const;
const marker = (id: string, extra = {}) => ({ alert_id: id, outbreak_id: id, source_id: 'official', disease_id: 'D049', disease_label: 'MERS', country_code: 'SA', published_date: '2026-11-10', ...extra }) as MapPoint;

test('overview chooses a complete entity shard or only matching outbreaks', () => {
  const points = [marker('one'), marker('copy', { outbreak_id: 'one' }), marker('two', { country_code: 'KE' }), marker('old', { published_date: '2026-01-01' })];
  assert.deepEqual(overviewPaths(points, filters, '2026-11-15'), ['/situation/outbreaks/one.json', '/situation/outbreaks/two.json']);
  assert.deepEqual(overviewPaths(points, { ...filters, disease: 'D049' }, '2026-11-15'), ['/situation/by-disease/D049.json']);
  assert.deepEqual(overviewPaths(points, { ...filters, country: 'KE' }, '2026-11-15'), ['/situation/by-country/KE.json']);
  assert.deepEqual(overviewPaths(points, { ...filters, source: 'absent' }, '2026-11-15'), []);
});

test('legacy overview uses matching outbreak routes even with entity filters', () => {
  const points = [marker('one'), marker('copy', { outbreak_id: 'one' }), marker('two', { country_code: 'KE' }), marker('other', { disease_id: 'D038' })];
  assert.deepEqual(overviewPaths(points, { ...filters, disease: 'D049' }, '2026-11-15', false), ['/situation/outbreaks/one.json', '/situation/outbreaks/two.json']);
  assert.deepEqual(overviewPaths(points, { ...filters, country: 'SA', disease: 'D049' }, '2026-11-15', false), ['/situation/outbreaks/one.json']);
  assert.deepEqual(overviewPaths(points, { ...filters, source: 'missing' }, '2026-11-15', false), []);
});

test('overview limits concurrent reads, preserves partial results and deduplicates report identities', async () => {
  let active = 0;
  let peak = 0;
  let calls = 0;
  let progress: { ids: string[]; failed: number } = { ids: [], failed: 0 };
  const paths = Array.from({ length: 9 }, (_, index) => String(index));
  await loadOverview(paths, async path => {
    active += 1;
    peak = Math.max(peak, active);
    calls += 1;
    await new Promise(resolve => setImmediate(resolve));
    active -= 1;
    if (path === '3') throw new Error('unavailable');
    if (path === '5') return {} as { alerts: SituationAlertRecord[] };
    return { alerts: [marker('shared'), marker(path)] as SituationAlertRecord[] };
  }, new AbortController().signal, (alerts, failed) => { progress = { ids: alerts.map(alert => alert.alert_id).sort(), failed }; });
  assert.equal(calls, 9);
  assert.equal(peak, 4);
  assert.equal(progress.failed, 2);
  assert.deepEqual(progress.ids, ['0', '1', '2', '4', '6', '7', '8', 'shared']);
});

test('abandoned overview reads never publish results or start another batch', async () => {
  const controller = new AbortController();
  let calls = 0;
  const updates: unknown[] = [];
  await loadOverview(['a', 'b', 'c', 'd', 'e'], async () => {
    calls += 1;
    controller.abort();
    return { alerts: [marker('stale')] as SituationAlertRecord[] };
  }, controller.signal, alerts => updates.push(alerts));
  assert.equal(calls, 1);
  assert.equal(updates.length, 0);
});
