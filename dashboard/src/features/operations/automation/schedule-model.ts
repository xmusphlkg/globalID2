export type ScheduleMode = "daily" | "interval";
export type IntervalUnit = "minutes" | "hours";
export const DEFAULT_INTERVAL_MINUTES = 60;
export const dailyPresetTimes = ["00:00", "08:00", "12:00", "18:00"] as const;
export const intervalPresetMinutes = [15, 30, 60, 180, 360, 720] as const;
export function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function intervalMinutesToFields(minutes?: number | null): { value: string; unit: IntervalUnit } {
  const safeMinutes = minutes && minutes > 0 ? minutes : DEFAULT_INTERVAL_MINUTES;
  if (safeMinutes % 60 === 0) {
    return { value: String(safeMinutes / 60), unit: "hours" };
  }
  return { value: String(safeMinutes), unit: "minutes" };
}

export function intervalFieldsToMinutes(value: string, unit: IntervalUnit): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return unit === "hours" ? parsed * 60 : parsed;
}

export function describeIntervalMinutes(minutes: number, lang: "en" | "zh"): string {
  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return lang === "zh" ? `每 ${hours} 小时运行一次` : `Every ${hours} hour(s)`;
  }
  return lang === "zh" ? `每 ${minutes} 分钟运行一次` : `Every ${minutes} minute(s)`;
}

export function scheduleLabel(
  job: { interval_minutes?: number | null; daily_time?: string | null; timezone?: string | null },
  lang: "en" | "zh",
): string {
  if (job.interval_minutes) return describeIntervalMinutes(job.interval_minutes, lang);
  if (job.daily_time) {
    return lang === "zh"
      ? `每天 ${job.daily_time} (${job.timezone || "UTC"})`
      : `Daily at ${job.daily_time} (${job.timezone || "UTC"})`;
  }
  return lang === "zh" ? "手动运行" : "Manual";
}
