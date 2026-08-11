import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface QualityStats {
  total_records: number;
  unique_diseases: number;
  earliest_date: string | null;
  latest_date: string | null;
  zero_cases_count: number;
  zero_cases_pct: number;
  zero_deaths_count: number;
  zero_deaths_pct: number;
}

export interface TimeGap {
  period_start: string;
  next_period: string | null;
  gap_periods: number;
  period_unit: string;
}

export interface DataSourceDist {
  data_source: string | null;
  count: number;
  percentage: number;
}

export interface CompletenessItem {
  disease_name: string;
  data_periods: number;
  expected_periods: number;
  completeness_rate: number;
  earliest_date: string | null;
  latest_date: string | null;
  total_records: number;
  period_unit: string;
}

export function useQualityStats(countryCode: string | null) {
  return useQuery<QualityStats>({
    queryKey: ["quality", "stats", countryCode],
    queryFn: () => apiFetch(`/quality/stats?country_code=${encodeURIComponent(countryCode || "")}`),
    enabled: !!countryCode,
    staleTime: 5 * 60 * 1000,
  });
}

export function useQualityGaps(countryCode: string | null) {
  return useQuery<TimeGap[]>({
    queryKey: ["quality", "gaps", countryCode],
    queryFn: () => apiFetch(`/quality/gaps?country_code=${encodeURIComponent(countryCode || "")}`),
    enabled: !!countryCode,
    staleTime: 5 * 60 * 1000,
  });
}

export function useQualitySources(countryCode: string | null) {
  return useQuery<DataSourceDist[]>({
    queryKey: ["quality", "sources", countryCode],
    queryFn: () =>
      apiFetch(countryCode ? `/quality/sources?country_code=${encodeURIComponent(countryCode)}` : "/quality/sources"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useQualityCompleteness(
  countryCode: string | null,
  start?: string,
  end?: string,
  lang = "en",
) {
  return useQuery<CompletenessItem[]>({
    queryKey: ["quality", "completeness", countryCode, start, end, lang],
    queryFn: () => {
      const params = new URLSearchParams({
        country_code: countryCode || "",
        lang,
      });
      if (start) params.set("start", start);
      if (end) params.set("end", end);
      return apiFetch(`/quality/completeness?${params}`);
    },
    enabled: !!countryCode,
    staleTime: 5 * 60 * 1000,
  });
}
