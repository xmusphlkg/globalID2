import { inWindow, periodKey, windowFor } from "../domain/calendar.ts";
import {
  fingerprint,
  type AnalysisConfig,
  type AnalysisResult,
  type Catalog,
  type Diagnostic,
  type ResultLine,
  type ResultPoint,
  type SurveillanceSeries,
} from "../domain/types.ts";

import {
  addDiagnostic,
  annualPoints,
  changePoints,
  finite,
  historicalPoints,
  isAdditiveSeries,
  smooth,
} from "./periodTransforms.ts";
import { summarize } from "./summary.ts";
import { planComparison } from "./comparisonPlan.ts";
import { applyGeographicAggregation } from "./geographicAggregation.ts";
import { alignSeasonality } from "./seasonality.ts";

export { annualPoints } from "./periodTransforms.ts";
export { summarize } from "./summary.ts";
export const METHOD_VERSION = "surveillance/1.3";
export function selectedSeries(
  catalog: Catalog,
  config: AnalysisConfig,
): SurveillanceSeries[] {
  return config.selectionIds.flatMap((id) =>
    (catalog.selections[id]?.seriesIds ?? [])
      .map((key) => catalog.series[key])
      .filter((series) => config.measures.includes(series.measure)),
  );
}
function cleanPoints(
  series: SurveillanceSeries,
  diagnostics: Diagnostic[],
): ResultPoint[] {
  const unique = new Map<string, ResultPoint>();
  for (const observation of series.observations) {
    const key = periodKey(observation.period.label, series.cadence);
    const point: ResultPoint = {
      date: observation.period.start ?? observation.period.label,
      value: observation.value,
      period: observation.period,
      quality: [...observation.quality],
    };
    if (unique.has(key)) {
      const previous = unique.get(key)!;
      unique.set(key, {
        ...previous,
        value: null,
        quality: [...previous.quality, "duplicate_period"],
      });
      addDiagnostic(diagnostics, "duplicate_period", series.id);
    } else unique.set(key, point);
  }
  return [...unique.values()].sort((a, b) => a.date.localeCompare(b.date));
}
export function analyze(
  catalog: Catalog,
  config: AnalysisConfig,
): AnalysisResult {
  const diagnostics: Diagnostic[] = [],
    excludedSeriesIds: string[] = [];
  const sources = selectedSeries(catalog, config);
  let lines: ResultLine[] = [];
  for (const selectionId of config.selectionIds) {
    for (const measure of config.measures) {
      if (
        !sources.some(
          (series) =>
            series.selectionId === selectionId && series.measure === measure,
        )
      )
        addDiagnostic(
          diagnostics,
          "measure_unavailable",
          selectionId,
          "error",
          measure,
        );
    }
  }
  for (const series of sources) {
    if (series.loading === "unloaded") {
      addDiagnostic(diagnostics, "source_unloaded", series.id, "error");
      excludedSeriesIds.push(series.id);
      continue;
    }
    let points = cleanPoints(series, diagnostics);
    if (!points.some((point) => finite(point.value))) {
      addDiagnostic(diagnostics, "no_observations", series.id, "error");
      excludedSeriesIds.push(series.id);
      continue;
    }
    if (
      config.task === "outbreak" &&
      (config.aggregation !== "native" ||
        config.smoothing !== 1 ||
        series.valueKind !== "interval_count" ||
        !["daily", "weekly"].includes(series.cadence) ||
        ![
          "onset",
          "onset_date",
          "symptom_onset",
          "date_of_onset",
          "symptom onset",
          "onset date",
        ].includes((series.timeBasis ?? "").toLowerCase()))
    ) {
      addDiagnostic(diagnostics, "outbreak_ineligible", series.id, "error");
      excludedSeriesIds.push(series.id);
      continue;
    }
    if (config.aggregation === "annual")
      points = annualPoints(series, points, diagnostics);
    const grain = config.aggregation === "annual" ? "annual" : series.cadence;
    if (
      config.task === "historical" ||
      (config.task === "trend" &&
        config.showReference &&
        series.valueKind === "interval_count")
    ) {
      points = historicalPoints(
        { ...series, cadence: grain },
        points,
        config,
        diagnostics,
      );
    }
    let unit = series.unit;
    if (config.task === "change") {
      points = changePoints(
        { ...series, cadence: grain },
        points,
        config.changeBasis,
        diagnostics,
      );
      unit = "change_percent";
    }
    if (config.task === "shape") {
      const peak = points.reduce(
        (max, point) =>
          finite(point.value) ? Math.max(max, point.value) : max,
        0,
      );
      if (
        peak <= 0 ||
        points.some((point) => finite(point.value) && point.value < 0)
      ) {
        addDiagnostic(
          diagnostics,
          "normalization_ineligible",
          series.id,
          "error",
        );
        excludedSeriesIds.push(series.id);
        continue;
      }
      points = points.map((point) => ({
        ...point,
        value: finite(point.value) ? (point.value / peak) * 100 : null,
      }));
      unit = "peak_index";
    }
    if (config.task === "historical") {
      points = points.map((point) => ({
        ...point,
        value:
          finite(point.value) && point.expected != null && point.expected > 0
            ? (point.value / point.expected) * 100
            : null,
      }));
      if (!points.some((point) => finite(point.value)))
        addDiagnostic(diagnostics, "history_insufficient", series.id, "error");
      unit = "historical_index";
    }
    if (config.smoothing !== 1 && config.task !== "change") {
      points = smooth(points, config.smoothing, { ...series, cadence: grain });
      // The unsmoothed reference is not a reference for the smoothed estimate.
      points = points.map(
        ({ expected, lower, upper, referenceCount, ...point }) => point,
      );
    }
    if (!points.some((point) => finite(point.value)) && config.task !== "change") {
      excludedSeriesIds.push(series.id);
      continue;
    }
    const base: ResultLine = {
      id: series.id,
      seriesId: series.id,
      selectionId: series.selectionId,
      entityId: series.entityId,
      labelSuffix: "",
      unit,
      cadence: grain,
      displayCadence: series.displayCadence,
      measure: series.measure,
      panelId: series.id,
      points,
      windowPoints: [],
      additive:
        isAdditiveSeries(series) &&
        !["shape", "historical", "change"].includes(config.task) &&
        config.smoothing === 1,
      provenance: series.provenance,
      events: series.events,
    };
    if (config.task === "seasonality") {
      const seasonality = alignSeasonality(series, points, config.window);
      if (seasonality.reason) {
        addDiagnostic(
          diagnostics,
          seasonality.reason === "calendar_unknown"
            ? "seasonality_calendar_unknown"
            : "seasonality_ineligible",
          series.id,
          "error",
        );
        excludedSeriesIds.push(series.id);
        continue;
      }
      for (const { year, points: yearPoints } of seasonality.groups) {
        if (!yearPoints.some((point) => finite(point.value))) continue;
        lines.push({
          ...base,
          id: `${series.id}::${year}`,
          labelSuffix: year,
          points: yearPoints,
          panelId: series.id,
        });
      }
    } else lines.push(base);
    if (series.cadence === "unknown")
      addDiagnostic(diagnostics, "cadence_unknown", series.id, "info");
    if (series.provenance.denominatorMethod === "legacy_inferred_population")
      addDiagnostic(diagnostics, "inferred_population", series.id, "warning");
    if (
      series.observations.some((point) =>
        point.quality.some((status) =>
          ["provisional", "preliminary"].includes(status),
        ),
      )
    )
      addDiagnostic(diagnostics, "provisional", series.id, "info");
    if (
      series.observations.some((point) =>
        point.quality.includes("partial_report_window"),
      )
    )
      addDiagnostic(diagnostics, "partial_report_window", series.id);
    if (
      series.observations.some((point) =>
        point.quality.includes("period_scope_mismatch"),
      )
    )
      addDiagnostic(diagnostics, "period_scope_mismatch", series.id);
  }
  lines = applyGeographicAggregation(catalog, config, lines, diagnostics);
  const comparison = planComparison(catalog, config, lines, diagnostics);
  lines = comparison.lines;
  const domain = windowFor(
    lines.flatMap((line) => line.points.map((point) => point.date)),
  );
  const requestedWindow =
    config.task === "seasonality" ? domain : (config.window ?? domain);
  const overlap =
    requestedWindow && comparison.useCommonWindow
      ? {
          startDate:
            requestedWindow.startDate > comparison.commonWindow!.startDate
              ? requestedWindow.startDate
              : comparison.commonWindow!.startDate,
          endDate:
            requestedWindow.endDate < comparison.commonWindow!.endDate
              ? requestedWindow.endDate
              : comparison.commonWindow!.endDate,
        }
      : null;
  const window =
    overlap && overlap.startDate <= overlap.endDate ? overlap : requestedWindow;
  lines = lines.map((line) => ({
    ...line,
    windowPoints: line.points.filter((point) => inWindow(point.date, window)),
  }));
  if (
    lines.length &&
    !lines.some((line) =>
      line.windowPoints.some((point) => finite(point.value)),
    )
  )
    diagnostics.push({
      code: "empty_window",
      severity: "warning",
      seriesIds: lines.map((line) => line.seriesId),
    });
  const logUnavailable = lines.some((line) =>
    line.points.some((point) => finite(point.value) && point.value <= 0),
  );
  const scale = config.scale === "log" && !logUnavailable ? "log" : "linear";
  if (config.scale === "log" && logUnavailable)
    diagnostics.push({
      code: "log_nonpositive",
      severity: "warning",
      seriesIds: lines
        .filter((line) =>
          line.points.some((point) => finite(point.value) && point.value <= 0),
        )
        .map((line) => line.seriesId),
    });
  return {
    id: fingerprint([catalog.version, METHOD_VERSION, config]),
    catalogVersion: catalog.version,
    methodVersion: METHOD_VERSION,
    config,
    lines,
    summaries: lines.map((line) => {
      const summary = summarize(line, window);
      return config.task === "change"
        ? { ...summary, changePrevious: null, changeYear: null }
        : summary;
    }),
    diagnostics,
    domain,
    window,
    commonWindow: comparison.commonWindow,
    layout: comparison.layout,
    scale,
    excludedSeriesIds,
  };
}
