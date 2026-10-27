export type Language = "en" | "zh" | "fr";
export type Labels = Record<Language, string>;
export type Measure = "cases" | "deaths" | "incidence_rates" | "source_value";
export type Cadence =
  "daily" | "weekly" | "monthly" | "quarterly" | "annual" | "unknown";
export type Task =
  | "trend"
  | "burden"
  | "shape"
  | "historical"
  | "change"
  | "seasonality"
  | "outbreak"
  | "sources";
export type Layout = "auto" | "overlay" | "facets";
export interface DateWindow {
  startDate: string;
  endDate: string;
}
export interface Period {
  label: string;
  start: string | null;
  endExclusive: string | null;
  calendar: "gregorian" | "iso-week" | "source" | "unknown";
  precision: "exact" | "inferred" | "unknown";
}
export interface Observation {
  period: Period;
  value: number | null;
  quality: string[];
  missingReason: string | null;
  sourceId: string;
  definitionId: string | null;
}
export interface Entity {
  id: string;
  labels: Labels;
  diseaseId: string;
  geographyId: string;
  parentId: string | null;
  geographyLevel: "country" | "subdivision" | "unknown";
  category?: string;
}
export interface Provenance {
  sourceId: string;
  label: string;
  url: string | null;
  dataLayer: string;
  policy: string | null;
  denominatorMethod:
    "published" | "explicit_population" | "legacy_inferred_population" | null;
  snapshotVersion?: string;
}
export interface SurveillanceSeries {
  id: string;
  selectionId: string;
  entityId: string;
  measure: Measure;
  unit: string;
  valueKind: "interval_count" | "cumulative_count" | "rate" | "unknown";
  aggregationPolicy: string | null;
  cadence: Cadence;
  displayCadence?: Cadence;
  cadenceBasis?: "source" | "date_pattern" | "unknown";
  dimensions: Record<string, string>;
  timeBasis: string | null;
  reportingBasis: string | null;
  definitionId: string | null;
  comparability: "direct" | "conditional" | "not_comparable" | "unknown";
  observations: Observation[];
  events: Array<{ date: string; label: string }>;
  provenance: Provenance;
  loading: "ready" | "unloaded";
}
export interface Selection {
  id: string;
  entityId: string;
  sourceLabel: string | null;
  isDefault: boolean;
  seriesIds: string[];
}
export interface Catalog {
  contextId: string;
  version: string;
  recordedAtAsOf: string | null;
  recordedHistoryComplete: boolean | null;
  snapshot: boolean;
  sourceVersion?: string;
  entities: Record<string, Entity>;
  selections: Record<string, Selection>;
  series: Record<string, SurveillanceSeries>;
}
export interface AnalysisConfig {
  schemaVersion: 3;
  task: Task;
  selectionIds: string[];
  measures: Measure[];
  aggregation: "native" | "annual";
  window: DateWindow | null;
  layout: Layout;
  scale: "linear" | "log";
  smoothing: 1 | 3 | 7;
  referenceYears: number;
  showReference: boolean;
  changeBasis: "previous" | "year";
  compareWindow: "common" | "independent";
  aggregationGroups: AggregationGroup[];
}
export interface AggregationGroup {
  id: string;
  selectionIds: string[];
}
export interface Diagnostic {
  code: string;
  severity: "info" | "warning" | "error";
  seriesIds: string[];
  detail?: string;
}
export interface ResultPoint {
  date: string;
  value: number | null;
  period: Period;
  quality: string[];
  expected?: number | null;
  lower?: number | null;
  upper?: number | null;
  referenceCount?: number;
  comparison?: {
    basis: "previous" | "year";
    periodLabel: string;
    value: number;
  } | null;
}
export interface ResultLine {
  id: string;
  seriesId: string;
  selectionId: string;
  entityId: string;
  labelSuffix: string;
  unit: string;
  cadence: Cadence;
  displayCadence?: Cadence;
  measure: Measure;
  panelId: string;
  points: ResultPoint[];
  windowPoints: ResultPoint[];
  additive: boolean;
  aggregateMembers?: string[];
  events: SurveillanceSeries["events"];
  provenance: Provenance;
}
export interface Summary {
  lineId: string;
  latest: ResultPoint | null;
  observed: number;
  missing: number;
  expectedPeriods: number | null;
  total: number | null;
  changePrevious: number | null;
  changeYear: number | null;
}
export interface AnalysisResult {
  id: string;
  catalogVersion: string;
  methodVersion: string;
  config: AnalysisConfig;
  lines: ResultLine[];
  summaries: Summary[];
  diagnostics: Diagnostic[];
  domain: DateWindow | null;
  window: DateWindow | null;
  commonWindow: DateWindow | null;
  layout: "overlay" | "facets";
  scale: "linear" | "log";
  excludedSeriesIds: string[];
}
export const TASKS: Task[] = [
  "trend",
  "burden",
  "shape",
  "historical",
  "change",
  "seasonality",
  "outbreak",
  "sources",
];
export const MEASURES: Measure[] = [
  "cases",
  "deaths",
  "incidence_rates",
  "source_value",
];
export function fingerprint(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++)
    hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
  return (hash >>> 0).toString(36);
}
