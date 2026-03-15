import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

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
}

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
  last_check_status: string | null;
  rate_limit_active: boolean;
  rate_limit_scope: string | null;
  rate_limit_cooldown_until: string | null;
  rate_limit_remaining_seconds: number;
  rate_limit_count: number;
  last_rate_limit_at: string | null;
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
  provider_id: number;
  model_name: string;
  display_name?: string;
  model_key?: string;
  model_type?: string;
  api_style?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
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
  extra_params?: Record<string, unknown>;
  is_enabled?: boolean;
  is_default?: boolean;
  priority?: number;
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
    mutationFn: ({ providerId, payload }: { providerId: number; payload: ProviderUpdatePayload }) =>
      apiFetch(`/ai/models/providers/${providerId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useTestAIProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (providerId: number) => apiFetch(`/ai/models/providers/${providerId}/test`, { method: "POST" }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useCreateAIModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ModelPayload) =>
      apiFetch("/ai/models", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useUpdateAIModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, payload }: { modelId: number; payload: ModelUpdatePayload }) =>
      apiFetch(`/ai/models/${modelId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useTestAIModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (modelId: number) => apiFetch(`/ai/models/${modelId}/test`, { method: "POST" }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useDeleteAIModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (modelId: number) => apiFetch(`/ai/models/${modelId}`, { method: "DELETE" }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}

export function useCheckAllAIModels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch("/ai/models/check-all", { method: "POST" }),
    onSuccess: () => invalidateAIModelQueries(queryClient),
  });
}
