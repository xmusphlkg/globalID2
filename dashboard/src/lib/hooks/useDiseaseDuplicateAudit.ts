import { useMutation } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface DiseaseAuditFinding {
  category: string;
  finding: string;
  term?: string;
  candidate_ids?: string[];
  samples?: Array<Record<string, unknown>>;
  catalogue_rows?: Array<Record<string, unknown>>;
  country_code?: string;
  raw_terms?: string[];
  files?: string[];
  row_count?: number;
  token_overlap?: number;
  text_similarity?: number;
}

export interface DiseaseAuditRecommendation {
  finding: string;
  category?: string;
  candidate_ids?: string[];
  decision: "merge" | "keep_separate" | "add_standard_disease" | "needs_human_review" | string;
  confidence?: "high" | "medium" | "low" | string;
  canonical_id?: string | null;
  merge_ids?: string[];
  proposed_standard_name_en?: string | null;
  proposed_standard_name_zh?: string | null;
  rationale_zh?: string;
  rationale_en?: string;
  suggested_actions?: string[];
}

export interface DiseaseDuplicateAuditResult {
  generated_at: string;
  standard_catalogue: string;
  mapping_directory: string;
  current_data_directory: string;
  summary: {
    high_confidence_standard_duplicates: number;
    mapping_term_review_candidates: number;
    similar_name_review_candidates: number;
    new_disease_candidates: number;
  };
  high_confidence_standard_duplicates: DiseaseAuditFinding[];
  mapping_term_review_candidates: DiseaseAuditFinding[];
  similar_name_review_candidates: DiseaseAuditFinding[];
  new_disease_candidates: DiseaseAuditFinding[];
  ai_review: {
    summary?: {
      merge?: number;
      keep_separate?: number;
      add_standard_disease?: number;
      needs_human_review?: number;
    };
    recommendations?: DiseaseAuditRecommendation[];
    warnings?: string[];
    model_route?: {
      model_id?: number;
      model_key?: string;
      model_name?: string;
      provider_key?: string;
      provider_name?: string;
    };
    raw_response?: string;
  } | null;
}

export interface DiseaseDuplicateAuditStatus {
  generated_at: string;
  module: string;
  local_summary: DiseaseDuplicateAuditResult["summary"];
  model_center: {
    route_count: number;
    active_route_count: number;
    routes: Array<{
      model_id?: number;
      model_key?: string;
      model_name?: string;
      provider_id?: number;
      provider_key?: string;
      provider_name?: string;
      api_style?: string;
      priority?: number;
      has_api_key?: boolean;
      available_for_routing?: boolean;
      last_check_status?: string | null;
      rate_limit_active?: boolean;
      rate_limit_scope?: string | null;
      rate_limit_cooldown_until?: string | null;
      rate_limit_remaining_seconds?: number;
    }>;
  };
}

export interface DiseaseDuplicateAuditPayload {
  include_ai: boolean;
  include_new_disease_candidates: boolean;
  max_ai_candidates: number;
}

export function useRunDiseaseDuplicateAudit() {
  return useMutation({
    mutationFn: (payload: DiseaseDuplicateAuditPayload) =>
      apiFetch<DiseaseDuplicateAuditResult>("/ai/disease-audit/run", {
        method: "POST",
        body: JSON.stringify(payload),
        timeoutMs: 180000,
      }),
  });
}

export function useDiseaseDuplicateAuditStatus(includeNewDiseaseCandidates = true) {
  const suffix = includeNewDiseaseCandidates ? "true" : "false";
  return useQuery<DiseaseDuplicateAuditStatus>({
    queryKey: ["disease-duplicate-audit-status", includeNewDiseaseCandidates],
    queryFn: () =>
      apiFetch(`/ai/disease-audit/status?include_new_disease_candidates=${suffix}`, {
        timeoutMs: 60000,
      }),
    staleTime: 20 * 1000,
    retry: false,
  });
}
