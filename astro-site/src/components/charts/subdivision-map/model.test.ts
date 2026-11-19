import test from 'node:test';
import assert from 'node:assert/strict';
import { sharedScale } from './model.ts';
import { prepareMapDataset, resultsFor, aggregateRegion, sharedCeiling, restoreWindow, csvFor, addDays, expectedPeriods, comparisonWindow, presetWindow, trendFor, dateBounds, defaultWindow, type DiseaseDataset, type MapCatalog, type PopulationDataset, type MapWindow, type Population } from './model.ts';

const region = { code: 'AU-NSW', name_en: 'New South Wales' };
const window: MapWindow = { from: '2024-01-01', to: '2024-02-29', metric: 'rate', view: 'monthly' };
const dataset: DiseaseDataset = { schema_version: 1, country_code: 'AU', disease_id: 'D028', labels: { en: 'Pertussis' }, years: [2024, 2025], cadence: 'monthly', comparison_key: 'same', case_basis: {}, regions: { 'AU-NSW': { comparison_key: 'same', data_layer: 'series_registry', semantics: { metric: 'case_notifications', definition: 'official-v1', reporting_basis: 'notification', time_basis: 'notification_month', source_series: ['SER_TEST'] }, points: [['2024-01-01', 10, 'observed'], ['2024-02-01', 20, 'observed'], ['2025-01-01', 10, 'observed'], ['2025-02-01', 20, 'observed']] } } };
const pop = (year: number, value: number): Population => ({ year, value, source: 'ABS', reference_date: `${year}-06-30`, basis: 'june_estimated_resident_population', source_url: 'https://www.abs.gov.au/population', method: 'same_calendar_year_no_interpolation', version: `snapshot${year}` });
const populations: PopulationDataset = { schema_version: 1, country_code: 'AU', regions: { 'AU-NSW': { '2024': pop(2024, 100000), '2025': pop(2025, 200000) } } };
const catalog = { regions: [region], boundary: { version: 'boundary1' } } as MapCatalog;
const close = (value: number | null, expected: number) => assert.ok(value !== null && Math.abs(value - expected) < 1e-10);

test('aggregates numerator once, links the exact year population, and never annualizes', () => {
  close(aggregateRegion(region, dataset, populations, window).rate, 30);
  close(aggregateRegion(region, dataset, populations, { ...window, from: '2025-01-01', to: '2025-02-28' }).rate, 15);
});
test('missing same-year population does not borrow a previous year or WPP; cases remain available', () => {
  const local = structuredClone(populations); delete local.regions['AU-NSW']['2024'];
  const result = aggregateRegion(region, dataset, local, window);
  assert.equal(result.rate, null); assert.equal(result.cases, 30);
  assert.equal(aggregateRegion(region, dataset, local, { ...window, metric: 'cases' }).value, 30);
  local.regions['AU-NSW']['2024'] = { ...populations.regions['AU-NSW']['2024'], source: 'WPP' };
  assert.equal(aggregateRegion(region, dataset, local, window).rate, null);
});
test('true zero differs from suppression and a missing report period', () => {
  const local = structuredClone(dataset), january = { ...window, to: '2024-01-31' };
  local.regions['AU-NSW'].points[0][1] = 0;
  assert.equal(aggregateRegion(region, local, populations, january).rate, 0);
  local.regions['AU-NSW'].points[0] = ['2024-01-01', null, 'suppressed'];
  assert.equal(aggregateRegion(region, local, populations, window).cases, null);
  assert.ok(aggregateRegion(region, local, populations, window).reasons.includes('suppressed'));
  local.regions['AU-NSW'].points.shift();
  assert.ok(aggregateRegion(region, local, populations, window).reasons.includes('missing_period'));
});
test('raw source points remain numeric but are marked unverified', () => {
  const local = structuredClone(dataset);
  local.regions['AU-NSW'].points = [['2024-01-01', 10, 'raw'], ['2024-02-01', 20, 'raw'], ['2025-01-01', 1, 'observed'], ['2025-02-01', 1, 'observed']];
  const result = aggregateRegion(region, local, populations, window);
  assert.equal(result.cases, 30);
  assert.equal(result.unverified, true);
  assert.equal(result.provisional, false);
  assert.match(csvFor([result], window, local, catalog), /unverified/);
});
test('duplicates and incompatible definitions cannot produce a complete value', () => {
  const local = structuredClone(dataset);
  local.regions['AU-NSW'].points.push(['2024-01-01', 100, 'observed']);
  assert.equal(aggregateRegion(region, local, populations, window).rate, null);
  assert.ok(aggregateRegion(region, local, populations, window).reasons.includes('overlap'));
  local.regions['AU-NSW'].comparison_key = 'different';
  assert.ok(aggregateRegion(region, local, populations, window).reasons.includes('not_comparable'));
});
test('same range shares scale between years and downloads denominator and comparison provenance', () => {
  assert.equal(sharedCeiling(catalog, dataset, populations, window), sharedCeiling(catalog, dataset, populations, { ...window, from: '2025-01-01', to: '2025-02-28' }));
  const otherWindow = { ...window, from: '2025-01-01', to: '2025-02-28' };
  const csv = csvFor([aggregateRegion(region, dataset, populations, window)], window, dataset, catalog, [aggregateRegion(region, dataset, populations, otherWindow)], otherWindow);
  for (const value of ['population_reference_date', 'ABS', '2024-06-30', 'https://www.abs.gov.au/population', 'snapshot2024', 'official-v1', 'boundary1', 'comparison_annual_denominators', '2025-02-28']) assert.ok(csv.includes(value));
});
test('date URL restoration validates dates, chronology and years, and keeps legacy links', () => {
  for (const query of ['mapFrom=2024-03-01&mapTo=2024-02-01', 'mapFrom=2030-01-01', 'mapFrom=2024-02-30', 'mapMetric=deaths', 'mapView=daily', 'mapStart=12&mapEnd=1&mapYear=2024']) assert.deepEqual(restoreWindow(new URLSearchParams(query), window, [2024, 2025]), window);
  assert.deepEqual(restoreWindow(new URLSearchParams('mapYear=2024&mapStart=1&mapEnd=2'), window, [2024, 2025]), window);
  assert.throws(() => aggregateRegion(region, dataset, populations, { ...window, from: '2024-05-01' }), RangeError);
});
test('cross-year cumulative rates use each year population and expose every contribution', () => {
  const local = structuredClone(dataset);
  local.regions['AU-NSW'].points = [['2024-12-01', 10, 'observed'], ['2025-01-01', 20, 'observed']];
  const range = { ...window, from: '2024-12-01', to: '2025-01-31' };
  const result = aggregateRegion(region, local, populations, range);
  assert.equal(result.cases, 30); close(result.rate, 20);
  assert.equal(result.population, null);
  assert.deepEqual(result.annual.map(item => [item.year, item.population!.value, item.rate]), [[2024, 100000, 10], [2025, 200000, 10]]);
  const missing = structuredClone(populations); delete missing.regions['AU-NSW']['2025'];
  assert.equal(aggregateRegion(region, local, missing, range).rate, null);
  assert.equal(aggregateRegion(region, local, missing, range).cases, 30);
});
test('month, quarter, year and previous-period presets respect calendar boundaries including leap years', () => {
  assert.deepEqual(presetWindow(window, 2024, 'month', 2), { ...window, from: '2024-02-01', to: '2024-02-29' });
  const quarter = presetWindow(window, 2024, 'quarter', 1);
  assert.equal(quarter.to, '2024-03-31'); assert.equal(quarter.view, 'quarterly');
  assert.deepEqual(comparisonWindow(quarter, 'previous_period'), { ...quarter, from: '2023-10-01', to: '2023-12-31' });
  assert.deepEqual(comparisonWindow(presetWindow(window, 2024, 'month', 3), 'previous_period'), { ...window, from: '2024-02-01', to: '2024-02-29' });
  assert.equal(comparisonWindow({ ...window, from: '2024-02-29', to: '2024-03-01' }, 'previous_year')!.from, '2023-02-28');
  assert.equal(presetWindow(window, 2024, 'year').to, '2024-12-31');
  assert.equal(comparisonWindow(window, 'custom', { from: '2025-02-01', to: '2025-01-01' }), null);
});
test('monthly counts aggregate to quarters and years without manufacturing missing periods', () => {
  const local = structuredClone(dataset);
  local.regions['AU-NSW'].points = Array.from({ length: 12 }, (_, index) => [`2024-${String(index + 1).padStart(2, '0')}-01`, 10, 'observed']);
  const quarter = presetWindow(window, 2024, 'quarter', 1), year = presetWindow(window, 2024, 'year');
  assert.equal(aggregateRegion(region, local, populations, quarter).cases, 30);
  close(trendFor(region, local, populations, quarter)[0].value, 30);
  assert.equal(aggregateRegion(region, local, populations, year).cases, 120);
  close(trendFor(region, local, populations, year)[0].value, 120);
  local.regions['AU-NSW'].points.splice(1, 1);
  assert.equal(aggregateRegion(region, local, populations, quarter).cases, null);
});
function weeklyDataset(): DiseaseDataset {
  const local = structuredClone(dataset); local.cadence = 'weekly';
  const source = local.regions['AU-NSW'];
  const dates = Array.from({ length: 5 }, (_, index) => addDays('2024-12-30', index * 7));
  source.points = dates.map(date => [addDays(date, 6), 10, 'observed']);
  source.periods = dates.map(start => ({ start, end_exclusive: addDays(start, 7), reporting_year: 2025 }));
  return local;
}
test('weekly reports select full source weeks only and expose the actual date range', () => {
  const local = weeklyDataset(), range = { ...window, from: '2025-01-01', to: '2025-01-31' };
  const result = aggregateRegion(region, local, populations, range);
  assert.equal(result.cases, 30); assert.equal(result.expected, 3);
  assert.equal(result.from, '2025-01-06'); assert.equal(result.to, '2025-01-26');
  assert.equal(aggregateRegion(region, local, populations, { ...range, to: '2025-01-03' }).cases, null);
  assert.ok(aggregateRegion(region, local, populations, { ...range, to: '2025-01-03' }).reasons.includes('no_complete_period'));
});
test('a week crossing New Year uses the verified reporting year, not its start-date year', () => {
  const result = aggregateRegion(region, weeklyDataset(), populations, { ...window, from: '2024-12-30', to: '2025-01-05' });
  assert.equal(result.annual[0].year, 2025); close(result.rate, 5);
});
test('missing weekly observations cannot be replaced with zero or removed from expected coverage', () => {
  const local = weeklyDataset(); local.regions['AU-NSW'].points.splice(2, 1); local.regions['AU-NSW'].periods!.splice(2, 1);
  const result = aggregateRegion(region, local, populations, { ...window, from: '2025-01-01', to: '2025-01-31' });
  assert.equal(result.expected, 3); assert.equal(result.observed, 2); assert.equal(result.cases, null);
});
test('quarterly and annual source observations remain indivisible', () => {
  const local = structuredClone(dataset); local.cadence = 'quarterly';
  local.regions['AU-NSW'].points = [['2024-01-01', 100, 'observed']];
  local.regions['AU-NSW'].periods = [{ start: '2024-01-01', end_exclusive: '2024-04-01', reporting_year: 2024 }];
  assert.equal(aggregateRegion(region, local, populations, window).cases, null);
  assert.equal(aggregateRegion(region, local, populations, { ...window, to: '2024-03-31' }).cases, 100);
  local.cadence = 'annual'; local.regions['AU-NSW'].periods[0].end_exclusive = '2025-01-01';
  assert.equal(aggregateRegion(region, local, populations, { ...window, to: '2024-03-31' }).cases, null);
  assert.equal(aggregateRegion(region, local, populations, { ...window, to: '2024-12-31', view: 'annual' }).cases, 100);
});
test('unverified weekly intervals cannot silently acquire a calendar or population denominator', () => {
  const local = weeklyDataset(); delete local.regions['AU-NSW'].periods;
  assert.deepEqual(expectedPeriods(local, window), []);
  assert.equal(aggregateRegion(region, local, populations, window).cases, null);
});


test('source weeks at reporting-year boundaries survive date bounds and share URL restoration', () => {
  const local = weeklyDataset(); local.years = [2025];
  const range = { ...window, from: '2024-12-30', to: '2025-01-05', view: 'weekly' as const };
  assert.equal(dateBounds(local).from, '2024-12-30');
  assert.deepEqual(restoreWindow(new URLSearchParams('mapFrom=2024-12-30&mapTo=2025-01-05&mapView=weekly'), window, local.years, dateBounds(local)), range);
});
test('an entirely absent boundary week never guesses its reporting year from its start date', () => {
  const local = weeklyDataset();
  local.regions['AU-NSW'].points.shift(); local.regions['AU-NSW'].periods!.shift();
  const range = { ...window, from: '2024-12-30', to: '2025-01-05' };
  assert.equal(expectedPeriods(local, range)[0].reporting_year, null);
  assert.deepEqual(aggregateRegion(region, local, populations, range).annual, []);
  assert.equal(aggregateRegion(region, local, populations, range).rate, null);
});


test('an entirely missing week also invalidates annual trend aggregation instead of being discarded', () => {
  const local = weeklyDataset();
  local.regions['AU-NSW'].points.splice(2, 1); local.regions['AU-NSW'].periods!.splice(2, 1);
  const trend = trendFor(region, local, populations, { ...window, from: '2025-01-01', to: '2025-01-31', view: 'annual' });
  assert.equal(trend.length, 1); assert.equal(trend[0].label, '2025'); assert.equal(trend[0].value, null); assert.equal(trend[0].expected, 3);
});


test('a sparse source defaults to a complete recent quarter or month instead of inventing a full year', () => {
  const chosen = defaultWindow(catalog, dataset, populations);
  assert.equal(chosen.from, '2025-02-01'); assert.equal(chosen.to, '2025-02-28');
  assert.equal(chosen.metric, 'rate'); assert.equal(aggregateRegion(region, dataset, populations, chosen).expected, 1);
});

test('prepared publications preserve monthly, weekly, missing, suppressed and overlapping results', () => {
  const examples: Array<{ data: DiseaseDataset; population: PopulationDataset; range: MapWindow }> = [];
  for (const quality of ['observed', 'suppressed', 'provisional', 'incomplete']) {
    const data = structuredClone(dataset);
    data.regions[region.code].points[1][2] = quality;
    examples.push({ data, population: structuredClone(populations), range: { ...window } });
  }
  const duplicate = structuredClone(dataset);
  duplicate.regions[region.code].points.push(['2024-01-01', 100, 'observed']);
  examples.push({ data: duplicate, population: structuredClone(populations), range: { ...window } });
  const invalid = structuredClone(dataset);
  invalid.regions[region.code].points[0][0] = 'not-a-date';
  examples.push({ data: invalid, population: structuredClone(populations), range: { ...window } });
  for (const missingWeek of [false, true]) {
    const data = weeklyDataset();
    if (missingWeek) { data.regions[region.code].points.splice(2, 1); data.regions[region.code].periods!.splice(2, 1); }
    examples.push({ data, population: structuredClone(populations), range: { ...window, from: '2024-12-30', to: '2025-01-31', view: 'annual' } });
  }
  const missingPopulation = structuredClone(populations); delete missingPopulation.regions[region.code]['2025'];
  examples.push({ data: weeklyDataset(), population: missingPopulation, range: { ...window, from: '2024-12-30', to: '2025-01-31' } });
  for (const cadence of ['quarterly', 'annual'] as const) for (const clipped of [false, true]) {
    const data = structuredClone(dataset); data.cadence = cadence; data.years = [2024];
    data.regions[region.code].points = [['2024-01-01', 100, 'observed']];
    data.regions[region.code].periods = [{ start: '2024-01-01', end_exclusive: cadence === 'annual' ? '2025-01-01' : '2024-04-01', reporting_year: 2024 }];
    examples.push({ data, population: structuredClone(populations), range: { ...window,
      to: clipped ? '2024-02-29' : cadence === 'annual' ? '2024-12-31' : '2024-03-31', view: cadence } });
  }
  const crossYear = structuredClone(dataset);
  crossYear.regions[region.code].points = [['2024-12-01', 10, 'observed'], ['2025-01-01', 20, 'observed']];
  examples.push({ data: crossYear, population: structuredClone(populations), range: { ...window, from: '2024-12-01', to: '2025-01-31' } });
  for (const { data, population, range } of examples) {
    const expected = resultsFor(catalog, data, population, range);
    const trend = trendFor(region, data, population, range);
    const ceiling = sharedCeiling(catalog, data, population, range);
    const bounds = dateBounds(data), periods = expectedPeriods(data, range);
    prepareMapDataset(data);
    assert.deepEqual(resultsFor(catalog, data, population, range), expected);
    assert.deepEqual(trendFor(region, data, population, range), trend);
    assert.equal(sharedCeiling(catalog, data, population, range), ceiling);
    assert.deepEqual(dateBounds(data), bounds);
    assert.deepEqual(expectedPeriods(data, range), periods);
  }
});

test('new disease and population publication objects invalidate cached rates and shared scales', () => {
  const data = prepareMapDataset(structuredClone(dataset)), population = structuredClone(populations);
  close(resultsFor(catalog, data, population, window)[0].rate, 30);
  assert.equal(sharedCeiling(catalog, data, population, window), 30);
  const revisedPopulation = structuredClone(population);
  revisedPopulation.regions[region.code]['2024'] = pop(2024, 50000);
  close(resultsFor(catalog, data, revisedPopulation, window)[0].rate, 60);
  assert.equal(sharedCeiling(catalog, data, revisedPopulation, window), 60);
  const revision = structuredClone(data);
  revision.regions[region.code].points[0][1] = 40;
  prepareMapDataset(revision);
  close(resultsFor(catalog, revision, population, window)[0].rate, 60);
  assert.equal(sharedCeiling(catalog, revision, population, window), 60);
  close(resultsFor(catalog, data, population, window)[0].rate, 30);
  assert.throws(() => { data.regions[region.code].points[0][1] = 999; }, TypeError);
});

test('changing dates, years and trend grouping never rescans a prepared source history', () => {
  const data = weeklyDataset(), source = data.regions[region.code], periods = source.periods;
  let reads = 0;
  Object.defineProperty(source, 'periods', { configurable: true, get() { reads++; return periods; } });
  prepareMapDataset(data);
  reads = 0;
  const range = { ...window, from: '2025-01-01', to: '2025-01-31' };
  const expected = resultsFor(catalog, data, populations, range);
  for (const view of ['weekly', 'monthly', 'quarterly', 'annual'] as const) {
    const selected = { ...range, view };
    assert.deepEqual(resultsFor(catalog, data, populations, selected), expected);
    sharedCeiling(catalog, data, populations, selected);
    sharedScale(catalog, data, populations, selected);
    trendFor(region, data, populations, selected);
  }
  for (let year = 2000; year < 2080; year++) {
    const selected = { ...range, from: `${year}-01-01`, to: `${year}-01-31` };
    resultsFor(catalog, data, populations, selected);
    sharedCeiling(catalog, data, populations, selected);
    sharedScale(catalog, data, populations, selected);
  }
  assert.deepEqual(resultsFor(catalog, data, populations, range), expected);
  assert.equal(reads, 0, 'interaction should use the prepared period index, including after range-cache eviction');
});

test('shared continuous scales are stable across years and grouping and never double-weight a comparison', () => {
  const data = prepareMapDataset(structuredClone(dataset));
  const breaks = sharedScale(catalog, data, populations, window);
  const next = { ...window, from: '2025-01-01', to: '2025-02-28' };
  assert.deepEqual(sharedScale(catalog, data, populations, next), breaks);
  assert.deepEqual(sharedScale(catalog, data, populations, window, next), breaks);
  assert.deepEqual(sharedScale(catalog, data, populations, { ...window, view: 'annual' }), breaks);
  const revised = structuredClone(populations);
  revised.regions[region.code]['2024'] = pop(2024, 400000);
  assert.notDeepEqual(sharedScale(catalog, data, revised, window), breaks);
});
