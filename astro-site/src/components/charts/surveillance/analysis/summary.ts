import { DAY, expectedCount, periodKey } from "../domain/calendar.ts";
import type { DateWindow, ResultLine, ResultPoint, Summary } from "../domain/types.ts";
import { finite } from "./periodTransforms.ts";

export function summarize(
  line: ResultLine,
  window: DateWindow | null,
): Summary {
  const observed = line.windowPoints.filter((point) => finite(point.value));
  const latest = observed.at(-1) ?? null;
  let expectedPeriods = expectedCount(window, line.cadence);
  if (window && line.cadence === "weekly") {
    const anchor = line.points.find(
      (point) =>
        point.period.calendar === "iso-week" &&
        point.period.precision === "exact" &&
        point.period.start &&
        point.period.endExclusive &&
        Date.parse(point.period.endExclusive) -
          Date.parse(point.period.start) ===
          7 * DAY,
    );
    if (anchor?.period.start) {
      const offset =
        Date.parse(anchor.period.start) - Date.parse(window.startDate);
      if (offset % (7 * DAY) === 0)
        expectedPeriods =
          Math.floor(
            (Date.parse(window.endDate) - Date.parse(window.startDate)) /
              (7 * DAY),
          ) + 1;
    }
  }
  const byDate = new Map(
    line.points.map((point) => [periodKey(point.date, line.cadence), point]),
  );
  const change = (previous?: ResultPoint) =>
    latest && previous && finite(previous.value) && previous.value !== 0
      ? ((latest.value! - previous.value) / Math.abs(previous.value)) * 100
      : null;
  let previous: ResultPoint | undefined, lastYear: ResultPoint | undefined;
  if (latest && line.cadence !== "unknown") {
    const date = new Date(`${latest.date}T00:00:00Z`);
    const yearDate = new Date(date);
    yearDate.setUTCFullYear(yearDate.getUTCFullYear() - 1);
    if (
      yearDate.getUTCMonth() === date.getUTCMonth() &&
      yearDate.getUTCDate() === date.getUTCDate()
    )
      lastYear = byDate.get(
        periodKey(yearDate.toISOString().slice(0, 10), line.cadence),
      );
    if (line.cadence === "daily" || line.cadence === "weekly")
      date.setUTCDate(date.getUTCDate() - (line.cadence === "daily" ? 1 : 7));
    else {
      date.setUTCDate(1);
      date.setUTCMonth(
        date.getUTCMonth() -
          (line.cadence === "annual"
            ? 12
            : line.cadence === "quarterly"
              ? 3
              : 1),
      );
    }
    previous = byDate.get(
      periodKey(date.toISOString().slice(0, 10), line.cadence),
    );
  }
  return {
    lineId: line.id,
    latest,
    observed: observed.length,
    missing: Math.max(
      line.windowPoints.length - observed.length,
      (expectedPeriods ?? observed.length) - observed.length,
    ),
    expectedPeriods,
    total:
      line.additive &&
      expectedPeriods !== null &&
      observed.length === expectedPeriods &&
      observed.every(
        (point) =>
          !point.quality.some((status) =>
            ["partial_report_window", "period_scope_mismatch"].includes(status),
          ),
      )
        ? observed.reduce((sum, point) => sum + point.value!, 0)
        : null,
    changePrevious: change(previous),
    changeYear: line.cadence === "weekly" ? null : change(lastYear),
  };
}
