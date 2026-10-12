import type { AnalysisResult, Catalog, Language } from "../domain/types.ts";
import { label, lineName } from "./labels.ts";
export type DataScope = "window" | "derived" | "raw";
export interface DataRow {
  series: string;
  seriesId: string;
  date: string;
  value: number | null;
  unit: string;
  quality: string;
  source: string;
  sourceVersion: string | null;
  baselineDate: string | null;
  baselineValue: number | null;
  aggregationMembers: string;
}
export function dataRows(
  result: AnalysisResult,
  catalog: Catalog,
  lang: Language,
  scope: DataScope,
): DataRow[] {
  if (scope === "raw") {
    return result.config.selectionIds
      .flatMap((id) =>
        (catalog.selections[id]?.seriesIds ?? []).map(
          (key) => catalog.series[key],
        ),
      )
      .filter((series) => result.config.measures.includes(series.measure))
      .flatMap((series) =>
        series.observations.map((point) => ({
          series: [
            catalog.entities[series.entityId]?.labels[lang] ?? series.entityId,
            catalog.selections[series.selectionId]?.sourceLabel,
            label(series.measure, lang),
          ]
            .filter(Boolean)
            .join(" · "),
          seriesId: series.id,
          date: point.period.label,
          value: point.value,
          unit: series.unit,
          quality: point.quality.join(";"),
          source: point.sourceId,
          sourceVersion: series.provenance.snapshotVersion ?? null,
          baselineDate: null,
          baselineValue: null,
          aggregationMembers: "",
        })),
      );
  }
  return result.lines.flatMap((line) =>
    (scope === "window" ? line.windowPoints : line.points).map((point) => ({
      series: lineName(line, catalog, lang),
      seriesId: line.id,
      date: point.period.label,
      value: point.value,
      unit: line.unit,
      quality: point.quality.join(";"),
      source: line.provenance.sourceId,
      sourceVersion: line.provenance.snapshotVersion ?? null,
      baselineDate: point.comparison?.periodLabel ?? null,
      baselineValue: point.comparison?.value ?? null,
      aggregationMembers: (line.aggregateMembers ?? [])
        .map(
          (id) =>
            catalog.entities[catalog.selections[id]?.entityId]?.labels[lang] ??
            id,
        )
        .join("; "),
    })),
  );
}
function csvCell(value: unknown): string {
  const raw = value == null ? "" : String(value);
  // Spreadsheet formula protection applies to text, never to numeric observations.
  const safe =
    typeof value === "string" && /^[=+\-@\t\r]/.test(raw) ? `'${raw}` : raw;
  return `"${safe.replaceAll('"', '""')}"`;
}
export function csv(
  result: AnalysisResult,
  catalog: Catalog,
  lang: Language,
  scope: DataScope,
): string {
  const rows = dataRows(result, catalog, lang, scope);
  return [
    [
      "result_id",
      "data_version",
      "series",
      "series_id",
      "date",
      "value",
      "unit",
      "quality",
      "source",
      "source_version",
      "baseline_date",
      "baseline_value",
      "aggregation_members",
      "scope",
    ],
    ...rows.map((row) => [
      result.id,
      result.catalogVersion,
      row.series,
      row.seriesId,
      row.date,
      row.value,
      row.unit,
      row.quality,
      row.source,
      row.sourceVersion,
      row.baselineDate,
      row.baselineValue,
      row.aggregationMembers,
      scope,
    ]),
  ]
    .map((row) => row.map(csvCell).join(","))
    .join("\r\n");
}
export function download(name: string, contents: string, type: string) {
  const url = URL.createObjectURL(new Blob([contents], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
