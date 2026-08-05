import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatNumber(n: number): string {
  return n.toLocaleString();
}

export function parseApiDate(value?: string | Date | null): Date | null {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;

  // ISO timestamps without an offset are UTC in the API contract. Explicitly
  // append Z so browser and host timezone differences cannot change the instant.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(d: string | Date): string {
  const date = parseApiDate(d);
  if (!date) return "—";
  return date.toLocaleDateString("en-CA"); // YYYY-MM-DD
}

export function formatDateTime(value?: string | Date | null): string {
  const date = parseApiDate(value);
  if (!date) return value ? String(value) : "-";
  return date.toLocaleString();
}

export function formatRelativeTime(value?: string | Date | null, locale?: string): string {
  const date = parseApiDate(value);
  if (!date) return value ? String(value) : "-";

  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (Math.abs(seconds) < 60) return formatter.format(seconds, "second");
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
  return formatter.format(Math.round(hours / 24), "day");
}
