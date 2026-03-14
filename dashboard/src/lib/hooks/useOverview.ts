import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface OverviewSummary {
  total_diseases: number;
  total_records: number;
  latest_date: string | null;
  recent_cases_30d: number;
  top_diseases: TopDiseaseItem[];
}

export interface TopDiseaseItem {
  name: string;
  name_en: string | null;
  total_cases: number;
  total_deaths: number;
}

export interface TrendPoint {
  time_period: string;
  cases: number;
  deaths: number;
}

export function useOverviewSummary(countryId: number | null, lang: string) {
  return useQuery<OverviewSummary>({
    queryKey: ["overview", "summary", countryId, lang],
    queryFn: () =>
      apiFetch(`/overview/summary?country_id=${countryId}&lang=${lang}`),
    enabled: !!countryId,
    staleTime: 5 * 60 * 1000,
  });
}

export function useOverviewTrend(
  countryId: number | null,
  diseaseCode?: string | null,
  interval?: number | null,
) {
  return useQuery<TrendPoint[]>({
    queryKey: ["overview", "trend", countryId, diseaseCode, interval],
    queryFn: () => {
      const params = new URLSearchParams({ country_id: String(countryId) });
      if (diseaseCode) params.set("disease_code", diseaseCode);
      if (interval) params.set("interval", String(interval));
      return apiFetch(`/overview/trend?${params}`);
    },
    enabled: !!countryId,
    staleTime: 5 * 60 * 1000,
  });
}
