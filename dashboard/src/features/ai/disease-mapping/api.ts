import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export interface MappingCandidate {
  id: number;
  candidate_key: string;
  target_code?: string | null;
  proposed_name_en?: string | null;
  candidate_kind: string;
  mapping_relation: string;
  comparability: string;
  confidence_score: number;
  reasoning?: string | null;
  model_key?: string | null;
  status: string;
}

export interface SourceCategory {
  id: number;
  category_key: string;
  country_code: string;
  source_id: string;
  source_code: string;
  canonical_source_label: string;
  status: string;
  ai_status: string;
  ai_last_error?: string | null;
  definition_version: string;
  mapping_status: "reviewed" | "review_ready" | "awaiting_suggestion";
  automation_failure_kind?: "provider_unavailable" | "internal_processing_error" | null;
  candidates: MappingCandidate[];
  assertions: Array<{ id: number; target_code: string; mapping_relation: string; projection_policy: string }>;
}

export interface MappingSummary {
  category_total: number;
  ai_pending_total: number;
  ai_failed_total: number;
  ai_internal_failed_total: number;
  ai_provider_unavailable_total: number;
  ai_review_ready_total: number;
  assertions: Record<string, number>;
  candidates: Record<string, number>;
  countries: Array<{ country_code: string; categories: number; ai_pending: number; ai_failed: number; ai_provider_unavailable: number; ai_review_ready: number; active: number }>;
  active_release?: { id: number; release_code: string; activated_at?: string | null } | null;
  automation: { enabled: boolean; running: boolean; email_provider: string; ai_circuit_open: boolean; ai_circuit_until?: string | null; last_cycle?: Record<string, unknown> };
}

export interface MappingCoverage {
  observation_total: number;
  mapped_total: number;
  mapping_coverage: number;
  canonical_total: number;
  canonical_coverage: number;
  no_projection_total: number;
  discovery_only_total: number;
  source_only_total: number;
  undecided_total: number;
  registered_series_total: number;
  observed_series_total: number;
  holding_observation_total: number;
  holding_series_total: number;
  countries: Array<{
    country_code: string;
    observation_count: number;
    mapped_count: number;
    mapping_coverage: number;
    canonical_count: number;
    canonical_coverage: number;
    no_projection_count: number;
    discovery_only_count: number;
    source_only_count: number;
    undecided_count: number;
    registered_series_count: number;
    observed_series_count: number;
    holding_observation_count: number;
    holding_series_count: number;
  }>;
}

export interface MappingRelease {
  id: number;
  release_code: string;
  status: string;
  checksum: string;
  created_at: string;
  activated_at?: string | null;
  metadata?: { assertion_count?: number };
}

const root = "/mappings";

export function useMappingSummary() {
  return useQuery({ queryKey: ["mapping-v3-summary"], queryFn: () => apiFetch<MappingSummary>(`${root}/summary`), refetchInterval: 15_000 });
}

export function useMappingCoverage() {
  return useQuery({ queryKey: ["mapping-v3-coverage"], queryFn: () => apiFetch<MappingCoverage>(`${root}/coverage`, { timeoutMs: 60_000 }) });
}

export function useMappingCategories(countryCode: string, aiStatus: string) {
  const params = new URLSearchParams({ page: "1", page_size: "300" });
  if (countryCode) params.set("country_code", countryCode);
  if (aiStatus) params.set("ai_status", aiStatus);
  return useQuery({
    queryKey: ["mapping-v3-categories", countryCode, aiStatus],
    queryFn: () => apiFetch<SourceCategory[]>(`${root}/categories?${params}`),
  });
}

export function useMappingReleases() {
  return useQuery({ queryKey: ["mapping-v3-releases"], queryFn: () => apiFetch<MappingRelease[]>(`${root}/releases`) });
}

function useMappingMutation<TVariables>(mutationFn: (variables: TVariables) => Promise<unknown>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => client.invalidateQueries(),
  });
}

export function useRunMappingAutomation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch(`${root}/automation/runs`, { method: "POST", timeoutMs: 180_000 }),
    onSuccess: () => client.invalidateQueries(),
  });
}

export function useSyncReviewedMappings() {
  return useMappingMutation<void>(() => apiFetch(`${root}/bootstrap`, {
    method: "POST",
    timeoutMs: 180_000,
  }));
}

export function useSuggestCategory() {
  return useMappingMutation<string>((key) => apiFetch(`${root}/categories/${encodeURIComponent(key)}/suggest`, { method: "POST", timeoutMs: 180_000 }));
}

export function useReviewCandidate(action: "accept" | "reject") {
  return useMappingMutation<string>((key) => apiFetch(`${root}/candidates/${encodeURIComponent(key)}/${action}`, {
    method: "POST",
    body: JSON.stringify({ reviewer: "control-panel", notes: "Reviewed in Mapping Registry v3 control panel" }),
  }));
}

export function useCreateMappingRelease() {
  return useMappingMutation<string>((releaseCode) => apiFetch(`${root}/releases`, {
    method: "POST",
    body: JSON.stringify({ release_code: releaseCode, created_by: "control-panel", description: "Global Mapping Registry v3 release" }),
  }));
}

export function useActivateMappingRelease() {
  return useMappingMutation<string>((code) => apiFetch(`${root}/releases/${encodeURIComponent(code)}/activate`, { method: "POST" }));
}
