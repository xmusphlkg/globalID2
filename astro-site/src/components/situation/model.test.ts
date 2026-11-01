import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { ageDays, buildAnchors, buildGraph, filterPoints, placePoints, recencyOf, sortPoints } from './model.ts';

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

test('exported coordinates win over the country anchor and do not use its fan-out', () => {
  const anchors = { CD: [5, 5] as [number, number] };
  const rows = [
    point('a', '2026-10-27', { lat: 1.57, lng: 30.25 }),
    point('b', '2026-10-27'),
    point('c', '2026-10-27', { lat: 1.57, lng: 30.25 }),
  ];
  const { placed } = placePoints(rows, anchors);
  const byId = Object.fromEntries(placed.map(item => [item.point.alert_id, item]));
  assert.deepEqual([byId.a.lng, byId.a.lat], [30.25, 1.57]);
  assert.deepEqual([byId.b.lng, byId.b.lat], [5, 5]);
  assert.notDeepEqual([byId.c.lng, byId.c.lat], [30.25, 1.57]);
  assert.ok(Math.abs(byId.c.lng - 30.25) < 1 && Math.abs(byId.c.lat - 1.57) < 1);
});

test('timeline graph packs columns: a country holds one only while it is active, and lines bend left when one ends', () => {
  const row = (id: string, day: string, code: string) => ({ alert_id: id, published_date: day, country_code: code });
  const { lanes, rows, width } = buildGraph([
    row('a', '2026-10-01', 'KE'),
    row('b', '2026-10-27', 'CD'),
    row('c', '2026-10-20', 'KE'),
    row('d', '2026-10-10', 'CD'),
    row('e', '2026-10-15', 'NG'),
  ], 'KE');
  assert.deepEqual(rows.map(item => item.alert.alert_id), ['b', 'c', 'e', 'd', 'a']);
  assert.deepEqual(lanes.map(lane => lane.key), ['KE', 'CD', 'NG']);
  assert.equal(new Set(lanes.map(lane => lane.color)).size, 3);
  assert.deepEqual(rows.map(item => item.node), [0, 1, 2, 0, 0]);
  assert.deepEqual(rows[0].lines, [{ lane: 1, from: 0, to: 0, part: 'down' }]);
  assert.deepEqual(rows[2].lines, [
    { lane: 1, from: 0, to: 0, part: 'through' },
    { lane: 0, from: 1, to: 1, part: 'through' },
  ]);
  assert.deepEqual(rows[3].lines, [
    { lane: 0, from: 1, to: 0, part: 'through' },
    { lane: 1, from: 0, to: 0, part: 'up' },
  ]);
  assert.deepEqual(rows[4].lines, [{ lane: 0, from: 0, to: 0, part: 'up' }]);
  assert.equal(width, 3);
});

test('timeline graph stays one column wide when countries never overlap', () => {
  const day = (value: number) => `2026-10-${String(value).padStart(2, '0')}`;
  const alerts = ['A1', 'B2', 'C3'].flatMap((code, index) => [
    { published_date: day(20 - index * 5), country_code: code },
    { published_date: day(19 - index * 5), country_code: code },
  ]);
  const { rows, width } = buildGraph(alerts);
  assert.equal(width, 1);
  assert.ok(rows.every(item => item.node === 0));
});

test('timeline graph folds the quietest countries into one shared grey lane', () => {
  const codes = ['A1', 'B2', 'C3', 'D4', 'E5', 'F6', 'G7'];
  const alerts = codes.map((code, index) => ({ published_date: `2026-10-${String(20 - index).padStart(2, '0')}`, country_code: code }));
  const { lanes, rows } = buildGraph(alerts);
  assert.equal(lanes.length, 6);
  assert.equal(lanes[5].other, true);
  assert.equal(rows[5].lane, 5);
  assert.equal(rows[6].lane, 5);
  assert.deepEqual(rows[5].lines, [{ lane: 5, from: 0, to: 0, part: 'down' }]);
  assert.deepEqual(rows[6].lines, [{ lane: 5, from: 0, to: 0, part: 'up' }]);
});
