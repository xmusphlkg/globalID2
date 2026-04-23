import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { TaskItem } from "@/lib/hooks/useTasks";

export interface StartAITaskPayload {
  country_id: number;
  report_type?: "daily" | "weekly" | "monthly" | "special";
  language?: "zh" | "en";
  period_start?: string | null;
  period_end?: string | null;
  days?: number;
  enable_review?: boolean;
  send_email?: boolean;
  reuse_from_failed?: boolean;
  priority?: "low" | "normal" | "high" | "urgent";
  task_name?: string;
  description?: string;
}

export interface StartAITaskResult {
  id: number;
  task_uuid: string;
  task_name: string;
  task_type: string;
  status: string;
  priority: string;
  progress: number;
  country_id: number | null;
  report_id: number | null;
  description: string | null;
  last_error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  actual_duration: number | null;
  workbook_count: number;
  cancel_requested: boolean;
  cancel_requested_at: string | null;
}

export interface DiseaseKnowledgeCatalogueItem {
  disease_id: string;
  name_en: string | null;
  name_zh: string | null;
  category: string | null;
  icd_10: string | null;
  icd_11: string | null;
  description: string | null;
  slug: string | null;
  knowledge_status: string;
  knowledge_updated_at: string | null;
  published_languages: string[];
  source_count: number;
  brief_statuses: Record<string, string>;
}

export interface DiseaseKnowledgeDetailSource {
  id: number;
  disease_id: string;
  source_type: string;
  source_name: string;
  url: string;
  resolved_url: string | null;
  title: string | null;
  license: string | null;
  status: string;
  language: string;
  raw_excerpt: string | null;
  content_text: string | null;
  content_sections: Record<string, unknown>[];
  raw_excerpt_hash: string | null;
  fetched_at: string | null;
  review_status: string;
  metadata: Record<string, unknown>;
}

export interface DiseaseKnowledgeDetailBrief {
  language: string;
  status: string;
  source_confidence: string;
  updated_at: string | null;
  brief: string;
  definition: string | null;
  clinical_features: string | null;
  clinical_summary: string | null;
  epidemiology: string | null;
  transmission: string | null;
  prevention: string | null;
  surveillance_note: string | null;
  risk_groups: string | null;
  disclaimer: string | null;
  model: string | null;
  quality_score: number | null;
  review_notes: string | null;
  source_ids: number[];
  source_attribution: Record<string, unknown>[];
  metadata: Record<string, unknown>;
}

export interface DiseaseKnowledgeDetail {
  disease_id: string;
  name_en: string | null;
  name_zh: string | null;
  category: string | null;
  icd_10: string | null;
  icd_11: string | null;
  description: string | null;
  slug: string | null;
  knowledge_status: string;
  knowledge_updated_at: string | null;
  published_languages: string[];
  source_count: number;
  brief_statuses: Record<string, string>;
  summary: Record<string, unknown>;
  briefs: DiseaseKnowledgeDetailBrief[];
  sources: DiseaseKnowledgeDetailSource[];
}

export interface DiseaseKnowledgeSkippedItem {
  disease_id: string;
  reason: string;
  existing_task_uuid: string | null;
  existing_status: string | null;
}

export interface StartDiseaseKnowledgeTaskPayload {
  disease_ids: string[];
  source?: string[];
  force?: boolean;
  generator?: "ai" | "auto" | "template";
  priority?: "low" | "normal" | "high" | "urgent";
  task_name?: string;
  description?: string;
}

export interface StartDiseaseKnowledgeTaskResult {
  requested_disease_ids: string[];
  created_tasks: TaskItem[];
  skipped: DiseaseKnowledgeSkippedItem[];
}

export function useDiseaseKnowledgeCatalogue(search?: string) {
  return useQuery<DiseaseKnowledgeCatalogueItem[]>({
    queryKey: ["ai", "disease-knowledge", "catalogue", search ?? ""],
    queryFn: () => {
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      const suffix = params.toString() ? `?${params.toString()}` : "";
      return apiFetch(`/ai/disease-knowledge/catalogue${suffix}`);
    },
    staleTime: 30 * 1000,
  });
}

export function useDiseaseKnowledgeDetail(diseaseId: string | null) {
  return useQuery<DiseaseKnowledgeDetail>({
    queryKey: ["ai", "disease-knowledge", "detail", diseaseId],
    queryFn: () => apiFetch(`/ai/disease-knowledge/diseases/${diseaseId}`),
    enabled: !!diseaseId,
    staleTime: 30 * 1000,
  });
}

export function useStartAITask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: StartAITaskPayload) =>
      apiFetch("/ai/start", {
        method: "POST",
        body: JSON.stringify(payload),
      }) as Promise<StartAITaskResult>,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
      queryClient.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}

export function useStartDiseaseKnowledgeTasks() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: StartDiseaseKnowledgeTaskPayload) =>
      apiFetch("/ai/disease-knowledge/start", {
        method: "POST",
        body: JSON.stringify(payload),
      }) as Promise<StartDiseaseKnowledgeTaskResult>,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
      queryClient.invalidateQueries({ queryKey: ["ai", "disease-knowledge", "catalogue"] });
      queryClient.invalidateQueries({ queryKey: ["ai", "disease-knowledge", "detail"] });
    },
  });
}
