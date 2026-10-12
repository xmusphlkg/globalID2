import { inWindow, windowFor } from "../domain/calendar.ts";
import {
  type AnalysisConfig,
  type Catalog,
  type DateWindow,
  type Diagnostic,
  type ResultLine,
  type SurveillanceSeries,
} from "../domain/types.ts";

function commonWindow(lines: ResultLine[]): DateWindow | null {
  const ranges = lines.map((line) =>
    windowFor(
      line.points
        .filter((point) => point.value !== null && Number.isFinite(point.value))
        .map((point) => point.date),
    ),
  );
  if (!ranges.length || ranges.some((range) => !range)) return null;
  const startDate = ranges.reduce(
    (date, range) => (range!.startDate > date ? range!.startDate : date),
    ranges[0]!.startDate,
  );
  const endDate = ranges.reduce(
    (date, range) => (range!.endDate < date ? range!.endDate : date),
    ranges[0]!.endDate,
  );
  return startDate <= endDate ? { startDate, endDate } : null;
}

function signature(series: SurveillanceSeries, line: ResultLine): string {
  return JSON.stringify([
    line.measure,
    line.unit,
    line.cadence,
    series.reportingBasis,
    series.timeBasis,
    series.definitionId,
  ]);
}

export function planComparison(
  catalog: Catalog,
  config: AnalysisConfig,
  lines: ResultLine[],
  diagnostics: Diagnostic[],
): {
  lines: ResultLine[];
  commonWindow: DateWindow | null;
  layout: "overlay" | "facets";
  useCommonWindow: boolean;
} {
  const common = config.task === "seasonality" ? null : commonWindow(lines);
  const isComparison =
    lines.length > 1 &&
    !["trend", "seasonality", "outbreak"].includes(config.task);
  const sources = config.selectionIds.flatMap((id) =>
    (catalog.selections[id]?.seriesIds ?? [])
      .map((key) => catalog.series[key])
      .filter((series) => config.measures.includes(series.measure)),
  );
  const hardIncompatible = sources.some(
    (source) => source.comparability === "not_comparable",
  );
  const signatures = new Set(
    lines.map((line) => signature(catalog.series[line.seriesId], line)),
  );
  const splitRequired =
    config.task === "sources" ||
    hardIncompatible ||
    (isComparison && !common) ||
    signatures.size > 1;
  if (isComparison && !common)
    diagnostics.push({
      code: "no_common_window",
      severity: "warning",
      seriesIds: lines.map((line) => line.seriesId),
    });
  if (hardIncompatible && isComparison)
    diagnostics.push({
      code: "not_comparable",
      severity: "warning",
      seriesIds: sources
        .filter((series) => series.comparability === "not_comparable")
        .map((series) => series.id),
    });
  if (
    isComparison &&
    sources.some(
      (source) =>
        source.comparability !== "direct" ||
        !source.timeBasis ||
        !source.reportingBasis ||
        !source.definitionId,
    )
  )
    diagnostics.push({
      code: "conditional_comparison",
      severity: "warning",
      seriesIds: sources.map((series) => series.id),
    });
  if (signatures.size > 1 && isComparison)
    diagnostics.push({
      code: "separate_panels",
      severity: "info",
      seriesIds: lines.map((line) => line.seriesId),
    });
  const layout =
    config.task === "seasonality"
      ? "facets"
      : splitRequired ||
          config.layout === "facets" ||
          (config.layout === "auto" && lines.length > 5)
        ? "facets"
        : "overlay";
  if (config.layout === "overlay" && layout === "facets")
    diagnostics.push({
      code: "overlay_unavailable",
      severity: "warning",
      seriesIds: lines.map((line) => line.seriesId),
    });
  const useCommonWindow =
    isComparison &&
    config.compareWindow === "common" &&
    common !== null &&
    !hardIncompatible;
  const plannedLines = useCommonWindow
    ? lines.map((line) => ({
        ...line,
        points: line.points.filter((point) => inWindow(point.date, common)),
      }))
    : lines;
  return {
    lines: plannedLines.map((line) => ({
      ...line,
      panelId: layout === "overlay" ? "overlay" : line.panelId,
    })),
    commonWindow: common,
    layout,
    useCommonWindow,
  };
}
