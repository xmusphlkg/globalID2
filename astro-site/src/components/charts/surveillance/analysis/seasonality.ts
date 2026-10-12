import { DAY, inWindow } from "../domain/calendar.ts";
import type {
  Cadence,
  DateWindow,
  ResultPoint,
  SurveillanceSeries,
} from "../domain/types.ts";

export interface SeasonalGroup {
  year: string;
  points: ResultPoint[];
}

export interface SeasonalityResult {
  groups: SeasonalGroup[];
  reason: "unsupported_cadence" | "calendar_unknown" | null;
}

function isoWeek(date: Date): { year: number; week: number } {
  const thursday = new Date(date);
  thursday.setUTCDate(thursday.getUTCDate() + 3);
  const year = thursday.getUTCFullYear();
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const firstMonday = new Date(jan4);
  firstMonday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7));
  const monday = new Date(date);
  monday.setUTCHours(0, 0, 0, 0);
  return {
    year,
    week: Math.floor((monday.getTime() - firstMonday.getTime()) / (7 * DAY)) + 1,
  };
}

/** Align each source year to a calendar phase without changing its source period. */
export function alignSeasonality(
  series: SurveillanceSeries,
  points: ResultPoint[],
  window: DateWindow | null,
): SeasonalityResult {
  const cadence: Cadence = series.cadence;
  if (!["daily", "weekly", "monthly", "quarterly"].includes(cadence))
    return { groups: [], reason: "unsupported_cadence" };

  const grouped = new Map<string, ResultPoint[]>();
  for (const point of points) {
    const start = point.period.start;
    if (!start || point.period.calendar === "unknown")
      return { groups: [], reason: "calendar_unknown" };

    let year: string;
    let phaseDate: string;
    if (cadence === "weekly") {
      if (
        point.period.calendar !== "iso-week" ||
        point.period.precision !== "exact" ||
        !point.period.endExclusive ||
        Date.parse(`${point.period.endExclusive}T00:00:00Z`) -
          Date.parse(`${start}T00:00:00Z`) !==
          7 * DAY
      )
        return { groups: [], reason: "calendar_unknown" };
      const date = new Date(`${start}T00:00:00Z`);
      if (date.getUTCDay() !== 1)
        return { groups: [], reason: "calendar_unknown" };
      const { year: isoYear, week } = isoWeek(date);
      // ISO 2004 has week 53, so every ISO week has a distinct reference slot.
      const jan4 = new Date(Date.UTC(2004, 0, 4));
      const firstMonday = new Date(jan4);
      firstMonday.setUTCDate(
        jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7),
      );
      const phase = new Date(firstMonday);
      phase.setUTCDate(firstMonday.getUTCDate() + (week - 1) * 7);
      year = String(isoYear);
      phaseDate = phase.toISOString().slice(0, 10);
    } else {
      if (point.period.calendar !== "gregorian")
        return { groups: [], reason: "calendar_unknown" };
      year = start.slice(0, 4);
      phaseDate = `2000${start.slice(4)}`;
    }

    if (!inWindow(point.date, window)) continue;
    const aligned = { ...point, date: phaseDate };
    grouped.set(year, [...(grouped.get(year) ?? []), aligned]);
  }
  return {
    groups: [...grouped.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([year, yearPoints]) => ({ year, points: yearPoints })),
    reason: null,
  };
}
