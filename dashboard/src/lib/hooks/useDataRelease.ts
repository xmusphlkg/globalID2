import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export interface DataReleaseJob {
  job_id: string;
  name: string;
  enabled: boolean;
  priority: string;
  auto_after_crawls: boolean;
  include_git_push: boolean;
  include_cloudflare_deploy: boolean;
  require_clean_worktree: boolean;
  interval_minutes?: number | null;
  daily_time?: string | null;
  timezone?: string | null;
  github_remote: string;
  github_branch?: string | null;
  cloudflare_project_name?: string | null;
  commit_message_template: string;
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

export interface DataReleaseJobInput {
  job_id: string;
  name: string;
  enabled: boolean;
  priority: string;
  auto_after_crawls: boolean;
  include_git_push: boolean;
  include_cloudflare_deploy: boolean;
  require_clean_worktree: boolean;
  interval_minutes?: number | null;
  daily_time?: string | null;
  timezone?: string | null;
  github_remote: string;
  github_branch?: string | null;
  cloudflare_project_name?: string | null;
  commit_message_template: string;
  notes?: string | null;
}

export interface DataReleaseConfig {
  enabled: boolean;
  timezone: string;
  poll_interval_seconds: number;
  auto_failure_cooldown_minutes: number;
  last_tick_at?: string | null;
  jobs: DataReleaseJob[];
}

export interface DataReleaseTriggerResult {
  job_id: string;
  status: string;
  task_uuid?: string | null;
  reason?: string | null;
}

export interface DataReleaseChecks {
  checked_at: string;
  overall_ready: boolean;
  blockers: string[];
  git: {
    env_var: string;
    repo_url?: string | null;
    branch: string;
    raw_base_url?: string | null;
    read_access_ok: boolean;
    write_access_ok: boolean;
    read_check_output?: string | null;
    write_check_output?: string | null;
    require_clean_worktree: boolean;
    dirty_blocking_paths: string[];
  };
  cloudflare: {
    project_name?: string | null;
    token_present: boolean;
    account_id_present: boolean;
    project_access_ok: boolean;
    subdomain?: string | null;
    domains: string[];
    production_branch?: string | null;
    latest_production_deployment?: {
      id?: string | null;
      url?: string | null;
      environment?: string | null;
      created_on?: string | null;
      status?: string | null;
      branch?: string | null;
      commit_hash?: string | null;
      commit_message?: string | null;
      commit_dirty: boolean;
    } | null;
    error?: string | null;
  };
  commands: {
    python_path: string;
    python_exists: boolean;
    wrangler_available: boolean;
    wrangler_version?: string | null;
  };
  repository_boundary: {
    generated_paths: string[];
    tracked_paths: string[];
    enforced: boolean;
  };
  raw?: Record<string, unknown> | null;
}

export function useDataReleaseConfig() {
  return useQuery<DataReleaseConfig>({
    queryKey: ["data-release"],
    queryFn: () => apiFetch("/release"),
    staleTime: 10 * 1000,
  });
}

export function useDataReleaseJobs() {
  return useQuery<DataReleaseJob[]>({
    queryKey: ["data-release-jobs"],
    queryFn: () => apiFetch("/release/jobs"),
    staleTime: 10 * 1000,
  });
}

export function useDataReleaseChecks(jobId: string | null) {
  return useQuery<DataReleaseChecks>({
    queryKey: ["data-release-checks", jobId],
    queryFn: () =>
      apiFetch(`/release/jobs/${jobId}/checks`, {
        timeoutMs: 60_000,
      }),
    enabled: !!jobId,
    staleTime: 10 * 1000,
  });
}

export function useRunDataReleaseJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      apiFetch<DataReleaseTriggerResult>(`/release/jobs/${jobId}/run`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["data-release"] });
      queryClient.invalidateQueries({ queryKey: ["data-release-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
    },
  });
}

export function useCreateDataReleaseJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: DataReleaseJobInput) =>
      apiFetch<DataReleaseJob>("/release/jobs", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["data-release"] });
      queryClient.invalidateQueries({ queryKey: ["data-release-jobs"] });
    },
  });
}

export function useUpdateDataReleaseJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, payload }: { jobId: string; payload: Partial<DataReleaseJobInput> }) =>
      apiFetch<DataReleaseJob>(`/release/jobs/${jobId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["data-release"] });
      queryClient.invalidateQueries({ queryKey: ["data-release-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["data-release-checks"] });
    },
  });
}

export function useDeleteDataReleaseJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      apiFetch(`/release/jobs/${jobId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["data-release"] });
      queryClient.invalidateQueries({ queryKey: ["data-release-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["data-release-checks"] });
    },
  });
}
