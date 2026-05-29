import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface ReportListItem {
  id: number;
  report_uuid: string;
  title: string;
  report_type: string;
  status: string;
  country_id: number;
  country_name: string | null;
  period_start: string;
  period_end: string;
  quality_score: number | null;
  generation_time: number | null;
  section_count: number;
  primary_disease?: string | null;
  disease_names?: string[];
  created_at: string;
}

export interface ReportDetail {
  id: number;
  report_uuid: string;
  title: string;
  report_type: string;
  status: string;
  country_id: number;
  country_name: string | null;
  period_start: string;
  period_end: string;
  summary: string | null;
  key_findings: string[];
  recommendations: string[];
  quality_score: number | null;
  generation_time: number | null;
  token_usage: Record<string, unknown> | null;
  ai_model_used: string | null;
  error_message: string | null;
  metadata: Record<string, unknown> | null;
  analysis_summary: Record<string, unknown> | null;
  quality_gate: Record<string, unknown> | null;
  data_quality: Record<string, unknown> | null;
  method_version: string | null;
  created_at: string;
  sections: ReportSection[];
}

export interface ReportSection {
  id: number;
  section_type: string | null;
  section_order: number;
  title: string | null;
  content: string | null;
  ai_model: string | null;
  generation_time: number | null;
  data_sources: unknown;
  charts: unknown;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface AIConversation {
  id: number;
  agent: string | null;
  timestamp: string | null;
  prompt: string | null;
  response: string | null;
  model: string | null;
  provider: string | null;
  tokens: Record<string, unknown> | null;
}

export interface ReportSectionRun {
  id: number;
  run_uuid: string | null;
  section_id: number | null;
  disease_name: string | null;
  section_type: string | null;
  status: string;
  model: string | null;
  provider: string | null;
  temperature: number | null;
  max_tokens: number | null;
  token_usage: Record<string, unknown> | null;
  quality_scores: Record<string, unknown> | null;
  revision_count: number;
  error_message: string | null;
  started_at: string | null;
  ended_at: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export function useReports(
  countryId: number | null,
  status?: string,
  limit = 50,
) {
  return useQuery<ReportListItem[]>({
    queryKey: ["reports", countryId, status, limit],
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(limit) });
      if (countryId) params.set("country_id", String(countryId));
      if (status) params.set("status", status);
      return apiFetch(`/reports?${params}`);
    },
    staleTime: 60 * 1000,
  });
}

export function useReportDetail(uuid: string | null) {
  return useQuery<ReportDetail>({
    queryKey: ["report", uuid],
    queryFn: () => apiFetch(`/reports/${uuid}`),
    enabled: !!uuid,
    staleTime: 60 * 1000,
  });
}

export function useSectionConversations(
  reportUuid: string | null,
  sectionId: number | null,
) {
  return useQuery<AIConversation[]>({
    queryKey: ["conversations", reportUuid, sectionId],
    queryFn: () =>
      apiFetch(
        `/reports/${reportUuid}/sections/${sectionId}/conversations`,
      ),
    enabled: !!reportUuid && !!sectionId,
  });
}

export function useReportRuns(reportUuid: string | null) {
  return useQuery<ReportSectionRun[]>({
    queryKey: ["report-runs", reportUuid],
    queryFn: () => apiFetch(`/reports/${reportUuid}/runs`),
    enabled: !!reportUuid,
    staleTime: 5 * 1000,
  });
}
