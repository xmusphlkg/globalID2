import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface SubscriptionListOption {
  code: string;
  name_en: string;
  name_zh: string;
  description_en?: string;
  description_zh?: string;
  default_frequency: string;
}

export interface SubscriptionFilterOption {
  value: string;
  label_en: string;
  label_zh: string;
  description_en?: string;
  description_zh?: string;
}

export interface SubscriptionOptions {
  ok?: boolean;
  lists: SubscriptionListOption[];
  locales: SubscriptionFilterOption[];
  frequencies: SubscriptionFilterOption[];
  filters: {
    country: SubscriptionFilterOption[];
    disease: SubscriptionFilterOption[];
  };
}

export interface SubscriptionConfig {
  configured: boolean;
  worker_base_url?: string | null;
  d1_database_name?: string | null;
  sync_options_on_release?: string | null;
}

export interface SubscriptionStats {
  ok?: boolean;
  generated_at?: string;
  subscriptions?: Record<string, number>;
  contacts?: Record<string, number>;
  deliveries_last_7_days?: Record<string, number>;
  stale_pending_subscriptions?: number;
  pending_expiry_days?: number;
}

export interface SubscriptionRecord {
  subscription_id: string;
  email: string;
  source?: string | null;
  status: string;
  contact_status: string;
  list_code: string;
  list_name: string;
  list_name_zh: string;
  frequency: string;
  locale: string;
  timezone?: string | null;
  filters: Record<string, string[]>;
  created_at: string;
}

export interface SubscriptionRecordsResponse {
  subscriptions: SubscriptionRecord[];
  pagination: {
    total: number;
    limit: number;
    offset: number;
  };
}

export interface SubscriptionRecordFilters {
  status?: string;
  listCode?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

function recordsParams(filters: SubscriptionRecordFilters) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.listCode) params.set("list_code", filters.listCode);
  if (filters.q) params.set("q", filters.q);
  if (filters.limit) params.set("limit", String(filters.limit));
  if (filters.offset) params.set("offset", String(filters.offset));
  return params.toString();
}

export function useSubscriptionConfig() {
  return useQuery<SubscriptionConfig>({
    queryKey: ["subscriptions", "config"],
    queryFn: () => apiFetch("/subscriptions/config"),
    staleTime: 60 * 1000,
  });
}

export function useSubscriptionStats() {
  return useQuery<SubscriptionStats>({
    queryKey: ["subscriptions", "stats"],
    queryFn: () => apiFetch("/subscriptions/stats"),
    staleTime: 30 * 1000,
  });
}

export function useSubscriptionOptions() {
  return useQuery<SubscriptionOptions>({
    queryKey: ["subscriptions", "options"],
    queryFn: () => apiFetch("/subscriptions/options"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useSubscriptionRecords(filters: SubscriptionRecordFilters = {}) {
  return useQuery<SubscriptionRecordsResponse>({
    queryKey: ["subscriptions", "records", filters],
    queryFn: () => {
      const params = recordsParams(filters);
      return apiFetch(`/subscriptions/records${params ? `?${params}` : ""}`);
    },
    staleTime: 30 * 1000,
  });
}

export function useRunSubscriptionMaintenance() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => apiFetch("/subscriptions/maintenance", { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["subscriptions", "stats"] });
      queryClient.invalidateQueries({ queryKey: ["subscriptions", "records"] });
    },
  });
}

export function useSyncSubscriptionOptions() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => apiFetch("/subscriptions/sync-options", { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["subscriptions", "options"] });
      queryClient.invalidateQueries({ queryKey: ["subscriptions", "stats"] });
    },
  });
}
