import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface StageInfo {
  stage: string;
  task_type: string;
  status: string | null;
  task_uuid: string | null;
  task_name: string | null;
  progress: number;
  last_run: string | null;
}

export interface DataSourceFlow {
  data_source: string;
  country_id?: number | null;
  country_code?: string | null;
  country_name?: string | null;
  record_count: number;
  source_series_count: number;
  source_observation_count: number;
  series_availability: Record<string, number>;
  source_availability: Record<string, number>;
  observation_quality: Record<string, number>;
  metric_types: Record<string, number>;
  mapping_relations: Record<string, number>;
  comparability: Record<string, number>;
  earliest_date?: string | null;
  latest_date: string | null;
  history_start_year?: number | null;
  source_scope?: string | null;
  latest_task_uuid?: string | null;
  latest_task_source?: string | null;
  latest_task_status?: string | null;
  latest_task_time?: string | null;
  stages: StageInfo[];
}

export interface SourceOption {
  value: string;
  label: string;
  label_en: string;
  label_zh: string;
  source_kind: "current" | "history";
  supports_start_year: boolean;
}

export interface SourcePolicyMetadata {
  supports_current_month: boolean;
  default_include_current_month: boolean;
  dynamic_revision_enabled: boolean;
  default_revision_window_months: number;
  current_month_status: string;
  public_release_enabled: boolean;
  public_release_editable: boolean;
  publication_day?: number | null;
  source_update_cadence?: string | null;
}

export interface OntologySeries {
  id: string;
  source_id: string;
  concept_id?: string | null;
  local_codes: string[];
  local_labels: string[];
  frequency: string;
  measure: string;
  reporting_basis: string;
  unit: string;
  mapping_relation: string;
  comparability: string;
  aggregation_policy: string;
  status: string;
  target?: {
    id?: string | null;
    labels?: { en?: string | null; zh?: string | null };
  } | null;
  availability?: Array<{ status?: string | null }>;
}

export interface CountrySourceConfig {
  country_code: string;
  country_name: string;
  country_name_en: string;
  country_name_zh: string;
  language: string;
  timezone: string;
  supports_crawl: boolean;
  supports_fill_missing: boolean;
  default_fill_missing: boolean;
  default_source: string;
  default_start_year?: number | null;
  supports_start_year: boolean;
  supports_source_file: boolean;
  supports_source_dir: boolean;
  source_options: SourceOption[];
  source_policy?: SourcePolicyMetadata | null;
}

export interface AutomationJob {
  job_id: string;
  name: string;
  country_code: string;
  source: string;
  enabled: boolean;
  priority: string;
  process: boolean;
  save_raw: boolean;
  fill_missing: boolean;
  force: boolean;
  include_current_month: boolean;
  revision_window_months: number;
  retry_threshold: number;
  interval_minutes?: number | null;
  daily_time?: string | null;
  timezone?: string | null;
  notes?: string | null;
  next_run_at?: string | null;
  last_started_at?: string | null;
  last_finished_at?: string | null;
  last_status: string;
  last_error?: string | null;
  last_task_uuid?: string | null;
  run_count: number;
  skipped_count: number;
}

export interface AutomationJobInput {
  job_id: string;
  name: string;
  country_code: string;
  source: string;
  enabled: boolean;
  priority: string;
  process: boolean;
  save_raw: boolean;
  fill_missing: boolean;
  force: boolean;
  include_current_month: boolean;
  revision_window_months: number;
  retry_threshold: number;
  interval_minutes?: number | null;
  daily_time?: string | null;
  timezone?: string | null;
  notes?: string | null;
}

export interface AutomationConfig {
  enabled: boolean;
  timezone: string;
  poll_interval_seconds: number;
  default_retry_threshold: number;
  admin_emails: string[];
  email_enabled: boolean;
  last_tick_at?: string | null;
  jobs: AutomationJob[];
}

export interface AutomationTriggerResult {
  job_id: string;
  status: string;
  task_uuid?: string | null;
  reason?: string | null;
}

export function useSourcesFlow(countryId: number | null) {
  return useQuery<DataSourceFlow[]>({
    queryKey: ["sources-flow", countryId],
    queryFn: () =>
      apiFetch(countryId ? `/sources/flow?country_id=${countryId}` : "/sources/flow"),
    staleTime: 15 * 1000,
  });
}

export function useSourceConfigs(lang: "en" | "zh" = "en") {
  return useQuery<CountrySourceConfig[]>({
    queryKey: ["sources-config", lang],
    queryFn: () => apiFetch(`/sources/config?lang=${lang}`),
    staleTime: 30 * 60 * 1000,
  });
}

export function useOntologySeries(countryCode?: string | null) {
  const normalizedCode = (countryCode || "").trim().toUpperCase();
  return useQuery<OntologySeries[]>({
    queryKey: ["disease-ontology-series", normalizedCode],
    queryFn: () =>
      apiFetch(`/disease-ontology/series?country_code=${encodeURIComponent(normalizedCode)}`),
    enabled: Boolean(normalizedCode),
    staleTime: 30 * 60 * 1000,
  });
}

export function useAutomationConfig() {
  return useQuery<AutomationConfig>({
    queryKey: ["sources-automation"],
    queryFn: () => apiFetch("/sources/automation"),
    staleTime: 5 * 1000,
    refetchInterval: 5 * 1000,
  });
}

export function useAutomationJobs() {
  return useQuery<AutomationJob[]>({
    queryKey: ["sources-automation-jobs"],
    queryFn: () => apiFetch("/sources/automation/jobs"),
    staleTime: 5 * 1000,
    refetchInterval: 5 * 1000,
  });
}

export interface CreateCrawlTaskPayload {
  task_name: string;
  country_id: number;
  description?: string;
  priority?: string;
  input_data?: Record<string, unknown>;
}

/** Start a crawl that actually executes (not just creates a DB record). */
export interface StartCrawlPayload {
  country_id: number;
  source?: string;
  force?: boolean;
  process?: boolean;
  save_raw?: boolean;
  fill_missing?: boolean;
  include_current_month?: boolean;
  revision_window_months?: number;
  start_year?: number | null;
  source_file?: string | null;
  source_dir?: string | null;
  priority?: string;
}

export function useStartCrawl() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: StartCrawlPayload) =>
      apiFetch("/crawl/start", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources-flow"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

export function useRunAutomationJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      apiFetch<AutomationTriggerResult>(`/sources/automation/jobs/${jobId}/run`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources-automation"] });
      queryClient.invalidateQueries({ queryKey: ["sources-flow"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

export function useCreateAutomationJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: AutomationJobInput) =>
      apiFetch<AutomationJob>("/sources/automation/jobs", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources-automation"] });
      queryClient.invalidateQueries({ queryKey: ["sources-automation-jobs"] });
    },
  });
}

export function useUpdateAutomationJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, payload }: { jobId: string; payload: Partial<AutomationJobInput> }) =>
      apiFetch<AutomationJob>(`/sources/automation/jobs/${jobId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources-automation"] });
      queryClient.invalidateQueries({ queryKey: ["sources-automation-jobs"] });
    },
  });
}

export function useDeleteAutomationJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      apiFetch(`/sources/automation/jobs/${jobId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources-automation"] });
      queryClient.invalidateQueries({ queryKey: ["sources-automation-jobs"] });
    },
  });
}

/** Execute an existing pending/failed task. */
export function useExecuteTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskUuid: string) =>
      apiFetch(`/tasks/${taskUuid}/execute`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources-flow"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

/** Legacy: create a task record only (no execution). */
export function useCreateCrawlTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateCrawlTaskPayload) =>
      apiFetch("/tasks", {
        method: "POST",
        body: JSON.stringify({ task_type: "crawl_data", ...payload }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources-flow"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}
