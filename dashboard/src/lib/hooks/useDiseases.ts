import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface DiseaseListItem {
  code: string;
  display_name: string;
  display_name_en: string | null;
}

export interface DiseaseRecord {
  time: string;
  disease_id: number;
  country_id: number;
  cases: number | null;
  deaths: number | null;
  recoveries: number | null;
  active_cases: number | null;
  new_cases: number | null;
  new_deaths: number | null;
  new_recoveries: number | null;
  incidence_rate: number | null;
  mortality_rate: number | null;
  recovery_rate: number | null;
  region: string | null;
  city: string | null;
  data_source: string | null;
  data_quality: string | null;
  confidence_score: number | null;
  data_layer?: "series_registry" | "legacy_gap_fill" | "legacy_fallback";
  projection_policy?: string | null;
  series_codes?: string[];
  loss_risk?: string | null;
  gap_fill_reason?: string | null;
  coverage?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
}

export interface CompareResult {
  diseases: {
    disease_code: string;
    disease_name: string;
    data: { time_period: string; cases: number; deaths: number }[];
    data_layer?: string | null;
    projection_policy?: string | null;
    loss_risk?: string | null;
    coverage?: Record<string, unknown>;
    provenance?: Record<string, unknown>;
  }[];
}

export function useDiseases(countryId: number | null, lang: string) {
  return useQuery<DiseaseListItem[]>({
    queryKey: ["diseases", countryId, lang],
    queryFn: () =>
      apiFetch(`/diseases?country_id=${countryId}&lang=${lang}`),
    enabled: !!countryId,
    staleTime: 10 * 60 * 1000,
  });
}

export function useDiseaseRecords(
  code: string | null,
  countryId: number | null,
) {
  return useQuery<DiseaseRecord[]>({
    queryKey: ["disease-records", code, countryId],
    queryFn: () =>
      apiFetch(`/diseases/${code}/records?country_id=${countryId}`),
    enabled: !!code && !!countryId,
    staleTime: 5 * 60 * 1000,
  });
}

export function useCompare(
  countryId: number | null,
  codes: string[],
) {
  const joined = codes.join(",");
  return useQuery<CompareResult>({
    queryKey: ["compare", countryId, joined],
    queryFn: () =>
      apiFetch(
        `/analysis/compare?country_id=${countryId}&diseases=${encodeURIComponent(joined)}`,
      ),
    enabled: !!countryId && codes.length > 0,
    staleTime: 5 * 60 * 1000,
  });
}
