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

export function useQualityStats(countryId: number | null) {
  return useQuery<QualityStats>({
    queryKey: ["quality", "stats", countryId],
    queryFn: () => apiFetch(`/quality/stats?country_id=${countryId}`),
    enabled: !!countryId,
    staleTime: 5 * 60 * 1000,
  });
}

export function useQualityGaps(countryId: number | null) {
  return useQuery<TimeGap[]>({
    queryKey: ["quality", "gaps", countryId],
    queryFn: () => apiFetch(`/quality/gaps?country_id=${countryId}`),
    enabled: !!countryId,
    staleTime: 5 * 60 * 1000,
  });
}

export function useQualitySources(countryId: number | null) {
  return useQuery<DataSourceDist[]>({
    queryKey: ["quality", "sources", countryId],
    queryFn: () =>
      apiFetch(countryId ? `/quality/sources?country_id=${countryId}` : "/quality/sources"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useQualityCompleteness(
  countryId: number | null,
  start?: string,
  end?: string,
  lang = "en",
) {
  return useQuery<CompletenessItem[]>({
    queryKey: ["quality", "completeness", countryId, start, end, lang],
    queryFn: () => {
      const params = new URLSearchParams({
        country_id: String(countryId),
        lang,
      });
      if (start) params.set("start", start);
      if (end) params.set("end", end);
      return apiFetch(`/quality/completeness?${params}`);
    },
    enabled: !!countryId,
    staleTime: 5 * 60 * 1000,
  });
}
