import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, apiFetchWithMeta } from "@/lib/api";

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

export interface NotificationProgress {
  total: number;
  queued: number;
  sent: number;
  failed: number;
  skipped: number;
  completed: number;
  percent: number;
}

export interface NotificationContent {
  subject: string;
  markdown: string;
}

export interface NotificationDelivery {
  id: string;
  status: string;
  provider?: string;
  attempts: number;
  last_error?: string | null;
  queued_at: string;
  sent_at?: string | null;
  delivered_at?: string | null;
  failed_at?: string | null;
  email_masked: string;
  locale: string;
  list_code: string;
}

export interface NotificationCampaign {
  id: string;
  subject: string;
  status: string;
  created_at: string;
  scheduled_at?: string | null;
  sent_at?: string | null;
  source_locale?: string | null;
  default_locale: string;
  target_locales: string[];
  list_codes: string[];
  audience_count: number;
  progress: NotificationProgress;
  contents?: Record<string, NotificationContent>;
  deliveries?: NotificationDelivery[];
  metadata?: {
    template_version?: string;
    created_by?: string;
    ai?: {
      status?: string;
      model_route?: {
        model_name?: string;
        provider_key?: string;
        provider_name?: string;
      };
      locales?: string[];
    } | null;
  };
}

export interface NotificationCampaignsResponse {
  campaigns: NotificationCampaign[];
  pagination: {
    total: number;
    limit: number;
    offset: number;
  };
}

export interface NotificationCampaignResponse {
  campaign: NotificationCampaign;
}

export interface CreateNotificationPayload {
  subject?: string;
  markdown: string;
  source_locale: string;
  target_locales: string[];
  list_codes?: string[];
  start_sending?: boolean;
  batch_size?: number;
  max_recipients?: number;
}

function recordsParams(filters: SubscriptionRecordFilters) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.listCode) params.set("list_code", filters.listCode);
  if (filters.q) params.set("q", filters.q);
  const pageSize = filters.limit ?? 50;
  params.set("page_size", String(pageSize));
  params.set("page", String(Math.floor((filters.offset ?? 0) / pageSize) + 1));
  return params.toString();
}

function paginationParams(limit = 25, offset = 0) {
  const params = new URLSearchParams();
  params.set("page_size", String(limit));
  params.set("page", String(Math.floor(offset / limit) + 1));
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
    queryFn: async () => {
      const params = recordsParams(filters);
      const response = await apiFetchWithMeta<SubscriptionRecord[]>(`/subscriptions/records${params ? `?${params}` : ""}`);
      return {
        subscriptions: response.data,
        pagination: {
          total: response.meta.pagination?.total ?? response.data.length,
          limit: response.meta.pagination?.page_size ?? filters.limit ?? 50,
          offset: ((response.meta.pagination?.page ?? 1) - 1) * (response.meta.pagination?.page_size ?? filters.limit ?? 50),
        },
      };
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

export function useNotificationCampaigns(limit = 25, offset = 0) {
  return useQuery<NotificationCampaignsResponse>({
    queryKey: ["subscriptions", "notifications", { limit, offset }],
    queryFn: async () => {
      const response = await apiFetchWithMeta<NotificationCampaign[]>(`/notification-campaigns?${paginationParams(limit, offset)}`);
      return {
        campaigns: response.data,
        pagination: {
          total: response.meta.pagination?.total ?? response.data.length,
          limit: response.meta.pagination?.page_size ?? limit,
          offset: ((response.meta.pagination?.page ?? 1) - 1) * (response.meta.pagination?.page_size ?? limit),
        },
      };
    },
    refetchInterval: (query) => {
      const active = query.state.data?.campaigns?.some(
        (campaign) => campaign.status === "queued" || campaign.status === "sending",
      );
      return active ? 3000 : false;
    },
    staleTime: 10 * 1000,
  });
}

export function useNotificationCampaignDetail(campaignId?: string | null, deliveryLimit = 100) {
  return useQuery<NotificationCampaignResponse>({
    queryKey: ["subscriptions", "notifications", campaignId, { deliveryLimit }],
    queryFn: () => apiFetch(`/notification-campaigns/${campaignId}?delivery_limit=${deliveryLimit}`),
    enabled: Boolean(campaignId),
    refetchInterval: (query) => {
      const status = query.state.data?.campaign?.status;
      return status === "queued" || status === "sending" ? 3000 : false;
    },
    staleTime: 5000,
  });
}

export function useCreateNotificationCampaign() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CreateNotificationPayload) =>
      apiFetch<NotificationCampaignResponse & { send_started?: boolean }>("/notification-campaigns", {
        method: "POST",
        body: JSON.stringify(payload),
        timeoutMs: 180000,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["subscriptions", "notifications"] });
      queryClient.invalidateQueries({ queryKey: ["subscriptions", "stats"] });
    },
  });
}

export function useStartNotificationSend() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ campaignId, batchSize = 20 }: { campaignId: string; batchSize?: number }) =>
      apiFetch(`/notification-campaigns/${campaignId}/send?batch_size=${batchSize}`, {
        method: "POST",
        timeoutMs: 30000,
      }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["subscriptions", "notifications"] });
      queryClient.invalidateQueries({ queryKey: ["subscriptions", "notifications", variables.campaignId] });
    },
  });
}
