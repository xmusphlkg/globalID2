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
  month: string;
  next_month: string | null;
  gap_months: number;
}

export interface DataSourceDist {
  data_source: string | null;
  count: number;
  percentage: number;
}

export interface CompletenessItem {
  disease_name: string;
  data_months: number;
  expected_months: number;
  completeness_rate: number;
  earliest_date: string | null;
  latest_date: string | null;
  total_records: number;
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
