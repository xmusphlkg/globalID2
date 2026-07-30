import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface OverviewSummary {
  total_diseases: number;
  total_records: number;
  earliest_date: string | null;
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
  incidence_rate: number | null;
  mortality_rate: number | null;
}

export interface MonthlyComparisonPoint {
  year: number;
  month: number;
  cases: number;
  deaths: number;
  incidence_rate: number | null;
  mortality_rate: number | null;
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
  startDate?: string | null,
  endDate?: string | null,
) {
  return useQuery<TrendPoint[]>({
    queryKey: ["overview", "trend", countryId, diseaseCode, interval, startDate, endDate],
    queryFn: () => {
      const params = new URLSearchParams({ country_id: String(countryId) });
      if (diseaseCode) params.set("disease_code", diseaseCode);
      if (startDate) params.set("start_date", startDate);
      if (endDate) params.set("end_date", endDate);
      if (interval && !startDate && !endDate) params.set("interval", String(interval));
      return apiFetch(`/overview/trend?${params}`);
    },
    enabled: !!countryId,
    staleTime: 5 * 60 * 1000,
  });
}

export function useOverviewMonthlyComparison(
  countryId: number | null,
  diseaseCode?: string | null,
  interval?: number | null,
  startDate?: string | null,
  endDate?: string | null,
) {
  return useQuery<MonthlyComparisonPoint[]>({
    queryKey: ["overview", "monthly-comparison", countryId, diseaseCode, interval, startDate, endDate],
    queryFn: () => {
      const params = new URLSearchParams({ country_id: String(countryId) });
      if (diseaseCode) params.set("disease_code", diseaseCode);
      if (startDate) params.set("start_date", startDate);
      if (endDate) params.set("end_date", endDate);
      if (interval && !startDate && !endDate) params.set("interval", String(interval));
      return apiFetch(`/overview/monthly-comparison?${params}`);
    },
    enabled: !!countryId,
    staleTime: 5 * 60 * 1000,
  });
}
