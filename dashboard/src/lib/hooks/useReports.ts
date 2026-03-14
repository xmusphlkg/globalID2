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
