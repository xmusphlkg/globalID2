import type { CountryDatasetSeriesEntry } from "../../countryDataset";
import { reportPeriodIndices } from "./reportSnapshot.ts";

export type ReportCurveSeries = Pick<
  CountryDatasetSeriesEntry,
  "dates" | "cases" | "deaths" | "incidence_rates" | "total_cases"
> & Partial<CountryDatasetSeriesEntry>;

export interface ReportCurveDisease {
  disease_id: string;
  name_en: string;
  name_zh: string;
  name_fr?: string;
}

export function buildReportComparison(input: {
  series: ReportCurveSeries;
  latestSeries?: ReportCurveSeries | null;
  disease: ReportCurveDisease;
  periodStart: string;
  periodEnd: string;
}) {
  const { series, latestSeries, disease, periodStart, periodEnd } = input;
  const hasSnapshot = Boolean(series.snapshot_version);
  const compareLatest = hasSnapshot && Boolean(latestSeries?.dates.length);
  const snapshotKey = `${disease.disease_id}::snapshot`;
  const latestKey = `${disease.disease_id}::latest`;
  const partialSnapshotPeriods = new Set(
    series.dates.filter((_, index) =>
      series.point_quality_statuses?.[index]?.includes("partial_report_window"),
    ),
  );
  const project = (
    source: ReportCurveSeries,
    frozen: boolean,
    name: { en: string; zh: string; fr: string },
  ): CountryDatasetSeriesEntry => {
    const indices = reportPeriodIndices(
      source.dates,
      periodStart,
      periodEnd,
      frozen,
      source.period_starts,
      source.period_ends_exclusive,
    );
    const aligned = <T,>(values: Array<T | null> | undefined) =>
      indices.map((index) => values?.[index] ?? null);
    return {
      ...source,
      disease_id: disease.disease_id,
      name_en: name.en,
      name_zh: name.zh,
      name_fr: name.fr,
      dates: indices.map((index) => source.dates[index]),
      cases: aligned(source.cases),
      deaths: aligned(source.deaths),
      incidence_rates: aligned(source.incidence_rates),
      weekly_equiv_cases: [],
      source_series: [],
      data_layer: frozen ? "report_snapshot" : "latest_published",
      point_series_codes: aligned(source.point_series_codes),
      point_granularities: aligned(source.point_granularities),
      point_data_layers: indices.map(() =>
        frozen ? "report_snapshot" : "latest_published",
      ),
      incidence_sources: aligned(source.incidence_sources),
      period_starts: aligned(source.period_starts),
      period_ends_exclusive: aligned(source.period_ends_exclusive),
      point_quality_statuses: indices.map((index) => {
        const statuses = (source.point_quality_statuses?.[index] ?? "")
          .split(";")
          .filter(Boolean);
        if (!frozen && partialSnapshotPeriods.has(source.dates[index]))
          statuses.push("period_scope_mismatch");
        return [...new Set(statuses)].join(";");
      }),
      snapshot_version: frozen ? source.snapshot_version : undefined,
    };
  };

  let entries: Record<string, CountryDatasetSeriesEntry>;
  if (hasSnapshot) {
    entries = {
      [snapshotKey]: project(series, true, {
        en: `${disease.name_en} · report snapshot`,
        zh: `${disease.name_zh} · 报告快照`,
        fr: `${disease.name_fr ?? disease.name_en} · instantané du rapport`,
      }),
    };
    if (compareLatest && latestSeries) {
      entries[latestKey] = project(latestSeries, false, {
        en: `${disease.name_en} · current published`,
        zh: `${disease.name_zh} · 当前发布数据`,
        fr: `${disease.name_fr ?? disease.name_en} · données publiées actuelles`,
      });
    }
  } else {
    entries = {
      [disease.disease_id]: project(series, false, {
        en: disease.name_en,
        zh: disease.name_zh,
        fr: disease.name_fr ?? disease.name_en,
      }),
    };
  }

  const selectionKey = hasSnapshot ? snapshotKey : disease.disease_id;
  return {
    entries,
    hasSnapshot,
    compareLatest,
    initialTask: compareLatest ? ("burden" as const) : ("trend" as const),
    initialSelectionIds: compareLatest
      ? [
          `${encodeURIComponent(snapshotKey)}::published`,
          `${encodeURIComponent(latestKey)}::published`,
        ]
      : [`${encodeURIComponent(selectionKey)}::published`],
  };
}
