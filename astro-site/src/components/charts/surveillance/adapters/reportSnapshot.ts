import type { CountryDatasetSeriesEntry } from "../../countryDataset";
import { validDate } from "../domain/calendar.ts";

export type ReportSnapshotSeries = CountryDatasetSeriesEntry & {
  snapshot_version: string;
};

export function reportPeriodIndices(
  dates: string[],
  periodStart: string,
  periodEnd: string,
  frozen: boolean,
  periodStarts?: Array<string | null>,
  periodEndsExclusive?: Array<string | null>,
): number[] {
  return dates.flatMap((date, index) =>
    frozen ||
    (periodStarts?.[index] && periodEndsExclusive?.[index]
      ? periodStarts[index]! <= periodEnd &&
        periodEndsExclusive[index]! > periodStart
      : date >= periodStart && date <= periodEnd)
      ? [index]
      : [],
  );
}

/** Reject incomplete archives so an older report can use its labeled fallback. */
export function readReportSnapshotSeries(
  snapshot: unknown,
  diseaseId: string,
): ReportSnapshotSeries | null {
  if (!snapshot || typeof snapshot !== "object") return null;
  const payload = snapshot as Record<string, unknown>;
  if (
    payload.schema_version !== "report_surveillance.v1" ||
    typeof payload.version !== "string" ||
    !/^[a-f0-9]{64}$/.test(payload.version) ||
    !payload.series ||
    typeof payload.series !== "object"
  )
    return null;
  const record = (payload.series as Record<string, unknown>)[diseaseId];
  if (!record || typeof record !== "object") return null;
  const series = record as Record<string, unknown>;
  const dates = series.dates;
  const starts = series.period_starts;
  const ends = series.period_ends_exclusive;
  const statuses = series.point_quality_statuses;
  if (
    series.disease_id !== diseaseId ||
    !Array.isArray(dates) ||
    !dates.length ||
    !dates.every((date) => typeof date === "string" && validDate(date)) ||
    dates.some((date, index) => index > 0 && date <= dates[index - 1]) ||
    !Array.isArray(starts) ||
    !Array.isArray(ends) ||
    !Array.isArray(statuses) ||
    [starts, ends, statuses].some((values) => values.length !== dates.length) ||
    !starts.every((start, index) =>
      start === null || (typeof start === "string" && validDate(start) && start === dates[index]),
    ) ||
    !ends.every((end, index) =>
      end === null ||
      (typeof end === "string" &&
        validDate(end) &&
        typeof starts[index] === "string" &&
        end > starts[index]),
    ) ||
    starts.some((start, index) => (start === null) !== (ends[index] === null)) ||
    !statuses.every((status) => typeof status === "string") ||
    ![series.cases, series.deaths, series.incidence_rates].every(
      (values) =>
        Array.isArray(values) &&
        values.length === dates.length &&
        values.every(
          (value) =>
            value === null ||
            (typeof value === "number" && Number.isFinite(value)),
        ),
    )
  )
    return null;
  return {
    ...(series as unknown as CountryDatasetSeriesEntry),
    snapshot_version: payload.version,
  };
}
