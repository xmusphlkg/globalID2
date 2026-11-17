import { continuousScale, prettyBreaks } from './scale.ts';

export type Language = 'en' | 'zh' | 'fr';
export type Metric = 'rate' | 'cases';
export type Cadence = 'weekly' | 'monthly' | 'quarterly' | 'annual';
export type Comparison = 'none' | 'previous_year' | 'previous_period' | 'custom';
export interface Population {
  location_code?: string;
  year: number; value: number; source: string; reference_date: string;
  basis: string; source_url: string; method: string; version: string;
}
export interface Region { code: string; name_en: string; name_zh?: string; }
export interface MapCatalog {
  schema_version: number; country_code: string; expected_regions: number; cadence?: Cadence | 'mixed';
  regions: Region[];
  diseases: Array<{ id: string; labels: Record<string, string>; years: number[]; path: string; cadence?: Cadence }>;
  boundary: { path: string; source: string; source_url: string; license: string; license_url: string; version: string; historical_boundary_policy: string };
  population_path: string;
}
export interface PopulationDataset { schema_version: number; country_code: string; regions: Record<string, Record<string, Population>>; }
export interface ReportPeriod { start: string; end_exclusive: string; reporting_year: number | null; }
export interface RegionSeries {
  case_version?: string; source_url?: string; periods?: ReportPeriod[];
  points: Array<[string, number | null, string]>;
  comparison_key: string; data_layer: string;
  semantics: { metric: string; definition: string; reporting_basis: string; time_basis: string; source_series: string[]; source_labels?: string[] };
}
export interface DiseaseDataset {
  schema_version: number; country_code: string; disease_id: string; labels: Record<string, string>;
  years: number[]; cadence: Cadence; comparison_key: string;
  case_basis: Record<string, string>; regions: Record<string, RegionSeries>;
}
export interface MapWindow { from: string; to: string; metric: Metric; view: Cadence; }
export type MissingReason = 'not_covered' | 'not_comparable' | 'missing_period' | 'suppressed' | 'missing_population' | 'overlap' | 'incomplete' | 'no_complete_period' | 'invalid_period';
export interface AnnualContribution { year: number; cases: number | null; rate: number | null; population: Population | null; }
export interface RegionResult {
  region: Region; cases: number | null; rate: number | null; value: number | null;
  population: Population | null; annual: AnnualContribution[];
  observed: number; expected: number; from: string | null; to: string | null;
  reasons: MissingReason[]; provisional: boolean;
}
const day = 86400000;
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const stamp = (value: string) => Date.parse(`${value}T00:00:00Z`);
const iso = (value: number | Date) => new Date(value).toISOString().slice(0, 10);
export const addDays = (value: string, count: number) => iso(stamp(value) + count * day);
export const isDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && finite(stamp(value)) && iso(stamp(value)) === value;
export const cadenceRank: Record<Cadence, number> = { weekly: 0, monthly: 1, quarterly: 2, annual: 3 };
export function validWindow(window: MapWindow) {
  return isDate(window.from) && isDate(window.to) && window.from <= window.to
    && stamp(window.to) - stamp(window.from) < 100 * 366 * day
    && ['cases', 'rate'].includes(window.metric) && window.view in cadenceRank;
}
export function shiftYear(value: string, years: number) {
  const year = Number(value.slice(0, 4)) + years, month = Number(value.slice(5, 7));
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return `${year}-${String(month).padStart(2, '0')}-${String(Math.min(Number(value.slice(8, 10)), last)).padStart(2, '0')}`;
}
export function shiftWindowYear(window: MapWindow, years: number): MapWindow {
  const endExclusive = addDays(window.to, 1);
  return { ...window, from: shiftYear(window.from, years), to: endExclusive.endsWith('-01')
    ? addDays(shiftYear(endExclusive, years), -1) : shiftYear(window.to, years) };
}
export function comparisonWindow(window: MapWindow, mode: Comparison, custom?: { from: string; to: string }): MapWindow | null {
  if (mode === 'none') return null;
  if (mode === 'custom') return custom && validWindow({ ...window, ...custom }) ? { ...window, ...custom } : null;
  if (mode === 'previous_year') return shiftWindowYear(window, -1);
  const to = addDays(window.from, -1);
  // Preserve calendar months/quarters/years for their aligned presets.
  if (window.from.endsWith('-01') && addDays(window.to, 1).endsWith('-01')) {
    const months = (Number(addDays(window.to, 1).slice(0, 4)) - Number(window.from.slice(0, 4))) * 12 + Number(addDays(window.to, 1).slice(5, 7)) - Number(window.from.slice(5, 7));
    const from = iso(Date.UTC(Number(window.from.slice(0, 4)), Number(window.from.slice(5, 7)) - 1 - months, 1));
    return { ...window, from, to };
  }
  return { ...window, from: addDays(to, -(stamp(window.to) - stamp(window.from)) / day), to };
}
export function presetWindow(window: MapWindow, year: number, kind: 'month' | 'quarter' | 'year', index = 1): MapWindow {
  const month = kind === 'month' ? index : kind === 'quarter' ? (index - 1) * 3 + 1 : 1;
  const months = kind === 'month' ? 1 : kind === 'quarter' ? 3 : 12;
  return { ...window, from: iso(Date.UTC(year, month - 1, 1)), to: iso(Date.UTC(year, month - 1 + months, 0)), view: kind === 'year' ? 'annual' : kind === 'quarter' ? 'quarterly' : 'monthly' };
}
function monthEnd(start: string, months: number) {
  return iso(Date.UTC(Number(start.slice(0, 4)), Number(start.slice(5, 7)) - 1 + months, 1));
}
export function periodsFor(series: RegionSeries, cadence: Cadence): Array<ReportPeriod | null> {
  return parsePeriods(series, cadence, isDate);
}
function parsePeriods(series: RegionSeries, cadence: Cadence, validDate: typeof isDate): Array<ReportPeriod | null> {
  return series.points.map((point, index) => {
    const period = series.periods?.[index] ?? (cadence === 'monthly' && validDate(point[0]) && point[0].endsWith('-01')
      ? { start: point[0], end_exclusive: monthEnd(point[0], 1), reporting_year: Number(point[0].slice(0, 4)) } : null);
    if (!period || !validDate(period.start) || !validDate(period.end_exclusive) || period.start >= period.end_exclusive
      || typeof period.reporting_year !== 'number' || !Number.isInteger(period.reporting_year) || period.reporting_year < 1800 || period.reporting_year > 2200) return null;
    return period;
  });
}

interface IndexedPoint { period: ReportPeriod; value: number | null; quality: string; }
interface SeriesIndex {
  periods: Array<ReportPeriod | null>;
  lookup: Map<string, IndexedPoint[]>;
  invalid: boolean;
}
interface ProjectionCache { results: Map<string, RegionResult[]>; ceilings: Map<string, number>; }
interface DatasetIndex {
  sources: Map<RegionSeries, SeriesIndex>;
  known: Map<string, ReportPeriod>;
  anchor?: string;
  from?: string;
  to?: string;
  expected: Map<string, ReportPeriod[]>;
  projections: WeakMap<MapCatalog, WeakMap<PopulationDataset, ProjectionCache>>;
}

// Only explicitly prepared publication snapshots are memoized. Pure callers may
// still edit their inputs between calls; those callers always compute afresh.
const preparedDatasets = new WeakMap<DiseaseDataset, DatasetIndex>();
const CACHE_LIMIT = 64;
function remember<T>(cache: Map<string, T>, key: string, value: T): T {
  if (cache.size >= CACHE_LIMIT) cache.delete(cache.keys().next().value!);
  cache.set(key, value);
  return value;
}
function freezePublication<T>(value: T): T {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) freezePublication(child);
    Object.freeze(value);
  }
  return value;
}
function indexSeries(source: RegionSeries, cadence: Cadence, validDate = isDate): SeriesIndex {
  const periods = parsePeriods(source, cadence, validDate), lookup = new Map<string, IndexedPoint[]>();
  let invalid = false;
  periods.forEach((period, index) => {
    if (!period) { invalid = true; return; }
    const [, value, quality] = source.points[index];
    const point = { period, value, quality };
    const matches = lookup.get(period.start);
    if (matches) matches.push(point);
    else lookup.set(period.start, [point]);
  });
  return { periods, lookup, invalid };
}
/** Prepare one immutable published disease object, once per fetched generation.
 * Revisions must replace the dataset/population/catalogue objects. Weak keys
 * release old generations; bounded range caches cannot grow with every edit.
 */
export function prepareMapDataset(dataset: DiseaseDataset): DiseaseDataset {
  if (preparedDatasets.has(dataset)) return dataset;
  freezePublication(dataset);
  const sources = new Map<RegionSeries, SeriesIndex>(), known = new Map<string, ReportPeriod>();
  // Most regions share the same calendar dates; validate each distinct date
  // once while preparing this snapshot, without weakening date validation.
  const dates = new Map<string, boolean>();
  const validDate = (value: string) => {
    if (!dates.has(value)) dates.set(value, isDate(value));
    return dates.get(value)!;
  };
  let from: string | undefined, to: string | undefined;
  for (const source of Object.values(dataset.regions)) {
    const index = indexSeries(source, dataset.cadence, validDate);
    sources.set(source, index);
    for (const period of index.periods) if (period) {
      if (source.comparison_key === dataset.comparison_key) known.set(period.start, period);
      if (!from || period.start < from) from = period.start;
      if (!to || period.end_exclusive > to) to = period.end_exclusive;
    }
  }
  preparedDatasets.set(dataset, { sources, known, anchor: [...known.keys()].sort()[0], from,
    to: to ? addDays(to, -1) : undefined, expected: new Map(), projections: new WeakMap() });
  return dataset;
}
function projectionCache(catalog: MapCatalog, dataset: DiseaseDataset, populations: PopulationDataset): ProjectionCache | undefined {
  const prepared = preparedDatasets.get(dataset);
  if (!prepared) return undefined;
  let byPopulation = prepared.projections.get(catalog);
  if (!byPopulation) { byPopulation = new WeakMap(); prepared.projections.set(catalog, byPopulation); }
  let cache = byPopulation.get(populations);
  if (!cache) {
    // Population revisions arrive as new objects, just as disease revisions do.
    freezePublication(catalog); freezePublication(populations);
    cache = { results: new Map(), ceilings: new Map() }; byPopulation.set(populations, cache);
  }
  return cache;
}
const rangeKey = (window: MapWindow) => `${window.from}|${window.to}|${window.metric}`;

/** Expected full source periods, including periods absent from every region. */
export function expectedPeriods(dataset: DiseaseDataset, window: MapWindow): ReportPeriod[] {
  const prepared = preparedDatasets.get(dataset), key = `${window.from}|${window.to}`;
  const cached = prepared?.expected.get(key);
  if (cached) return cached;
  const endsBefore = addDays(window.to, 1);
  const known = prepared?.known ?? new Map<string, ReportPeriod>();
  if (!prepared) for (const source of Object.values(dataset.regions).filter(source => source.comparison_key === dataset.comparison_key)) for (const period of periodsFor(source, dataset.cadence)) if (period) known.set(period.start, period);
  let start: string;
  if (dataset.cadence === 'weekly') {
    const anchor = prepared?.anchor ?? [...known.keys()].sort()[0];
    if (!anchor) return [];
    start = addDays(anchor, Math.floor((stamp(window.from) - stamp(anchor)) / (7 * day)) * 7);
  } else {
    const year = Number(window.from.slice(0, 4)), month = Number(window.from.slice(5, 7));
    start = iso(Date.UTC(year, dataset.cadence === 'annual' ? 0 : dataset.cadence === 'quarterly' ? Math.floor((month - 1) / 3) * 3 : month - 1, 1));
  }
  const result: ReportPeriod[] = [];
  while (start < endsBefore) {
    const end = dataset.cadence === 'weekly' ? addDays(start, 7) : monthEnd(start, dataset.cadence === 'annual' ? 12 : dataset.cadence === 'quarterly' ? 3 : 1);
    if (start >= window.from && end <= endsBefore) result.push(known.get(start) ?? { start, end_exclusive: end, reporting_year: dataset.cadence === 'weekly' ? null : Number(start.slice(0, 4)) });
    start = end;
  }
  return prepared ? remember(prepared.expected, key, result) : result;
}
export function dateBounds(dataset: DiseaseDataset, years = dataset.years) {
  let from = `${Math.min(...years)}-01-01`, to = `${Math.max(...years)}-12-31`;
  const prepared = preparedDatasets.get(dataset);
  if (prepared) return { from: prepared.from && prepared.from < from ? prepared.from : from,
    to: prepared.to && prepared.to > to ? prepared.to : to };
  for (const source of Object.values(dataset.regions)) for (const period of periodsFor(source, dataset.cadence)) if (period) {
    if (period.start < from) from = period.start;
    const end = addDays(period.end_exclusive, -1);
    if (end > to) to = end;
  }
  return { from, to };
}
function localPopulation(region: Region, year: number, dataset: DiseaseDataset, populations: PopulationDataset): Population | null {
  const value = populations.regions[region.code]?.[String(year)];
  const sources: Record<string, string> = { CN: 'NBS', AU: 'ABS', BR: 'IBGE', US: 'Census', JP: 'StatisticsJapan', SE: 'SCB' };
  return value && value.year === year && finite(value.value) && value.value > 0 && value.reference_date.startsWith(`${year}-`)
    && value.source === sources[dataset.country_code] && (!value.location_code || value.location_code === region.code)
    && value.method === 'same_calendar_year_no_interpolation' ? value : null;
}
export function aggregateRegion(region: Region, dataset: DiseaseDataset, populations: PopulationDataset, window: MapWindow, expected = expectedPeriods(dataset, window)): RegionResult {
  if (!validWindow(window)) throw new RangeError('Invalid date range');
  const source = dataset.regions[region.code], reasons: MissingReason[] = [];
  if (!source) reasons.push('not_covered');
  else if (source.comparison_key !== dataset.comparison_key) reasons.push('not_comparable');
  if (!expected.length) reasons.push('no_complete_period');
  const indexed = source ? preparedDatasets.get(dataset)?.sources.get(source) ?? indexSeries(source, dataset.cadence) : undefined;
  if (indexed?.invalid) reasons.push('invalid_period');
  const annualCounts = new Map<number, number>();
  let observed = 0, provisional = false;
  for (const period of expected) {
    const matches = indexed?.lookup.get(period.start) ?? [];
    if (period.reporting_year !== null) annualCounts.set(period.reporting_year, annualCounts.get(period.reporting_year) ?? 0);
    if (!matches.length) { reasons.push('missing_period'); continue; }
    if (matches.length > 1) { reasons.push('overlap'); continue; }
    const point = matches[0];
    if (point.period.reporting_year === null) { reasons.push('invalid_period'); continue; }
    if (point.period.end_exclusive !== period.end_exclusive) { reasons.push('invalid_period'); continue; }
    if (['suppressed', 'missing_or_suppressed'].includes(point.quality)) { reasons.push('suppressed'); continue; }
    if (point.quality === 'missing' || !finite(point.value)) { reasons.push('missing_period'); continue; }
    if (!['observed', 'provisional'].includes(point.quality) || point.value < 0 || !Number.isInteger(point.value)) { reasons.push('incomplete'); continue; }
    observed++; provisional ||= point.quality === 'provisional';
    annualCounts.set(point.period.reporting_year, (annualCounts.get(point.period.reporting_year) ?? 0) + point.value);
  }
  const complete = reasons.length === 0;
  const annual = [...annualCounts].sort(([a], [b]) => a - b).map(([year, count]) => {
    const population = localPopulation(region, year, dataset, populations);
    if (!population) reasons.push('missing_population');
    return { year, cases: complete ? count : null, population, rate: complete && population ? count / population.value * 100000 : null };
  });
  const cases = complete ? annual.reduce((sum, item) => sum + (item.cases ?? 0), 0) : null;
  const rate = complete && annual.length && annual.every(item => item.rate !== null) ? annual.reduce((sum, item) => sum + item.rate!, 0) : null;
  return { region, cases, rate, value: window.metric === 'cases' ? cases : rate, population: annual.length === 1 ? annual[0].population : null, annual,
    observed, expected: expected.length, from: expected[0]?.start ?? null, to: expected.length ? addDays(expected[expected.length - 1].end_exclusive, -1) : null,
    reasons: [...new Set(reasons)], provisional };
}
export function resultsFor(catalog: MapCatalog, dataset: DiseaseDataset, populations: PopulationDataset, window: MapWindow) {
  if (!validWindow(window)) throw new RangeError('Invalid date range');
  const cache = projectionCache(catalog, dataset, populations)?.results, key = rangeKey(window);
  const cached = cache?.get(key);
  if (cached) return cached;
  const expected = expectedPeriods(dataset, window);
  const rows = catalog.regions.map(region => aggregateRegion(region, dataset, populations, window, expected));
  return cache ? remember(cache, key, rows) : rows;
}
export function defaultWindow(catalog: MapCatalog, dataset: DiseaseDataset, populations: PopulationDataset): MapWindow {
  const years = [...dataset.years].sort((a, b) => b - a);
  for (const requireAll of [true, false]) for (const year of years) {
    const window: MapWindow = { from: `${year}-01-01`, to: `${year}-12-31`, metric: 'rate', view: dataset.cadence === 'weekly' ? 'monthly' : dataset.cadence };
    const rows = resultsFor(catalog, dataset, populations, window).filter(row => dataset.regions[row.region.code]?.comparison_key === dataset.comparison_key);
    if (rows.length && (requireAll ? rows.every(row => row.value !== null) : rows.some(row => row.value !== null))) return window;
  }
  // Sparse weekly series may have no complete full year. Prefer a complete
  // recent quarter/month over an empty latest-year map, without filling gaps.
  for (const metric of ['rate', 'cases'] as const) for (const year of years) {
    const base: MapWindow = { from: `${year}-01-01`, to: `${year}-12-31`, metric, view: dataset.cadence === 'weekly' ? 'monthly' : dataset.cadence };
    const candidates = [
      ...(metric === 'cases' ? [base] : []),
      ...(cadenceRank[dataset.cadence] <= 2 ? [4, 3, 2, 1].map(quarter => presetWindow(base, year, 'quarter', quarter)) : []),
      ...(cadenceRank[dataset.cadence] <= 1 ? Array.from({ length: 12 }, (_, index) => presetWindow(base, year, 'month', 12 - index)) : []),
    ];
    for (const candidate of candidates) if (resultsFor(catalog, dataset, populations, candidate).some(row => row.value !== null)) return candidate;
  }
  const year = years[0] ?? new Date().getUTCFullYear();
  return { from: `${year}-01-01`, to: `${year}-12-31`, metric: 'cases', view: dataset.cadence === 'weekly' ? 'monthly' : dataset.cadence };
}
export function sharedCeiling(catalog: MapCatalog, dataset: DiseaseDataset, populations: PopulationDataset, window: MapWindow, comparison?: MapWindow | null): number {
  let maximum = 0;
  const fromYear = Number(window.from.slice(0, 4));
  // Deduplicate exact current, shifted and comparison windows before pooling.
  const windows = [...new Map([window, ...dataset.years.map(year => shiftWindowYear(window, year - fromYear)), ...(comparison ? [comparison] : [])].map(range => [rangeKey(range), range])).values()];
  const key = windows.map(rangeKey).sort().join(';');
  const cache = projectionCache(catalog, dataset, populations)?.ceilings;
  const cached = cache?.get(key);
  if (cached !== undefined) return cached;
  for (const range of windows) for (const row of resultsFor(catalog, dataset, populations, range)) maximum = Math.max(maximum, row.value ?? 0);
  const ceiling = prettyBreaks(maximum).at(-1)!;
  return cache ? remember(cache, key, ceiling) : ceiling;
}
export function sharedScale(catalog: MapCatalog, dataset: DiseaseDataset, populations: PopulationDataset, window: MapWindow, comparison?: MapWindow | null) {
  return continuousScale(sharedCeiling(catalog, dataset, populations, window, comparison));
}
export function restoreWindow(query: URLSearchParams, defaults: MapWindow, years: number[], bounds?: { from: string; to: string }): MapWindow {
  let from = query.get('mapFrom'), to = query.get('mapTo');
  if (!from && query.has('mapYear')) {
    const year = Number(query.get('mapYear')), start = Number(query.get('mapStart') ?? 1), end = Number(query.get('mapEnd') ?? 12);
    if (!years.includes(year) || !Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end > 12 || start > end) return defaults;
    from = iso(Date.UTC(year, start - 1, 1)); to = iso(Date.UTC(year, end, 0));
  }
  const candidate = { from: from ?? defaults.from, to: to ?? defaults.to, metric: query.get('mapMetric') ?? defaults.metric, view: query.get('mapView') ?? defaults.view } as MapWindow;
  return validWindow(candidate) && candidate.from >= (bounds?.from ?? `${Math.min(...years)}-01-01`)
    && candidate.to <= (bounds?.to ?? `${Math.max(...years)}-12-31`) ? candidate : defaults;
}
export function trendFor(region: Region, dataset: DiseaseDataset, populations: PopulationDataset, window: MapWindow) {
  const groups = new Map<string, ReportPeriod[]>();
  for (const period of expectedPeriods(dataset, window)) {
    if (window.view === 'annual' && period.reporting_year === null) {
      // An absent New Year week can belong to either adjacent report year.
      // Keep both possible annual groups incomplete, without assigning a
      // population denominator or publishing an apparently complete trend.
      for (const year of new Set([period.start.slice(0, 4), addDays(period.end_exclusive, -1).slice(0, 4)])) {
        groups.set(year, [...(groups.get(year) ?? []), period]);
      }
      continue;
    }
    const key = window.view === 'weekly' ? period.start : window.view === 'annual' ? String(period.reporting_year) : window.view === 'quarterly' ? `${period.start.slice(0, 4)} Q${Math.ceil(Number(period.start.slice(5, 7)) / 3)}` : period.start.slice(0, 7);
    groups.set(key, [...(groups.get(key) ?? []), period]);
  }
  return [...groups].sort(([a], [b]) => a.localeCompare(b)).map(([label, periods]) => {
    periods.sort((a, b) => a.start.localeCompare(b.start));
    const range = { ...window, from: periods[0].start, to: addDays(periods[periods.length - 1].end_exclusive, -1) };
    const row = aggregateRegion(region, dataset, populations, range, periods);
    return { label, value: row.value, from: range.from, to: range.to, observed: row.observed, expected: row.expected };
  });
}
export function csvFor(rows: RegionResult[], window: MapWindow, dataset: DiseaseDataset, catalog: MapCatalog, comparisonRows: RegionResult[] = [], comparison?: MapWindow | null) {
  const header = ['region_code', 'region_name', 'disease_id', 'requested_start', 'requested_end', 'actual_start', 'actual_end', 'source_cadence', 'view', 'cases', 'reports_per_100000', 'rate_method', 'population', 'population_year', 'population_source', 'population_reference_date', 'population_basis', 'population_source_url', 'population_version', 'annual_denominators_and_contributions', 'observed_periods', 'expected_periods', 'status', 'provisional', 'definition', 'time_basis', 'boundary_version', 'case_version', 'case_source_url', 'comparison_start', 'comparison_end', 'comparison_actual_start', 'comparison_actual_end', 'comparison_observed_periods', 'comparison_expected_periods', 'comparison_status', 'comparison_cases', 'comparison_reports_per_100000', 'comparison_annual_denominators_and_contributions', 'case_source_labels'];
  const escape = (value: unknown) => `"${String(value ?? '').replace(/"/g, '""')}"`;
  const other = new Map(comparisonRows.map(row => [row.region.code, row]));
  const records = rows.map(row => [row.region.code, row.region.name_en, dataset.disease_id, window.from, window.to, row.from, row.to, dataset.cadence, window.view, row.cases, row.rate, 'sum_of_reporting_year_cases_divided_by_same_year_local_population_times_100000', row.population?.value, row.population?.year, row.population?.source, row.population?.reference_date, row.population?.basis, row.population?.source_url, row.population?.version, JSON.stringify(row.annual), row.observed, row.expected, row.reasons.join('|') || 'observed', row.provisional, dataset.regions[row.region.code]?.semantics.definition, dataset.regions[row.region.code]?.semantics.time_basis, catalog.boundary.version, dataset.regions[row.region.code]?.case_version, dataset.regions[row.region.code]?.source_url, comparison?.from, comparison?.to, other.get(row.region.code)?.from, other.get(row.region.code)?.to, other.get(row.region.code)?.observed, other.get(row.region.code)?.expected, other.get(row.region.code)?.reasons.join('|'), other.get(row.region.code)?.cases, other.get(row.region.code)?.rate, other.has(row.region.code) ? JSON.stringify(other.get(row.region.code)!.annual) : '', dataset.regions[row.region.code]?.semantics.source_labels?.join(' | ')]);
  return '\uFEFF' + [header, ...records].map(record => record.map(escape).join(',')).join('\r\n');
}
