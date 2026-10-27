import type { Cadence, DateWindow, Period } from "./types.ts";

export const DAY = 86_400_000;
export const isoDate = (date: Date) => date.toISOString().slice(0, 10);
export function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && isoDate(date) === value;
}
export function cadence(value?: string | null): Cadence {
  const aliases: Record<string, Cadence> = {
    day: "daily",
    daily: "daily",
    week: "weekly",
    weekly: "weekly",
    month: "monthly",
    monthly: "monthly",
    quarter: "quarterly",
    quarterly: "quarterly",
    year: "annual",
    yearly: "annual",
    annual: "annual",
  };
  return aliases[String(value ?? "").toLowerCase()] ?? "unknown";
}
export function periodFor(label: string, grain: Cadence): Period {
  const unknown: Period = {
    label,
    start: null,
    endExclusive: null,
    calendar: "unknown",
    precision: "unknown",
  };
  if (!validDate(label) || grain === "unknown") return unknown;
  const date = new Date(`${label}T00:00:00Z`);
  const year = date.getUTCFullYear(),
    month = date.getUTCMonth();
  if (grain === "weekly") return { ...unknown, calendar: "source" };
  const start =
    grain === "annual"
      ? new Date(Date.UTC(year, 0, 1))
      : grain === "quarterly"
        ? new Date(Date.UTC(year, Math.floor(month / 3) * 3, 1))
        : grain === "monthly"
          ? new Date(Date.UTC(year, month, 1))
          : date;
  const end = new Date(start);
  if (grain === "daily") end.setUTCDate(end.getUTCDate() + 1);
  else
    end.setUTCMonth(
      end.getUTCMonth() +
        (grain === "annual" ? 12 : grain === "quarterly" ? 3 : 1),
    );
  return {
    label,
    start: isoDate(start),
    endExclusive: isoDate(end),
    calendar: "gregorian",
    precision: "inferred",
  };
}
export function periodKey(label: string, grain: Cadence): string {
  const p = periodFor(label, grain);
  return p.start ?? label;
}
export function windowFor(dates: string[]): DateWindow | null {
  const sorted = dates.filter(validDate).sort();
  return sorted.length
    ? { startDate: sorted[0], endDate: sorted[sorted.length - 1] }
    : null;
}
export function inWindow(date: string, window: DateWindow | null): boolean {
  return !window || (date >= window.startDate && date <= window.endDate);
}
export function trailingWindow(domain: DateWindow, months: number): DateWindow {
  const end = new Date(`${domain.endDate}T00:00:00Z`);
  const day = end.getUTCDate();
  end.setUTCDate(1);
  end.setUTCMonth(end.getUTCMonth() - months);
  const lastDay = new Date(
    Date.UTC(end.getUTCFullYear(), end.getUTCMonth() + 1, 0),
  ).getUTCDate();
  end.setUTCDate(Math.min(day, lastDay));
  return {
    startDate:
      isoDate(end) < domain.startDate ? domain.startDate : isoDate(end),
    endDate: domain.endDate,
  };
}
export function expectedCount(
  window: DateWindow | null,
  grain: Cadence,
): number | null {
  if (!window || grain === "unknown" || grain === "weekly") return null;
  const a = new Date(`${window.startDate}T00:00:00Z`),
    b = new Date(`${window.endDate}T00:00:00Z`);
  if (grain === "daily") return Math.floor((+b - +a) / DAY) + 1;
  const first = periodFor(window.startDate, grain);
  const firstDate = new Date(`${first.start}T00:00:00Z`);
  if (first.start! < window.startDate)
    firstDate.setUTCMonth(
      firstDate.getUTCMonth() +
        (grain === "annual" ? 12 : grain === "quarterly" ? 3 : 1),
    );
  if (firstDate > b) return 0;
  const months =
    (b.getUTCFullYear() - firstDate.getUTCFullYear()) * 12 +
    b.getUTCMonth() -
    firstDate.getUTCMonth();
  return (
    Math.floor(
      months / (grain === "annual" ? 12 : grain === "quarterly" ? 3 : 1),
    ) + 1
  );
}
