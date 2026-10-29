import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, apiFetchWithMeta } from "@/lib/api";

export interface AIProviderItem {
  id: number;
  provider_key: string;
  provider_name: string;
  display_name: string;
  api_style: string;
  base_url: string | null;
  organization: string | null;
  is_active: boolean;
  priority: number;
  has_api_key: boolean;
  api_key_hint: string | null;
  extra_headers: Record<string, unknown>;
  extra_config: Record<string, unknown>;
  last_check_status: string;
  last_check_message: string | null;
  last_checked_at: string | null;
  rate_limit_active: boolean;
  rate_limit_cooldown_until: string | null;
  rate_limit_remaining_seconds: number;
  rate_limit_count: number;
  last_rate_limit_at: string | null;
  runtime_failure_active: boolean;
  runtime_failure_cooldown_until: string | null;
  runtime_failure_remaining_seconds: number;
  runtime_failure_streak: number;
  runtime_timeout_count: number;
  runtime_latency_ewma_ms: number | null;
  runtime_last_error: string | null;
  runtime_provider_capacity: number;
  runtime_provider_inflight: number;
  runtime_model_capacity: number;
  runtime_model_inflight: number;
}

export type ThinkingMode = "default" | "enabled" | "disabled";

export interface AIModelItem {
  id: number;
  provider_id: number;
  provider_key: string;
  provider_name: string;
  model_key: string;
  model_name: string;
  display_name: string;
  model_type: string;
  api_style: string | null;
  temperature: number | null;
  max_tokens: number | null;
  runtime_max_concurrency: number | null;
  thinking_mode: ThinkingMode;
  extra_params: Record<string, unknown>;
  is_enabled: boolean;
  is_default: boolean;
  priority: number;
  last_check_status: string;
  last_check_message: string | null;
  last_checked_at: string | null;
  rate_limit_active: boolean;
  rate_limit_scope: string | null;
  rate_limit_cooldown_until: string | null;
  rate_limit_remaining_seconds: number;
  rate_limit_count: number;
  last_rate_limit_at: string | null;
  runtime_failure_active: boolean;
  runtime_failure_scope: string | null;
  runtime_failure_cooldown_until: string | null;
  runtime_failure_remaining_seconds: number;
  runtime_failure_streak: number;
  runtime_timeout_count: number;
  runtime_latency_ewma_ms: number | null;
  runtime_last_error: string | null;
}

export interface AIRuntimeRoute {
  model_id: number;
  model_key: string;
  model_name: string;
  provider_id: number;
  provider_key: string;
  provider_name: string;
  api_style: string;
  base_url: string | null;
  has_api_key: boolean;
  api_key_hint: string | null;
  priority: number | null;
  available_for_routing: boolean;
  routing_status: string;
  routing_status_reason: string | null;
  last_check_status: string | null;
  last_checked_at: string | null;
  rate_limit_active: boolean;
  rate_limit_scope: string | null;
  rate_limit_cooldown_until: string | null;
  rate_limit_remaining_seconds: number;
  rate_limit_count: number;
  last_rate_limit_at: string | null;
  runtime_failure_active: boolean;
  runtime_failure_scope: string | null;
  runtime_failure_cooldown_until: string | null;
  runtime_failure_remaining_seconds: number;
  runtime_failure_kind: string | null;
  runtime_failure_streak: number;
  runtime_failure_streak_raw: number;
  runtime_failure_count: number;
  runtime_timeout_count: number;
  runtime_success_count: number;
  runtime_degraded: boolean;
  runtime_degraded_scope: string | null;
  runtime_degraded_reason: string | null;
  runtime_latency_ewma_ms: number | null;
  runtime_last_failure_at: string | null;
  runtime_last_success_at: string | null;
  runtime_last_error: string | null;
  runtime_provider_capacity: number;
  runtime_provider_inflight: number;
  runtime_provider_min_capacity: number;
  runtime_provider_max_capacity: number;
  runtime_provider_auto_concurrency: boolean;
  runtime_provider_success_streak: number;
  runtime_provider_backoff_remaining_seconds: number;
  runtime_model_capacity: number;
  runtime_model_inflight: number;
}

export interface AIModelCallLogItem {
  id: number;
  request_id: string;
  model_key: string;
  model_name: string;
  provider_key: string;
  agent_name: string;
  status: "success" | "failed";
  latency_ms: number;
  error_type: string | null;
  error_message: string | null;
  created_at: string;
}

export interface ProviderPayload {
  provider_key: string;
  provider_name: string;
  display_name: string;
  api_style: string;
  base_url?: string | null;
  api_key?: string | null;
  organization?: string | null;
  extra_headers?: Record<string, unknown>;
  extra_config?: Record<string, unknown>;
  is_active?: boolean;
  priority?: number;
}

export interface ProviderUpdatePayload {
  display_name?: string;
  api_style?: string;
  base_url?: string | null;
  api_key?: string | null;
  clear_api_key?: boolean;
  organization?: string | null;
  extra_headers?: Record<string, unknown>;
  extra_config?: Record<string, unknown>;
  is_active?: boolean;
  priority?: number;
}

export interface ModelPayload {
  provider_key: string;
  model_name: string;
  display_name?: string;
  model_key?: string;
  model_type?: string;
  api_style?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  runtime_max_concurrency?: number | null;
  thinking_mode?: ThinkingMode;
  extra_params?: Record<string, unknown>;
  is_enabled?: boolean;
  is_default?: boolean;
  priority?: number;
}

export interface ModelUpdatePayload {
  display_name?: string;
  model_type?: string;
  api_style?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  runtime_max_concurrency?: number | null;
  thinking_mode?: ThinkingMode;
  extra_params?: Record<string, unknown>;
  is_enabled?: boolean;
  is_default?: boolean;
  priority?: number;
}

export interface ProviderBootstrapPayload {
  force?: boolean;
}

export interface ProviderModelDiscoveryResult {
  success: boolean;
  status: string;
  message: string;
  models: string[];
  base_url?: string;
}

function invalidateAIModelQueries(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ["ai-providers"] });
  queryClient.invalidateQueries({ queryKey: ["ai-models"] });
  queryClient.invalidateQueries({ queryKey: ["ai-runtime-routes"] });
}

export function useAIProviders() {
  return useQuery<AIProviderItem[]>({
    queryKey: ["ai-providers"],
    queryFn: () => apiFetch("/ai/models/providers"),
    staleTime: 10 * 1000,
  });
}

export function useAIModels() {
  return useQuery<AIModelItem[]>({
    queryKey: ["ai-models"],
    queryFn: () => apiFetch("/ai/models"),
    staleTime: 10 * 1000,
  });
}

export function useAIRuntimeRoutes() {
  return useQuery<AIRuntimeRoute[]>({
    queryKey: ["ai-runtime-routes"],
    queryFn: () => apiFetch("/ai/models/runtime"),
    staleTime: 10 * 1000,
  });
}

export interface AIModelCallLogFilters {
  page: number;
  providerKey: string;
  status: string;
}

export interface AIModelCallLogPage {
  items: AIModelCallLogItem[];
  total: number;
  totalPages: number;
}

const CALL_LOG_PAGE_SIZE = 50;

export function useAIModelCallLogs(
  enabled = true,
  filters: AIModelCallLogFilters = { page: 1, providerKey: "", status: "" },
) {
  return useQuery<AIModelCallLogPage>({
    queryKey: ["ai-model-call-logs", filters],
    queryFn: async () => {
      const params = new URLSearchParams({
        page: String(filters.page),
        page_size: String(CALL_LOG_PAGE_SIZE),
      });
      if (filters.providerKey) params.set("provider_key", filters.providerKey);
      if (filters.status) params.set("status", filters.status);
      const { data, meta } = await apiFetchWithMeta<AIModelCallLogItem[]>(`/ai/models/call-logs?${params}`);
      return {
        items: data,
        total: meta.pagination?.total ?? data.length,
        totalPages: meta.pagination?.total_pages ?? 1,
      };
    },
    enabled,
    staleTime: 5 * 1000,
    refetchInterval: enabled ? 15 * 1000 : false,
  });
}

export function useCreateAIProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ProviderPayload) =>
      apiFetch("/ai/models/providers", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useUpdateAIProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ providerKey, payload }: { providerKey: string; payload: ProviderUpdatePayload }) =>
      apiFetch(`/ai/models/providers/${encodeURIComponent(providerKey)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
        // Saving is local, but a busy API/DB during a provider refresh can
        // exceed the generic 10s UI timeout. Testing/discovery have their own
        // longer timeouts below; keep the save request bounded but forgiving.
        timeoutMs: 30_000,
      }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useDeleteAIProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (providerKey: string) => apiFetch(`/ai/models/providers/${encodeURIComponent(providerKey)}`, { method: "DELETE" }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useTestAIProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (providerKey: string) => apiFetch(`/ai/models/providers/${encodeURIComponent(providerKey)}/test`, { method: "POST", timeoutMs: 120_000 }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useDiscoverAIProviderModels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (providerKey: string) =>
      apiFetch<ProviderModelDiscoveryResult>(`/ai/models/providers/${encodeURIComponent(providerKey)}/discover-models`, {
        method: "POST",
        timeoutMs: 20_000,
      }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useRebuildAIProviders() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ProviderBootstrapPayload = {}) =>
      apiFetch("/ai/models/providers/bootstrap", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useCreateAIModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ModelPayload) =>
      apiFetch<AIModelItem>("/ai/models", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useUpdateAIModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ modelKey, payload }: { modelKey: string; payload: ModelUpdatePayload }) =>
      apiFetch(`/ai/models/${encodeURIComponent(modelKey)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useTestAIModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (modelKey: string) => apiFetch(`/ai/models/${encodeURIComponent(modelKey)}/test`, { method: "POST", timeoutMs: 120_000 }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useDeleteAIModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (modelKey: string) => apiFetch(`/ai/models/${encodeURIComponent(modelKey)}`, { method: "DELETE" }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useCheckAllAIModels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch("/ai/models/check-all", { method: "POST", timeoutMs: 300_000 }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}
