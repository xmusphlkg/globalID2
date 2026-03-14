import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface AIInteractionItem {
  id: number;
  report_id: number;
  report_uuid: string;
  report_status: string | null;
  report_title: string;
  country_id: number;
  section_id: number | null;
  section_type: string | null;
  section_title: string | null;
  disease_name: string | null;
  run_id: number;
  run_uuid: string | null;
  run_status: string | null;
  run_model: string | null;
  run_provider: string | null;
  run_temperature: number | null;
  agent: string | null;
  role: string | null;
  timestamp: string | null;
  model: string | null;
  provider: string | null;
  tokens: Record<string, unknown> | null;
  total_tokens: number;
  duration: number | null;
  quality_scores: Record<string, unknown> | null;
  quality_overall: number | null;
  system_prompt: string | null;
  prompt: string | null;
  response: string | null;
  temperature: number | null;
}

export interface AIInteractionSummary {
  total_interactions: number;
  total_tokens: number;
  avg_tokens: number;
  avg_duration: number;
  avg_quality: number | null;
  by_agent: Record<string, number>;
  by_model: Record<string, number>;
}

export interface AIInteractionFilters {
  countryId?: number | null;
  reportUuid?: string;
  agent?: string;
  model?: string;
  disease?: string;
  limit?: number;
}

function buildQuery(filters: AIInteractionFilters): string {
  const params = new URLSearchParams();
  if (filters.countryId) params.set("country_id", String(filters.countryId));
  if (filters.reportUuid) params.set("report_uuid", filters.reportUuid);
  if (filters.agent) params.set("agent", filters.agent);
  if (filters.model) params.set("model", filters.model);
  if (filters.disease) params.set("disease", filters.disease);
  if (filters.limit) params.set("limit", String(filters.limit));
  return params.toString();
}

export function useAIInteractions(filters: AIInteractionFilters) {
  return useQuery<AIInteractionItem[]>({
    queryKey: ["ai-interactions", filters],
    queryFn: () => {
      const query = buildQuery(filters);
      return apiFetch(`/ai/interactions${query ? `?${query}` : ""}`);
    },
    staleTime: 30 * 1000,
  });
}

export function useAIInteractionSummary(filters: Omit<AIInteractionFilters, "limit">) {
  return useQuery<AIInteractionSummary>({
    queryKey: ["ai-interactions-summary", filters],
    queryFn: () => {
      const query = buildQuery(filters);
      return apiFetch(`/ai/interactions/summary${query ? `?${query}` : ""}`);
    },
    staleTime: 30 * 1000,
  });
}
