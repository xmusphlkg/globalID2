import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { ageDays, buildAnchors, filterPoints, placePoints, recencyOf, sortPoints } from './model.ts';

const AS_OF = '2026-10-28';
const point = (id: string, day: string, extra: Record<string, unknown> = {}) =>
  ({ alert_id: id, published_date: day, country_code: 'CD', disease_id: 'D050', disease_label: 'Ebola', source_id: 'who_don', ...extra }) as any;

test('recency buckets follow the age of the report, not any severity', () => {
  assert.equal(ageDays('2026-10-21', AS_OF), 7);
  assert.equal(recencyOf('2026-10-21', AS_OF), 'fresh');
  assert.equal(recencyOf('2026-10-20', AS_OF), 'recent');
  assert.equal(recencyOf('2026-09-28', AS_OF), 'recent');
  assert.equal(recencyOf('2026-09-27', AS_OF), 'older');
  assert.equal(recencyOf('2026-06-01', AS_OF), 'stale');
  assert.equal(recencyOf('garbage', AS_OF), 'stale');
});

test('points updated in the last 7 days sort first, then newest first', () => {
  const sorted = sortPoints([point('b', '2026-10-01'), point('a', '2026-10-27'), point('c', '2026-10-22')], AS_OF);
  assert.deepEqual(sorted.map(item => item.alert_id), ['a', 'c', 'b']);
});

test('filters combine and the window drops older points', () => {
  const rows = [
    point('a', '2026-10-27'),
    point('b', '2026-10-01', { country_code: 'KE' }),
    point('c', '2026-08-01', { source_id: 'ecdc_cdtr' }),
  ];
  const base = { disease: '', country: '', source: '', window: 'all' } as const;
  assert.equal(filterPoints(rows, base, AS_OF).length, 3);
  assert.equal(filterPoints(rows, { ...base, window: '30' }, AS_OF).length, 2);
  assert.deepEqual(filterPoints(rows, { ...base, country: 'KE' }, AS_OF).map(item => item.alert_id), ['b']);
  assert.deepEqual(filterPoints(rows, { ...base, source: 'ecdc_cdtr' }, AS_OF).map(item => item.alert_id), ['c']);
  assert.equal(filterPoints(rows, { ...base, disease: 'D999' }, AS_OF).length, 0);
});

test('anchors use the largest landmass and points in one country fan out', () => {
  const world = {
    features: [
      {
        properties: { name: 'Congo (Kinshasa)' },
        geometry: {
          type: 'MultiPolygon',
          coordinates: [
            [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
            [[[50, 50], [51, 50], [51, 51], [50, 51], [50, 50]]],
          ],
        },
      },
    ],
  };
  const anchors = buildAnchors(world, { 'Congo (Kinshasa)': 'CD' });
  assert.deepEqual(anchors.CD, [5, 5]);
  const { placed, unplaced } = placePoints([point('a', '2026-10-27'), point('b', '2026-10-27'), point('c', '2026-10-27', { country_code: 'ZZ' })], anchors);
  assert.equal(placed.length, 2);
  assert.deepEqual([placed[0].lng, placed[0].lat], [5, 5]);
  assert.notDeepEqual([placed[1].lng, placed[1].lat], [5, 5]);
  assert.deepEqual(unplaced.map(item => item.alert_id), ['c']);
});
