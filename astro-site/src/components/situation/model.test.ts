import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { WORLD_CENTER, ageDays, buildAnchors, buildGraph, deduplicateAlerts, deduplicatePoints, filterPoints, fitView, latestMatchingPlaces, placePoints, recencyOf, sortPoints, situationPlaceKey, visibleSituationPoints } from './model.ts';

const AS_OF = '2026-10-28';
const point = (id: string, day: string, extra: Record<string, unknown> = {}) =>
  ({ alert_id: id, published_date: day, country_code: 'CD', disease_id: 'D050', disease_label: 'Ebola', source_id: 'who_don', ...extra }) as any;

test('homepage and map default counts use the same 30-day window and deduplication', () => {
  const recent = point('a', '2026-10-27', { outbreak_id: 'OB-1', admin_area: 'Goma', status: 'ongoing' });
  const duplicate = { ...recent, alert_id: 'b', source_id: 'ecdc_cdtr' };
  const old = point('old', '2026-08-01');
  const future = point('future', '2026-11-01');
  assert.deepEqual(visibleSituationPoints([old, duplicate, recent, future], AS_OF).map(item => item.alert_id), ['a']);
});

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

test('time windows include the boundary but exclude future and invalid dates', () => {
  const rows = [point('boundary', '2026-10-21'), point('old', '2026-10-20'), point('future', '2026-10-29'), point('invalid', 'bad')];
  const filters = { disease: '', country: '', source: '', window: '7' } as const;
  assert.deepEqual(filterPoints(rows, filters, AS_OF).map(row => row.alert_id), ['boundary']);
  assert.deepEqual(filterPoints(rows, { ...filters, window: 'all' }, AS_OF).map(row => row.alert_id), ['boundary', 'old']);
});

const alert = (id: string, extra = {}) => point(id, AS_OF, {
  outbreak_id: 'OB-1', notice_id: `notice-${id}`, admin_area: 'Nairobi',
  status: 'ongoing', cases: 6, deaths: 0, count_basis: 'confirmed', as_of: AS_OF,
  quote: 'Six cases reported.', url: `https://example.org/${id}`, ...extra,
});

test('repeated facts from different feeds form one event and retain both sources', () => {
  const first = alert('a');
  const second = alert('b', { source_id: 'ecdc_cdtr', admin_area: ' NAIROBI  ' });
  const groups = deduplicateAlerts([first, second, first], 'b');
  assert.equal(groups.length, 1);
  assert.equal(groups[0].alert_id, 'b');
  assert.deepEqual(groups[0].reports.map(row => row.alert_id), ['a', 'b']);
  assert.equal(first.reports, undefined);
  const filtered = filterPoints([first, second], { country: '', disease: '', source: 'ecdc_cdtr', window: '30' }, AS_OF);
  assert.deepEqual(deduplicateAlerts(filtered)[0].reports.map(row => row.alert_id), ['b']);
});

test('distinct updates, places, outbreaks and count bases never collapse', () => {
  const rows = [
    alert('a'), alert('b', { cases: 7 }), alert('c', { deaths: 1 }),
    alert('e', { as_of: '2026-10-27' }),
    alert('f', { country_code: 'KE' }), alert('g', { admin_area: 'Mombasa' }),
    alert('h', { outbreak_id: 'OB-2' }), alert('i', { count_basis: 'suspected' }),
    alert('j', { status: 'ended' }), alert('k', { disease_id: 'D999' }),
  ];
  assert.equal(deduplicateAlerts(rows).length, rows.length);
});

test('publication dates do not split reports of the same dated figures', () => {
  const reports = [alert('a'), alert('b', { published_date: '2026-10-27', source_id: 'ecdc_cdtr' })];
  const [group] = deduplicateAlerts(reports, 'b');
  assert.equal(deduplicateAlerts(reports).length, 1);
  assert.equal(group.alert_id, 'a', 'an older deep-linked report must not replace the latest facts');
  assert.equal(group.reports.length, 2);
});

// The Saudi Arabia MERS example: ECDC monthly events, WHO DON, and ECDC CDTR
// repeat the same year-to-date totals with different publication/cut-off dates.
const mersReports = [
  alert('e0ed903256a8d578', { source_id: 'ecdc_events', published_date: '2026-11-10', as_of: '2026-11-09', cases: 8, deaths: 1, count_basis: 'total', quote: 'Since the beginning of 2026, and as of 9 November 2026, eight MERS cases, including one fatality, have been reported in Saudi Arabia.' }),
  alert('2116ee479cc76e6d', { source_id: 'ecdc_events', published_date: '2026-10-19', as_of: '2026-10-19', cases: 8, deaths: 1, quote: 'Since the beginning of 2026, and as of 19 October 2026, eight MERS cases, including one fatality, have been reported in Saudi Arabia.' }),
  alert('461af4f262285453', { published_date: '2026-10-15', as_of: '2026-10-05', cases: 8, deaths: 1, quote: 'Between 1 January and 5 October 2026, the Kingdom of Saudi Arabia reported eight laboratory-confirmed cases of Middle East Respiratory Syndrome Coronavirus (MERS-CoV) infection, including one death.' }),
  alert('5d32bf2799447655', { source_id: 'ecdc_cdtr', published_date: '2026-10-02', as_of: '2026-09-29', cases: 2, deaths: 1, count_basis: 'total', quote: 'Since the beginning of 2026, and as of 29 September 2026, two MERS cases, including one fatality, have been reported in Saudi Arabia.' }),
  alert('c9441a3b5a30704e', { source_id: 'ecdc_events', published_date: '2026-09-30', as_of: '2026-09-29', cases: 2, deaths: 1, count_basis: 'total', quote: 'Since the beginning of 2026, and as of 29 September 2026, two MERS cases, including one fatality, have been reported in Saudi Arabia.' }),
  alert('1142153f5438b77c', { source_id: 'ecdc_events', published_date: '2026-09-01', as_of: '2026-08-31', cases: 2, deaths: 1, quote: 'Since the beginning of 2026, and as of 31 August 2026, two MERS cases, including one fatality, have been reported in Saudi Arabia.' }),
].map(report => ({ ...report, outbreak_id: 'OB-8c592ec14dae', disease_id: 'D049', country_code: 'SA', admin_area: null }));

test('MERS Saudi Arabia annual totals form two cards with all six source reports', () => {
  const filters = { country: 'SA', disease: 'D049', source: '', window: '90' } as const;
  const groups = deduplicateAlerts(filterPoints(mersReports, filters, '2026-11-15'), 'e0ed903256a8d578');
  assert.deepEqual(groups.map(group => [group.cases, group.deaths, group.reports.length]), [[8, 1, 3], [2, 1, 3]]);
  assert.deepEqual(groups.map(group => group.alert_id), ['e0ed903256a8d578', '5d32bf2799447655']);
  const sourceOnly = deduplicateAlerts(filterPoints(mersReports, { ...filters, source: 'who_don' }, '2026-11-15'));
  assert.equal(sourceOnly.length, 1);
  assert.deepEqual(sourceOnly[0].reports.map(report => report.alert_id), ['461af4f262285453']);
  const recentOnly = deduplicateAlerts(filterPoints(mersReports, { ...filters, window: '7' }, '2026-11-15'));
  assert.equal(recentOnly[0].reports.length, 1);
});

test('annual grouping separates years, suspected counts and different reporting periods', () => {
  const first = mersReports[0];
  const variants = [
    first,
    { ...first, alert_id: 'prior-year', quote: first.quote.replaceAll('2026', '2025'), published_date: '2025-11-10', as_of: '2025-11-09' },
    { ...first, alert_id: 'suspected', count_basis: 'suspected' },
    { ...first, alert_id: 'probable', count_basis: 'probable' },
    { ...first, alert_id: 'daily', quote: 'Eight MERS cases, including one fatality, reported on 9 November 2026.' },
  ];
  assert.equal(deduplicateAlerts(variants as any).length, variants.length);
});

test('New Zealand is a place name, while new laboratory-confirmed cases remain incident evidence', () => {
  const first = alert('a', { country_code: 'NZ', as_of: '2026-10-27', count_basis: 'total', quote: 'Since the beginning of 2026, New Zealand reported six cases.' });
  const repeat = alert('b', { country_code: 'NZ', quote: 'Since the beginning of 2026, six cases have been reported in New Zealand.' });
  const groups = deduplicateAlerts([first, repeat]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].reports.length, 2);

  const mixed = alert('mixed', { country_code: 'NZ', quote: 'Since the beginning of 2026, six cases included two new laboratory-confirmed cases.' });
  assert.equal(deduplicateAlerts([first, mixed]).length, 2);

  const incident = { as_of: '2026-10-07', quote: 'Six new laboratory-confirmed cases between 1 October and 7 October 2026.' };
  const incidentReports = [alert('c', incident), alert('d', { ...incident, published_date: '2026-10-27' })];
  assert.equal(deduplicateAlerts(incidentReports).length, 1);
});

test('reports without figures need matching evidence, not just matching empty fields', () => {
  const empty = { cases: null, deaths: null, quote: null };
  assert.equal(deduplicateAlerts([alert('a', empty), alert('b', empty)]).length, 2);
  assert.equal(deduplicateAlerts([alert('a', { ...empty, quote: 'An outbreak.' }), alert('b', { ...empty, quote: 'An outbreak.' })]).length, 1);
});

test('map points collapse repeated place reports after source filtering', () => {
  const rows = [alert('a'), alert('b', { source_id: 'ecdc_cdtr' }), alert('c', { outbreak_id: 'OB-2' })];
  assert.deepEqual(deduplicatePoints(rows).map(row => row.alert_id), ['a', 'c']);
  const filtered = filterPoints(rows, { country: '', disease: '', source: 'ecdc_cdtr', window: '30' }, AS_OF);
  assert.deepEqual(deduplicatePoints(filtered).map(row => row.alert_id), ['b']);
});

test('full history filtering keeps old channels and reduces each outbreak place to its newest matching marker', () => {
  const latest = alert('latest', { published_date: '2026-10-27', source_id: 'ecdc_cdtr' });
  const historical = alert('historical', { published_date: '2026-10-20', source_id: 'who_don', admin_area: ' NAIROBI ' });
  const sameSourceOlder = alert('older', { published_date: '2026-10-01', source_id: 'who_don' });
  const nearby = alert('nearby', { published_date: '2026-10-21', admin_area: 'Mombasa' });
  const rows = [historical, sameSourceOlder, latest, nearby];
  const base = { disease: '', country: '', source: '', window: 'all' } as const;
  assert.deepEqual(latestMatchingPlaces(rows, AS_OF, base).map(item => item.alert_id), ['latest', 'nearby']);
  assert.deepEqual(latestMatchingPlaces(rows, AS_OF, { ...base, source: 'who_don' }).map(item => item.alert_id), ['nearby', 'historical']);
  assert.deepEqual(latestMatchingPlaces(rows, AS_OF, { ...base, window: '7', source: 'who_don' }).map(item => item.alert_id), ['nearby']);
  assert.equal(situationPlaceKey(historical), situationPlaceKey(latest));
  assert.notEqual(situationPlaceKey(historical), situationPlaceKey(nearby));
});

test('structured cumulative data merge only across the same start, figures and literal count basis', () => {
  const first = alert('a', { as_of: '2026-10-27', reporting_period: { kind: 'cumulative', start: '2026-03-01', end: '2026-10-27' } });
  const repeat = alert('b', { published_date: '2026-10-27', as_of: '2026-10-26', reporting_period: { kind: 'cumulative', start: '2026-03-01', end: '2026-10-26' } });
  const otherStart = alert('c', { reporting_period: { kind: 'cumulative', start: '2026-04-01', end: AS_OF } });
  const total = alert('d', { as_of: first.as_of, count_basis: 'total', reporting_period: first.reporting_period });
  const groups = deduplicateAlerts([first, repeat, otherStart, total]);
  assert.equal(groups.length, 3);
  assert.deepEqual(groups[0].reports.map(row => row.alert_id), ['a', 'b']);
});

test('structured incident data require the same complete interval and take precedence over annual prose', () => {
  const common = { kind: 'incident', start: '2026-10-01', end: '2026-10-07' };
  const first = alert('a', { as_of: common.end, quote: mersReports[0].quote, reporting_period: common });
  const repeat = alert('b', { as_of: common.end, reporting_period: common, published_date: '2026-10-27' });
  const following = alert('c', { as_of: '2026-10-14', reporting_period: { ...common, start: '2026-10-08', end: '2026-10-14' } });
  const cumulative = alert('d', { as_of: common.end, reporting_period: { ...common, kind: 'cumulative' } });
  assert.deepEqual(deduplicateAlerts([first, repeat, following, cumulative]).map(group => group.reports.length), [2, 1, 1]);
});

test('invalid structured dates and missing incident ends cannot silently use annual fallback', () => {
  const rows = [
    alert('a', { reporting_period: { kind: 'incident', start: '2026-10-01' } }),
    alert('b', { reporting_period: { kind: 'incident', start: '2026-10-01' } }),
    alert('c', { reporting_period: { kind: 'cumulative', start: '2026-02-30' } }),
    alert('d', { reporting_period: { kind: 'cumulative', start: AS_OF, end: '2026-10-01' } }),
  ];
  assert.equal(deduplicateAlerts(rows).length, 4);
});

test('a quoted incident interval blocks a conflicting annual summary', () => {
  const summary = 'Since the beginning of 2026, six cases were reported.';
  const first = alert('a', { as_of: '2026-10-07', quote: 'Six new cases between 1 October and 7 October 2026.', summary_en: summary });
  const repeat = alert('b', { as_of: '2026-10-07', quote: 'Six new cases between 1 October and 7 October 2026.', summary_en: summary, published_date: '2026-10-27' });
  const following = alert('c', { as_of: '2026-10-14', quote: 'Six new cases between 8 October and 14 October 2026.', summary_en: summary });
  const cumulative = alert('d', { quote: summary, summary_en: summary });
  assert.deepEqual(deduplicateAlerts([first, repeat, following, cumulative]).map(group => group.reports.length), [2, 1, 1]);
});

test('ambiguous periods and conflicting quoted years do not combine with annual totals', () => {
  const first = mersReports[0];
  const rows = [
    first,
    { ...first, alert_id: 'conflict', quote: 'Since the beginning of 2025 and as of 9 November 2026, eight cases were reported.' },
    { ...first, alert_id: 'mixed', quote: 'Since the beginning of 2026 there were eight cases including six new cases this week.' },
    { ...first, alert_id: 'last-week', quote: 'Eight cases were reported last week.', summary_en: first.quote },
    { ...first, alert_id: 'this-week', quote: 'Eight cases were reported this week.', summary_en: first.quote },
  ];
  assert.equal(deduplicateAlerts(rows as any).length, rows.length);
});

test('explicit annual start alone does not equate total, confirmed, suspected or probable bases', () => {
  const period = { kind: 'cumulative', start: '2026-01-01', end: AS_OF };
  const rows = ['total', 'confirmed', 'suspected', 'probable'].map((count_basis, index) => alert(String(index), { reporting_period: period, count_basis }));
  assert.equal(deduplicateAlerts(rows).length, 4);
  const proven = rows.slice(0, 2).map(row => ({ ...row, quote: 'Since the beginning of 2026, six cases were reported.' }));
  assert.equal(deduplicateAlerts(proven).length, 1);
  const nextYear = proven.map(row => ({ ...row, published_date: '2027-01-08', as_of: '2027-01-07', reporting_period: { ...period, end: '2027-01-07' } }));
  assert.equal(deduplicateAlerts(nextYear).length, 2, 'a cumulative interval spanning years is not proof of an annual count basis');
});

test('period dates beyond publication or inconsistent with the count cutoff stay individual', () => {
  const base = alert('a', { reporting_period: { kind: 'cumulative', start: '2026-01-01', end: AS_OF } });
  const rows = [
    base,
    { ...base, alert_id: 'future-start', reporting_period: { kind: 'cumulative', start: '2027-01-01' } },
    { ...base, alert_id: 'future-end', reporting_period: { kind: 'cumulative', start: '2026-01-01', end: '2026-11-01' } },
    { ...base, alert_id: 'cutoff-mismatch', reporting_period: { kind: 'cumulative', start: '2026-01-01', end: '2026-10-27' } },
    { ...base, alert_id: 'prior-cutoff', as_of: '2025-12-31' },
    { ...base, alert_id: 'future-cutoff', as_of: '2026-11-01' },
  ];
  assert.equal(deduplicateAlerts(rows as any).length, rows.length);
  const futureAnnual = [alert('x', { quote: 'Since the beginning of 2027, six cases were reported.' }), alert('y', { quote: 'Since the beginning of 2027, six cases were reported.' })];
  assert.equal(deduplicateAlerts(futureAnnual).length, 2);
});

test('multiple incident intervals and undated month evidence cannot use an annual summary', () => {
  const summary_en = 'Since the beginning of 2026, six cases were reported.';
  const rows = [
    alert('a', { summary_en, quote: 'Six new cases from 2026-10-01 to 2026-10-07 and from 2026-10-08 to 2026-10-14.' }),
    alert('b', { summary_en, quote: 'Six new cases from 2026-10-01 to 2026-10-07.' }),
    alert('c', { summary_en, quote: 'Six cases reported during October.' }),
    alert('d', { summary_en, quote: summary_en }),
  ];
  assert.equal(deduplicateAlerts(rows).length, rows.length);
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

test('fitView falls back to the world with no points or no size', () => {
  assert.deepEqual(fitView([], 800, 600), { center: WORLD_CENTER, zoom: 1 });
  assert.deepEqual(fitView([{ lng: 10, lat: 10 }], 0, 600), { center: WORLD_CENTER, zoom: 1 });
});

test('fitView centres a single place and caps the zoom', () => {
  const view = fitView([{ lng: 36.8, lat: -1.3 }], 800, 600);
  assert.deepEqual(view.center, [36.8, -1.3]);
  assert.ok(view.zoom > 1 && view.zoom <= 8);
});

test('fitView zooms out further for a wider spread and frames its middle', () => {
  const near = fitView([{ lng: 30, lat: 0 }, { lng: 40, lat: 5 }], 800, 600);
  const far = fitView([{ lng: -10, lat: 0 }, { lng: 60, lat: 40 }], 800, 600);
  assert.deepEqual(far.center, [25, 20]);
  assert.ok(far.zoom < near.zoom);
});

test('fitView returns the world view for global spreads, including across the dateline', () => {
  assert.deepEqual(fitView([{ lng: -100, lat: 0 }, { lng: 100, lat: 0 }], 800, 600), { center: WORLD_CENTER, zoom: 1 });
  assert.deepEqual(fitView([{ lng: 178, lat: -17 }, { lng: -178, lat: -17 }], 800, 600), { center: WORLD_CENTER, zoom: 1 });
});
