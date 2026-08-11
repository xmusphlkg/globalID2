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

export function useOverviewSummary(countryCode: string | null, lang: string) {
  return useQuery<OverviewSummary>({
    queryKey: ["overview", "summary", countryCode, lang],
    queryFn: () =>
      apiFetch(`/analytics/summary?country_code=${encodeURIComponent(countryCode || "")}&lang=${lang}`),
    enabled: !!countryCode,
    staleTime: 5 * 60 * 1000,
  });
}

export function useOverviewTrend(
  countryCode: string | null,
  diseaseCode?: string | null,
  interval?: number | null,
  startDate?: string | null,
  endDate?: string | null,
) {
  return useQuery<TrendPoint[]>({
    queryKey: ["overview", "trend", countryCode, diseaseCode, interval, startDate, endDate],
    queryFn: () => {
      const params = new URLSearchParams({ country_code: countryCode || "" });
      if (diseaseCode) params.set("disease_code", diseaseCode);
      if (startDate) params.set("start_date", startDate);
      if (endDate) params.set("end_date", endDate);
      if (interval && !startDate && !endDate) params.set("interval", String(interval));
      return apiFetch(`/analytics/trends?${params}`);
    },
    enabled: !!countryCode,
    staleTime: 5 * 60 * 1000,
  });
}

export function useOverviewMonthlyComparison(
  countryCode: string | null,
  diseaseCode?: string | null,
  interval?: number | null,
  startDate?: string | null,
  endDate?: string | null,
) {
  return useQuery<MonthlyComparisonPoint[]>({
    queryKey: ["overview", "monthly-comparison", countryCode, diseaseCode, interval, startDate, endDate],
    queryFn: () => {
      const params = new URLSearchParams({ country_code: countryCode || "" });
      if (diseaseCode) params.set("disease_code", diseaseCode);
      if (startDate) params.set("start_date", startDate);
      if (endDate) params.set("end_date", endDate);
      if (interval && !startDate && !endDate) params.set("interval", String(interval));
      return apiFetch(`/analytics/monthly-comparison?${params}`);
    },
    enabled: !!countryCode,
    staleTime: 5 * 60 * 1000,
  });
}
