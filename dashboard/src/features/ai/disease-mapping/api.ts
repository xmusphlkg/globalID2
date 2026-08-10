import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export interface MappingCandidate {
  id: number;
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
  candidates: MappingCandidate[];
  assertions: Array<{ id: number; target_code: string; mapping_relation: string; projection_policy: string }>;
}

export interface MappingSummary {
  category_total: number;
  ai_pending_total: number;
  assertions: Record<string, number>;
  candidates: Record<string, number>;
  countries: Array<{ country_code: string; categories: number; ai_pending: number; active: number }>;
  active_release?: { id: number; release_code: string; activated_at?: string | null } | null;
  automation: { enabled: boolean; running: boolean; email_provider: string; last_cycle?: Record<string, unknown> };
}

export interface MappingCoverage {
  observation_total: number;
  canonical_total: number;
  canonical_coverage: number;
  countries: Array<{ country_code: string; observation_count: number; mapped_count: number; canonical_count: number; canonical_coverage: number }>;
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

export interface MappingAudit {
  generated_at: string;
  totals: {
    observations: number; old_mapped: number; v3_mapped: number; same_target: number;
    old_only: number; v3_only: number; changed_target: number;
    exact_migration_gap: number; semantic_safety_exclusion: number;
    source_native_unreviewed: number; old_coverage: number; v3_coverage: number;
  };
  quality_gates: {
    active_release: string; orphan_observations: number; active_release_conflicts: number;
    no_unreviewed_target_changes: boolean; no_orphan_observations: boolean;
    single_mapping_per_category: boolean;
  };
  top_gaps: Array<{
    country_code: string; series_code: string; source_label: string; old_target: string;
    mapping_relation: string; comparability: string; observations: number; root_cause: string;
  }>;
}

const root = "/disease-mappings/v3";

export function useMappingSummary() {
  return useQuery({ queryKey: ["mapping-v3-summary"], queryFn: () => apiFetch<MappingSummary>(`${root}/summary`), refetchInterval: 15_000 });
}

export function useMappingCoverage() {
  return useQuery({ queryKey: ["mapping-v3-coverage"], queryFn: () => apiFetch<MappingCoverage>(`${root}/coverage`, { timeoutMs: 60_000 }) });
}

export function useMappingAudit() {
  return useQuery({ queryKey: ["mapping-v3-audit"], queryFn: () => apiFetch<MappingAudit>(`${root}/audit`, { timeoutMs: 60_000 }) });
}

export function useMappingCategories(countryCode: string, aiStatus: string) {
  const params = new URLSearchParams({ limit: "300" });
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
    mutationFn: () => apiFetch(`${root}/automation/run`, { method: "POST", timeoutMs: 180_000 }),
    onSuccess: () => client.invalidateQueries(),
  });
}

export function useSuggestCategory() {
  return useMappingMutation<number>((id) => apiFetch(`${root}/categories/${id}/suggest`, { method: "POST", timeoutMs: 180_000 }));
}

export function useReviewCandidate(action: "accept" | "reject") {
  return useMappingMutation<number>((id) => apiFetch(`${root}/candidates/${id}/${action}`, {
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
  return useMappingMutation<number>((id) => apiFetch(`${root}/releases/${id}/activate`, { method: "POST" }));
}
