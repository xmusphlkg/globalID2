import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, apiFetchWithMeta } from "@/lib/api";
import type { TaskDetail, TaskItem } from "@/lib/hooks/useTasks";

export interface AgentWorkflowRun {
  id: number;
  task_id: number;
  mode: string;
  output_format: string;
  prompt: string;
  status: string;
  risk_level: string;
  country_id: number | null;
  search_scope: string;
  memory_scope: string;
  allowed_actions: string[];
  plan_json: Array<Record<string, unknown>>;
  summary: string | null;
  findings: Array<Record<string, unknown>>;
  citations: Array<Record<string, unknown>>;
  artifacts: Array<Record<string, unknown>>;
  open_questions: string[];
  actions_taken: Array<Record<string, unknown>>;
  result_json: Record<string, unknown>;
  budget_tokens_total: number | null;
  budget_tokens_used: number;
  replan_count: number;
  search_round_count: number;
  review_round_count: number;
  step_count: number;
  error_message: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface AgentWorkflowStep {
  id: number;
  step_uuid: string;
  run_id: number;
  step_key: string;
  step_order: number;
  step_type: string;
  step_name: string;
  status: string;
  attempt: number;
  input_summary: string | null;
  output_summary: string | null;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  prompt: string | null;
  system_prompt: string | null;
  response: string | null;
  model: string | null;
  provider: string | null;
  tokens: Record<string, unknown>;
  duration: number | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface AgentWorkflowEvidence {
  id: number;
  evidence_uuid: string;
  run_id: number;
  step_id: number | null;
  evidence_type: string;
  source_type: string;
  source_name: string | null;
  title: string | null;
  url: string | null;
  resolved_url: string | null;
  content_snippet: string | null;
  content_hash: string;
  confidence: number;
  weight: number;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AgentWorkflowConversation {
  id: number;
  conversation_uuid: string;
  run_id: number;
  step_id: number | null;
  agent_role: string;
  phase: string;
  timestamp: string | null;
  prompt: string | null;
  system_prompt: string | null;
  response: string | null;
  model: string | null;
  provider: string | null;
  tokens: Record<string, unknown>;
  duration: number | null;
  temperature: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AgentWorkflowMemory {
  id: number;
  memory_uuid: string;
  run_id: number | null;
  task_id: number | null;
  scope: string;
  memory_type: string;
  content: string | null;
  summary: string | null;
  source_type: string | null;
  source_ref: string | null;
  content_hash: string;
  embedding: number[];
  collection_name: string | null;
  qdrant_point_id: string | null;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AgentRunSummary {
  task: TaskItem;
  country_code: string | null;
  country_name: string | null;
  run: AgentWorkflowRun;
}

export interface AgentRunListResponse {
  total: number;
  limit: number;
  offset: number;
  items: AgentRunSummary[];
}

export interface AgentRunDetailResponse {
  task: TaskDetail;
  run: AgentWorkflowRun;
  steps: AgentWorkflowStep[];
  evidence: AgentWorkflowEvidence[];
  conversations: AgentWorkflowConversation[];
  memories: AgentWorkflowMemory[];
}

export interface AgentRunActionResponse {
  task_uuid: string;
  task_status: string;
  run_status: string | null;
  cancel_requested: boolean;
  message: string | null;
  detail: AgentRunDetailResponse | null;
}

function buildQuery(params: Record<string, string | number | null | undefined>): string {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") return;
    searchParams.set(key, String(value));
  });
  return searchParams.toString();
}

export function useAgentRuns(
  countryCode: string | null,
  status?: string,
  search?: string,
  limit = 30,
  offset = 0,
) {
  return useQuery<AgentRunListResponse>({
    queryKey: ["agent-runs", countryCode, status, search, limit, offset],
    queryFn: async () => {
      const query = buildQuery({
        country_code: countryCode,
        status,
        search,
        page: Math.floor(offset / limit) + 1,
        page_size: limit,
      });
      const { data, meta } = await apiFetchWithMeta<AgentRunSummary[]>(`/ai/runs${query ? `?${query}` : ""}`);
      return {
        items: data,
        total: meta.pagination?.total ?? data.length,
        limit: meta.pagination?.page_size ?? limit,
        offset: ((meta.pagination?.page ?? 1) - 1) * (meta.pagination?.page_size ?? limit),
      };
    },
    staleTime: 10 * 1000,
    refetchInterval: 5 * 1000,
  });
}

export function useAgentRunDetail(taskUuid: string | null) {
  return useQuery<AgentRunDetailResponse>({
    queryKey: ["agent-run", taskUuid],
    queryFn: () => apiFetch(`/ai/runs/${taskUuid}`),
    enabled: !!taskUuid,
    staleTime: 5 * 1000,
    refetchInterval: taskUuid ? 5 * 1000 : false,
  });
}

export function useResumeAgentRun() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (taskUuid: string) =>
      apiFetch<AgentRunActionResponse>(`/ai/runs/${taskUuid}/resume`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
      queryClient.invalidateQueries({ queryKey: ["agent-run"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
    },
  });
}

export function useCancelAgentRun() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (taskUuid: string) =>
      apiFetch<AgentRunActionResponse>(`/ai/runs/${taskUuid}/cancel`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
      queryClient.invalidateQueries({ queryKey: ["agent-run"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
    },
  });
}
