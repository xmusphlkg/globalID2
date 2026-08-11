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
  repository_boundary?: {
    generated_paths: string[];
    tracked_paths: string[];
    enforced: boolean;
  };
  raw?: Record<string, unknown> | null;
}

type DataReleaseChecksResponse = Partial<
  Omit<DataReleaseChecks, "git" | "cloudflare" | "commands" | "repository_boundary">
> & {
  git?: Partial<DataReleaseChecks["git"]> & { dirty_release_paths?: unknown };
  cloudflare?: Partial<DataReleaseChecks["cloudflare"]>;
  commands?: Partial<DataReleaseChecks["commands"]>;
  repository_boundary?: Partial<NonNullable<DataReleaseChecks["repository_boundary"]>>;
  data_refresh_snapshot?: unknown;
};

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/**
 * Normalize release-check responses at the API boundary. During a rolling
 * upgrade the browser can temporarily talk to either the old or new API, so
 * rendering code must receive stable nested objects and arrays.
 */
export function normalizeDataReleaseChecks(payload: DataReleaseChecksResponse): DataReleaseChecks {
  const git = payload.git ?? {};
  const cloudflare = payload.cloudflare ?? {};
  const commands = payload.commands ?? {};
  const boundary = payload.repository_boundary;
  const repositoryBoundary = typeof boundary?.enforced === "boolean"
    ? {
        generated_paths: stringArray(boundary.generated_paths),
        tracked_paths: stringArray(boundary.tracked_paths),
        enforced: boundary.enforced,
      }
    : undefined;

  return {
    checked_at: typeof payload.checked_at === "string" ? payload.checked_at : "",
    overall_ready: payload.overall_ready === true,
    blockers: stringArray(payload.blockers),
    git: {
      env_var: typeof git.env_var === "string" ? git.env_var : "",
      repo_url: git.repo_url,
      branch: typeof git.branch === "string" ? git.branch : "",
      raw_base_url: git.raw_base_url,
      read_access_ok: git.read_access_ok === true,
      write_access_ok: git.write_access_ok === true,
      read_check_output: git.read_check_output,
      write_check_output: git.write_check_output,
      require_clean_worktree: git.require_clean_worktree === true,
      dirty_blocking_paths: stringArray(git.dirty_blocking_paths),
    },
    cloudflare: {
      project_name: cloudflare.project_name,
      token_present: cloudflare.token_present === true,
      account_id_present: cloudflare.account_id_present === true,
      project_access_ok: cloudflare.project_access_ok === true,
      subdomain: cloudflare.subdomain,
      domains: stringArray(cloudflare.domains),
      production_branch: cloudflare.production_branch,
      latest_production_deployment: cloudflare.latest_production_deployment,
      error: cloudflare.error,
    },
    commands: {
      python_path: typeof commands.python_path === "string" ? commands.python_path : "",
      python_exists: commands.python_exists === true,
      wrangler_available: commands.wrangler_available === true,
      wrangler_version: commands.wrangler_version,
    },
    repository_boundary: repositoryBoundary,
    raw: payload.raw,
  };
}

export function useDataReleaseConfig() {
  return useQuery<DataReleaseConfig>({
    queryKey: ["data-release"],
    queryFn: () => apiFetch("/releases/config"),
    staleTime: 10 * 1000,
  });
}

export function useDataReleaseJobs() {
  return useQuery<DataReleaseJob[]>({
    queryKey: ["data-release-jobs"],
    queryFn: () => apiFetch("/releases/jobs"),
    staleTime: 10 * 1000,
  });
}

export function useDataReleaseChecks(jobId: string | null) {
  return useQuery<DataReleaseChecks>({
    queryKey: ["data-release-checks", jobId],
    queryFn: async () => {
      const payload = await apiFetch<DataReleaseChecksResponse>(`/releases/${jobId}/checks`, {
        timeoutMs: 60_000,
      });
      return normalizeDataReleaseChecks(payload);
    },
    enabled: !!jobId,
    staleTime: 10 * 1000,
  });
}

export function useRunDataReleaseJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      apiFetch<DataReleaseTriggerResult>(`/releases/${jobId}/runs`, {
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
      apiFetch<DataReleaseJob>("/releases/jobs", {
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
      apiFetch<DataReleaseJob>(`/releases/jobs/${jobId}`, {
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
      apiFetch(`/releases/jobs/${jobId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["data-release"] });
      queryClient.invalidateQueries({ queryKey: ["data-release-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["data-release-checks"] });
    },
  });
}
