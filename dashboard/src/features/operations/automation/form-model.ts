import type { AutomationJob, AutomationJobInput, CountrySourceConfig } from "./api";
import type { Country } from "@/shared/config/countries";

export type AutomationPreset = Partial<AutomationJobInput> & {
  id: string;
  label: string;
  job_id: string;
  name: string;
  country_code: string;
  source: string;
  daily_time: string;
};

export const defaultForm: AutomationJobInput = {
  job_id: "",
  name: "",
  country_code: "CN",
  source: "all",
  enabled: true,
  priority: "normal",
  process: true,
  save_raw: true,
  fill_missing: true,
  force: false,
  include_current_month: false,
  revision_window_months: 3,
  retry_threshold: 3,
  interval_minutes: null,
  daily_time: "08:00",
  timezone: "Asia/Shanghai",
  notes: "",
};

export function toForm(job: AutomationJob): AutomationJobInput {
  return {
    job_id: job.job_id,
    name: job.name,
    country_code: job.country_code,
    source: job.source,
    enabled: job.enabled,
    priority: job.priority,
    process: job.process,
    save_raw: job.save_raw,
    fill_missing: job.fill_missing,
    force: job.force,
    include_current_month: job.include_current_month ?? false,
    revision_window_months: job.revision_window_months ?? 3,
    retry_threshold: job.retry_threshold,
    interval_minutes: job.interval_minutes ?? null,
    daily_time: job.daily_time ?? "",
    timezone: job.timezone ?? "",
    notes: job.notes ?? "",
  };
}

export function findCountryTimezone(countries: Country[] | undefined, code: string): string | null {
  return countries?.find((country) => country.code === code)?.timezone ?? null;
}

export function defaultFillMissingForCountry(config?: CountrySourceConfig | null): boolean {
  return config?.default_fill_missing ?? true;
}

export function defaultIncludeCurrentMonthForCountry(config?: CountrySourceConfig | null): boolean {
  return config?.source_policy?.default_include_current_month ?? false;
}

export function defaultRevisionWindowForCountry(config?: CountrySourceConfig | null): number {
  return Math.max(1, config?.source_policy?.default_revision_window ?? config?.source_policy?.default_revision_window_months ?? 3);
}

export function presetTimeForIndex(index: number): string {
  const totalMinutes = 8 * 60 + index * 15;
  const hour = Math.floor(totalMinutes / 60) % 24;
  const minute = totalMinutes % 60;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}
