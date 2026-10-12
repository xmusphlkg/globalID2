import { DAY, periodFor, validDate } from "../domain/calendar.ts";
import type {
  AnalysisConfig,
  Diagnostic,
  ResultPoint,
  SurveillanceSeries,
} from "../domain/types.ts";

export const finite = (value: number | null): value is number =>
  value !== null && Number.isFinite(value);
function quantile(values: number[], probability: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  const position = (sorted.length - 1) * probability;
  return (
    sorted[Math.floor(position)] +
    (sorted[Math.ceil(position)] - sorted[Math.floor(position)]) *
      (position % 1)
  );
}
export function addDiagnostic(
  list: Diagnostic[],
  code: string,
  seriesId: string,
  severity: Diagnostic["severity"] = "warning",
  detail?: string,
) {
  const existing = list.find(
    (item) => item.code === code && item.detail === detail,
  );
  if (existing) {
    if (!existing.seriesIds.includes(seriesId))
      existing.seriesIds.push(seriesId);
  } else list.push({ code, severity, seriesIds: [seriesId], detail });
}
export function annualPoints(
  series: SurveillanceSeries,
  points: ResultPoint[],
  diagnostics: Diagnostic[],
): ResultPoint[] {
  if (!isAdditiveSeries(series)) {
    addDiagnostic(diagnostics, "not_additive", series.id, "error");
    return [];
  }
  const years = new Map<string, ResultPoint[]>();
  points.forEach((point) => {
    const year = point.date.slice(0, 4);
    years.set(year, [...(years.get(year) ?? []), point]);
  });
  const result: ResultPoint[] = [];
  for (const [year, rows] of years) {
    const start = `${year}-01-01`,
      end = `${Number(year) + 1}-01-01`;
    // Full coverage is a contiguous sequence of unique intervals, not a count
    // of 12 months or 52 weeks. Unknown weekly boundaries remain unavailable.
    const ordered = [...rows].sort((a, b) =>
      (a.period.start ?? a.date).localeCompare(b.period.start ?? b.date),
    );
    let cursor = start;
    const complete =
      ordered.every((point) => {
        if (
          !finite(point.value) ||
          point.quality.some((status) =>
            ["partial_report_window", "period_scope_mismatch"].includes(status),
          ) ||
          point.period.start !== cursor ||
          !point.period.endExclusive ||
          point.period.endExclusive > end
        )
          return false;
        cursor = point.period.endExclusive;
        return true;
      }) && cursor === end;
    if (!complete) {
      addDiagnostic(diagnostics, "incomplete_year", series.id, "warning", year);
      continue;
    }
    result.push({
      date: start,
      value: ordered.reduce((sum, point) => sum + point.value!, 0),
      period: periodFor(start, "annual"),
      quality: [...new Set(rows.flatMap((point) => point.quality))],
    });
  }
  if (!result.length)
    addDiagnostic(diagnostics, "no_complete_year", series.id, "error");
  return result;
}
export function isAdditiveSeries(series: SurveillanceSeries): boolean {
  return (
    series.valueKind === "interval_count" &&
    !["none", "direct_only", "non_additive"].includes(
      series.aggregationPolicy ?? "",
    )
  );
}
export function historicalPoints(
  series: SurveillanceSeries,
  points: ResultPoint[],
  config: AnalysisConfig,
  diagnostics: Diagnostic[],
): ResultPoint[] {
  if (series.cadence === "unknown" || series.cadence === "weekly") {
    addDiagnostic(
      diagnostics,
      "history_calendar_unknown",
      series.id,
      config.task === "historical" ? "error" : "info",
    );
    return points;
  }
  const history = new Map<string, Array<{ year: number; value: number }>>();
  const breaks = series.events.map((event) => event.date).sort();
  let nextBreak = 0;
  return points.map((point) => {
    while (breaks[nextBreak] && point.date >= breaks[nextBreak]) {
      history.clear();
      nextBreak++;
    }
    const year = Number(point.date.slice(0, 4));
    const slot =
      series.cadence === "annual"
        ? "year"
        : series.cadence === "daily"
          ? point.date.slice(5)
          : point.date.slice(5, 7);
    const prior = (history.get(slot) ?? []).filter(
      (item) => item.year < year && item.year >= year - config.referenceYears,
    );
    const values = prior.map((item) => item.value);
    const enough = values.length >= 5;
    const expected = enough ? quantile(values, 0.5) : null;
    const result = {
      ...point,
      expected,
      lower: enough ? quantile(values, 0.25) : null,
      upper: enough ? quantile(values, 0.75) : null,
      referenceCount: values.length,
    };
    if (
      finite(point.value) &&
      point.value >= 0 &&
      !point.quality.some((status) =>
        ["provisional", "preliminary", "duplicate_period"].includes(status),
      )
    ) {
      history.set(slot, [
        ...(history.get(slot) ?? []),
        { year, value: point.value },
      ]);
    }
    return result;
  });
}
export function smooth(
  points: ResultPoint[],
  count: number,
  series: SurveillanceSeries,
): ResultPoint[] {
  if (count === 1) return points;
  return points.map((point, index) => {
    const window = points.slice(Math.max(0, index - count + 1), index + 1);
    const contiguous = window.every(
      (row, position) =>
        position === 0 ||
        (series.cadence === "weekly"
          ? Date.parse(row.date) - Date.parse(window[position - 1].date) ===
            7 * DAY
          : window[position - 1].period.endExclusive === row.period.start),
    );
    return {
      ...point,
      value:
        window.length === count &&
        contiguous &&
        window.every((row) => finite(row.value))
          ? window.reduce((sum, row) => sum + row.value!, 0) / count
          : null,
    };
  });
}

/** Match full source periods before calculating a change; missing and zero bases stay null. */
export function changePoints(
  series: SurveillanceSeries,
  points: ResultPoint[],
  basis: AnalysisConfig["changeBasis"],
  diagnostics: Diagnostic[],
): ResultPoint[] {
  if (series.cadence === "unknown" || (basis === "year" && series.cadence === "weekly")) {
    addDiagnostic(diagnostics, "change_calendar_unknown", series.id, "error");
    return points.map((point) => ({ ...point, value: null }));
  }
  const byStart = new Map(points.map((point) => [point.period.start, point]));
  const byEnd = new Map(points.map((point) => [point.period.endExclusive, point]));
  const breaks = series.events.map((event) => event.date).sort();
  return points.map((point) => {
    let baseline: ResultPoint | undefined;
    if (point.period.start && point.period.endExclusive) {
      if (basis === "previous") {
        baseline = byEnd.get(point.period.start);
      } else {
        const date = new Date(`${point.period.start}T00:00:00Z`);
        date.setUTCFullYear(date.getUTCFullYear() - 1);
        const priorDate = date.toISOString().slice(0, 10);
        if (validDate(priorDate) && priorDate.slice(5) === point.period.start.slice(5))
          baseline = byStart.get(priorDate);
      }
    }
    let reason: string | null = null;
    if (!finite(point.value)) return { ...point, value: null };
    if (!baseline || !finite(baseline.value)) reason = "change_baseline_missing";
    else if (
      baseline.period.calendar !== point.period.calendar ||
      (basis === "year" && point.period.calendar !== "gregorian")
    ) reason = "change_calendar_unknown";
    else if (baseline.value <= 0) reason = "change_baseline_nonpositive";
    else if (
      breaks.some(
        (date) => date > (baseline?.period.start ?? "") && date <= point.date,
      )
    ) reason = "change_definition_break";
    if (reason) {
      addDiagnostic(diagnostics, reason, series.id, "info");
      return {
        ...point,
        value: null,
        comparison: baseline && finite(baseline.value)
          ? { basis, periodLabel: baseline.period.label, value: baseline.value }
          : null,
        quality: [...point.quality, reason],
      };
    }
    return {
      ...point,
      value: ((point.value - baseline!.value!) / baseline!.value!) * 100,
      comparison: {
        basis,
        periodLabel: baseline!.period.label,
        value: baseline!.value!,
      },
    };
  });
}
