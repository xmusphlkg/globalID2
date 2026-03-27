"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, Grid, Metric, ProgressBar, Text, Title } from "@tremor/react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Cloud,
  GitBranch,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Wrench,
} from "lucide-react";

import { t } from "@/lib/i18n";
import {
  type DataReleaseJob,
  type DataReleaseJobInput,
  useCreateDataReleaseJob,
  useDataReleaseChecks,
  useDataReleaseConfig,
  useDataReleaseJobs,
  useDeleteDataReleaseJob,
  useRunDataReleaseJob,
  useUpdateDataReleaseJob,
} from "@/lib/hooks/useDataRelease";
import {
  type WorkbookEntry,
  useTaskDetail,
  useTasks,
  useTaskWebSocket,
  useWorkerStatus,
} from "@/lib/hooks/useTasks";
import { useAppStore } from "@/stores/app-store";

const defaultForm: DataReleaseJobInput = {
  job_id: "",
  name: "",
  enabled: true,
  priority: "high",
  auto_after_crawls: true,
  include_git_push: true,
  include_cloudflare_deploy: true,
  require_clean_worktree: true,
  interval_minutes: null,
  daily_time: "",
  timezone: "UTC",
  github_remote: "origin",
  github_branch: "main",
  cloudflare_project_name: "globalid",
  commit_message_template: "chore(data-release): publish site data {timestamp}",
  notes: "",
};

type BooleanReleaseField =
  | "auto_after_crawls"
  | "include_git_push"
  | "include_cloudflare_deploy"
  | "require_clean_worktree";

function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function scheduleLabel(job: { interval_minutes?: number | null; daily_time?: string | null; timezone?: string | null }): string {
  if (job.interval_minutes) return `Every ${job.interval_minutes} minute(s)`;
  if (job.daily_time) return `Daily at ${job.daily_time} (${job.timezone || "UTC"})`;
  return "Manual or crawl-completion trigger";
}

function toForm(job: DataReleaseJob): DataReleaseJobInput {
  return {
    job_id: job.job_id,
    name: job.name,
    enabled: job.enabled,
    priority: job.priority,
    auto_after_crawls: job.auto_after_crawls,
    include_git_push: job.include_git_push,
    include_cloudflare_deploy: job.include_cloudflare_deploy,
    require_clean_worktree: job.require_clean_worktree,
    interval_minutes: job.interval_minutes ?? null,
    daily_time: job.daily_time ?? "",
    timezone: job.timezone ?? "UTC",
    github_remote: job.github_remote,
    github_branch: job.github_branch ?? "",
    cloudflare_project_name: job.cloudflare_project_name ?? "",
    commit_message_template: job.commit_message_template,
    notes: job.notes ?? "",
  };
}

function statusColor(status: string) {
  switch (status) {
    case "completed":
      return "emerald" as const;
    case "running":
    case "queued":
      return "amber" as const;
    case "failed":
      return "rose" as const;
    case "skipped":
    case "cancelled":
      return "slate" as const;
    default:
      return "blue" as const;
  }
}

function workbookEntryColor(entryType: string) {
  switch (entryType) {
    case "success":
      return "emerald" as const;
    case "warning":
      return "amber" as const;
    case "error":
      return "rose" as const;
    case "info":
      return "blue" as const;
    default:
      return "slate" as const;
  }
}

function RuntimeTile({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-tremor-border bg-tremor-background px-4 py-3 shadow-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background">
      <div className="flex items-center gap-2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {icon}
        <span className="text-xs font-semibold uppercase tracking-[0.18em]">{label}</span>
      </div>
      <p className="mt-3 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{value}</p>
    </div>
  );
}

function AccessDetail({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="rounded-2xl border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {label}
      </p>
      <div
        className={`mt-2 text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong ${
          mono ? "break-all font-mono text-xs" : "break-words"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function CheckOutput({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="rounded-2xl border border-dashed border-tremor-border bg-tremor-background-muted/60 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/30">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {label}
      </p>
      <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-xs text-tremor-content dark:text-dark-tremor-content">
        {value}
      </pre>
    </div>
  );
}

function stripAnsiCodes(value: string): string {
  return value.replace(/\u001b\[[0-9;]*m/g, "");
}

function rawWorkbookText(entry: WorkbookEntry): string {
  const sections: string[] = [];
  if (entry.content) sections.push(stripAnsiCodes(entry.content));
  if (entry.prompt) sections.push(`[prompt]\n${entry.prompt}`);
  if (entry.response) sections.push(`[response]\n${entry.response}`);
  if (entry.error_message) sections.push(`[error]\n${entry.error_message}`);
  return sections.join("\n\n").trim();
}

function logPreview(value: string): string {
  const firstLine = value
    .split("\n")
    .map((line) => line.trim())
    .find(Boolean);
  if (!firstLine) return "(empty log entry)";
  if (firstLine.length <= 180) return firstLine;
  return `${firstLine.slice(0, 177)}...`;
}

function RawReleaseTaskDetail({
  taskDetail,
  detailLoading,
}: {
  taskDetail?: {
    task_uuid: string;
    status: string;
    progress: number;
    created_at: string;
    completed_at: string | null;
    workbook_entries: WorkbookEntry[];
  };
  detailLoading: boolean;
}) {
  const rawEntries = useMemo(() => {
    if (!taskDetail) return [];
    return [...taskDetail.workbook_entries].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
  }, [taskDetail]);

  if (detailLoading && !taskDetail) {
    return (
      <div className="space-y-3">
        <div className="h-6 animate-pulse rounded bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
        <div className="h-24 animate-pulse rounded bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
        <div className="h-24 animate-pulse rounded bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
      </div>
    );
  }

  if (!taskDetail) {
    return (
      <div className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        Failed to load raw release logs.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {taskDetail.status === "running" && taskDetail.progress >= 88 && taskDetail.progress < 100 ? (
        <div className="rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
          Cloudflare Pages upload is in progress. `wrangler` can stay at 88% for a while and then jump straight to 100% when deploy finishes.
        </div>
      ) : null}

      <div className="grid gap-3 md:grid-cols-3">
        <AccessDetail label="Status" value={<Badge color={statusColor(taskDetail.status)}>{taskDetail.status}</Badge>} />
        <AccessDetail label="Progress" value={`${taskDetail.progress}%`} />
        <AccessDetail label="Workbook Entries" value={String(rawEntries.length)} />
        <AccessDetail label="Created" value={formatDateTime(taskDetail.created_at)} />
        <AccessDetail label="Completed" value={formatDateTime(taskDetail.completed_at)} />
        <AccessDetail label="Task UUID" value={taskDetail.task_uuid} mono />
      </div>

      <div className="rounded-2xl border border-dashed border-tremor-border bg-tremor-background-muted/60 p-3 text-sm text-tremor-content dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/30 dark:text-dark-tremor-content">
        Raw workbook entries only. No structured parsing is applied in this view.
      </div>

      {!rawEntries.length ? (
        <div className="rounded-tremor-default border border-dashed border-tremor-border p-6 text-center dark:border-dark-tremor-border">
          <Text>No workbook entries recorded yet.</Text>
        </div>
      ) : (
        <div className="space-y-3">
          {rawEntries.map((entry) => {
            const rawText = rawWorkbookText(entry);
            const preview = logPreview(rawText);
            return (
              <details
                key={entry.id}
                className="rounded-2xl border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background"
              >
                <summary className="cursor-pointer list-none">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge color={workbookEntryColor(entry.entry_type)}>{entry.entry_type}</Badge>
                    <Text className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                      {entry.title}
                    </Text>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    <span>{formatDateTime(entry.created_at)}</span>
                    {entry.content_type ? <span>content_type: {entry.content_type}</span> : null}
                    {entry.duration ? <span>duration: {entry.duration.toFixed(1)}s</span> : null}
                  </div>
                  <p className="mt-2 whitespace-pre-wrap break-words font-mono text-xs text-tremor-content dark:text-dark-tremor-content">
                    {preview}
                  </p>
                </summary>
                <pre className="mt-3 whitespace-pre-wrap break-words rounded-xl bg-tremor-background-muted/60 p-3 font-mono text-xs text-tremor-content-strong dark:bg-dark-tremor-background-muted/30 dark:text-dark-tremor-content-strong">
                  {rawText || "(empty log entry)"}
                </pre>
              </details>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function DataReleasePage() {
  const { lang } = useAppStore();
  const { data: config } = useDataReleaseConfig();
  const { data: jobs, isLoading } = useDataReleaseJobs();
  const { data: workerStatus } = useWorkerStatus();
  const { data: releaseTasks } = useTasks(undefined, "export_data", undefined, undefined, 20);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  const [expandedTaskUuid, setExpandedTaskUuid] = useState<string | null>(null);
  const [form, setForm] = useState<DataReleaseJobInput>(defaultForm);

  const {
    data: checks,
    refetch: refetchChecks,
    isFetching: checkingAccess,
    isLoading: loadingChecks,
  } = useDataReleaseChecks(selectedJobId);
  const { data: taskDetail, isFetching: detailFetching, isLoading: detailLoading } = useTaskDetail(expandedTaskUuid);

  const runJob = useRunDataReleaseJob();
  const createJob = useCreateDataReleaseJob();
  const updateJob = useUpdateDataReleaseJob();
  const deleteJob = useDeleteDataReleaseJob();

  useTaskWebSocket({
    extraQueryKeys: [["data-release"], ["data-release-jobs"], ["data-release-checks"], ["tasks"], ["task"]],
  });

  useEffect(() => {
    if (!selectedJobId && jobs?.length) {
      setSelectedJobId(jobs[0].job_id);
    }
  }, [jobs, selectedJobId]);

  useEffect(() => {
    if (!expandedTaskUuid && releaseTasks?.length) {
      setExpandedTaskUuid(releaseTasks[0].task_uuid);
    }
  }, [releaseTasks, expandedTaskUuid]);

  const selectedJob = useMemo(
    () => jobs?.find((job) => job.job_id === selectedJobId) ?? null,
    [jobs, selectedJobId],
  );

  const summary = useMemo(() => {
    const rows = jobs ?? [];
    return {
      total: rows.length,
      enabled: rows.filter((job) => job.enabled).length,
      auto: rows.filter((job) => job.auto_after_crawls).length,
      healthy: checks?.overall_ready ? 1 : 0,
    };
  }, [checks?.overall_ready, jobs]);

  const accessStatusLabel = !checks
    ? (loadingChecks || checkingAccess ? "Checking access" : "Awaiting snapshot")
    : checks.overall_ready
      ? "Ready to release"
      : "Preflight blocked";

  const expandedTaskDetail = taskDetail?.task_uuid === expandedTaskUuid ? taskDetail : undefined;

  const resetForm = () => {
    setEditingJobId(null);
    setForm({
      ...defaultForm,
      timezone: config?.timezone || defaultForm.timezone,
      github_branch: selectedJob?.github_branch || defaultForm.github_branch,
      cloudflare_project_name: selectedJob?.cloudflare_project_name || defaultForm.cloudflare_project_name,
    });
  };

  const startEdit = (job: DataReleaseJob) => {
    setSelectedJobId(job.job_id);
    setEditingJobId(job.job_id);
    setForm(toForm(job));
  };

  const submitForm = async () => {
    const payload: DataReleaseJobInput = {
      ...form,
      job_id: form.job_id.trim(),
      name: form.name.trim(),
      priority: form.priority.trim().toLowerCase(),
      github_remote: form.github_remote.trim() || "origin",
      github_branch: form.github_branch?.trim() || null,
      cloudflare_project_name: form.cloudflare_project_name?.trim() || null,
      commit_message_template: form.commit_message_template.trim() || defaultForm.commit_message_template,
      daily_time: form.daily_time?.trim() || null,
      timezone: form.timezone?.trim() || config?.timezone || "UTC",
      notes: form.notes?.trim() || null,
      interval_minutes: form.interval_minutes ? Number(form.interval_minutes) : null,
    };
    if (editingJobId) {
      await updateJob.mutateAsync({ jobId: editingJobId, payload });
    } else {
      await createJob.mutateAsync(payload);
      setSelectedJobId(payload.job_id);
    }
    resetForm();
  };

  const removeJob = async (job: DataReleaseJob) => {
    const ok = window.confirm(
      lang === "zh" ? `确认删除发布任务 ${job.name} 吗？` : `Delete data release job ${job.name}?`,
    );
    if (!ok) return;
    await deleteJob.mutateAsync(job.job_id);
    if (selectedJobId === job.job_id) setSelectedJobId(null);
    if (editingJobId === job.job_id) resetForm();
  };

  const runSelectedJob = async (jobId: string) => {
    const result = await runJob.mutateAsync(jobId);
    if (result.task_uuid) {
      setExpandedTaskUuid(result.task_uuid);
    }
  };

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="teal" className="w-fit">{t(lang, "mod_database")}</Badge>
        <h1 className="text-3xl font-semibold tracking-tight text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {t(lang, "data_release")}
        </h1>
        <Text>
          {lang === "zh"
            ? "把站点数据导出、下载数据仓库发布、Cloudflare Pages 部署整合成一条统一工作流，支持手动运行、定时调度和 crawl 完成后的自动发布。"
            : "Unify site-data generation, download-repo publishing, and Cloudflare Pages deployment into one workflow with manual runs, scheduling, and automatic post-crawl release."}
        </Text>
      </div>

      <Grid numItemsSm={2} numItemsLg={4} className="gap-4">
        <Card decoration="top" decorationColor="blue">
          <Text>Scheduler</Text>
          <Metric>{config?.enabled ? "On" : "Off"}</Metric>
        </Card>
        <Card decoration="top" decorationColor="teal">
          <Text>Jobs</Text>
          <Metric>{summary.enabled}/{summary.total}</Metric>
        </Card>
        <Card decoration="top" decorationColor="amber">
          <Text>Auto after crawl</Text>
          <Metric>{summary.auto}</Metric>
        </Card>
        <Card decoration="top" decorationColor={checks?.overall_ready ? "emerald" : "rose"}>
          <Text>Access checks</Text>
          <Metric>{checks?.overall_ready ? "Ready" : "Needs attention"}</Metric>
        </Card>
      </Grid>

      <Card className="overflow-hidden border border-tremor-border dark:border-dark-tremor-border">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Title>Runtime & Access</Title>
              <Badge color={!checks ? "slate" : checks.overall_ready ? "emerald" : "rose"}>{accessStatusLabel}</Badge>
              {selectedJob ? <Badge color="slate">{selectedJob.name}</Badge> : null}
            </div>
            <Text>
              Keep the release pipeline healthy, then review GitHub and Cloudflare readiness in the status cards below.
            </Text>
          </div>
          <Button size="xs" variant="secondary" icon={RefreshCw} loading={checkingAccess} onClick={() => refetchChecks()}>
            Refresh
          </Button>
        </div>

        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <RuntimeTile icon={<Clock3 className="h-4 w-4" />} label="Last Tick" value={formatDateTime(config?.last_tick_at)} />
          <RuntimeTile icon={<Wrench className="h-4 w-4" />} label="Poll Interval" value={`${config?.poll_interval_seconds ?? "-"}s`} />
          <RuntimeTile
            icon={<ShieldCheck className="h-4 w-4" />}
            label="Worker"
            value={workerStatus?.worker_process_running ? "running" : "stopped"}
          />
          <RuntimeTile icon={<CheckCircle2 className="h-4 w-4" />} label="Selected Job" value={selectedJob?.name || "No job selected"} />
        </div>

        {checks?.blockers?.length ? (
          <div className="mt-5 rounded-2xl border border-rose-300 bg-rose-50 p-4 text-sm text-rose-900 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200">
            <div className="flex items-center gap-2 font-medium">
              <AlertTriangle className="h-4 w-4" />
              Blockers
            </div>
            <div className="mt-2 space-y-1">
              {checks.blockers.map((blocker) => (
                <Text key={blocker}>{blocker}</Text>
              ))}
            </div>
          </div>
        ) : null}

        {!checks ? (
          <div className="mt-5 rounded-2xl border border-dashed border-tremor-border bg-tremor-background-muted/60 p-4 text-sm text-tremor-content dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/30 dark:text-dark-tremor-content">
            {loadingChecks || checkingAccess
              ? "Checking the latest release prerequisites. GitHub and Cloudflare details will appear here in a moment."
              : "No preflight snapshot yet. Click Refresh or run a check from a release job to load the latest access status."}
          </div>
        ) : null}
      </Card>

      <Grid numItemsLg={2} className="gap-6">
        <Card className="overflow-hidden border border-tremor-border dark:border-dark-tremor-border">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="flex items-start gap-3">
              <div className="rounded-2xl bg-sky-50 p-3 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
                <GitBranch className="h-5 w-5" />
              </div>
              <div>
                <Title>GitHub Data Share</Title>
                <Text className="mt-1">Validate the dedicated download-data repository before publishing release artifacts.</Text>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge color={!checks ? "slate" : checks.git.read_access_ok ? "emerald" : "rose"}>
                {!checks ? "Read check pending" : checks.git.read_access_ok ? "Read access ok" : "Read access failed"}
              </Badge>
              <Badge color={!checks ? "slate" : checks.git.write_access_ok ? "emerald" : "rose"}>
                {!checks ? "Write check pending" : checks.git.write_access_ok ? "Write access ok" : "Write access failed"}
              </Badge>
            </div>
          </div>

          {checks ? (
            <>
              <div className="mt-5 grid gap-3 md:grid-cols-2">
                <AccessDetail label="Config Env" value={checks.git.env_var || "-"} />
                <AccessDetail label="Branch" value={checks.git.branch || "-"} />
                <AccessDetail label="Repo URL" value={checks.git.repo_url || "-"} mono />
                <AccessDetail label="Raw Base" value={checks.git.raw_base_url || "-"} mono />
                <AccessDetail label="Release-path Changes" value={String(checks.git.dirty_release_paths.length ?? 0)} />
                <AccessDetail label="Other Local Changes" value={String(checks.git.dirty_blocking_paths.length ?? 0)} />
              </div>

              {!selectedJob?.include_git_push ? (
                <div className="mt-4 rounded-2xl border border-dashed border-tremor-border bg-tremor-background-muted/60 p-3 text-sm text-tremor-content dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/30 dark:text-dark-tremor-content">
                  Download-repo publishing is disabled for this release job, so the GitHub status is informational only.
                </div>
              ) : null}

              <div className="mt-4 grid gap-3">
                <CheckOutput label="Read Check Output" value={checks.git.read_check_output} />
                <CheckOutput label="Write Check Output" value={checks.git.write_check_output} />
              </div>
            </>
          ) : null}
        </Card>

        <Card className="overflow-hidden border border-tremor-border dark:border-dark-tremor-border">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="flex items-start gap-3">
              <div className="rounded-2xl bg-emerald-50 p-3 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
                <Cloud className="h-5 w-5" />
              </div>
              <div>
                <Title>Cloudflare Pages</Title>
                <Text className="mt-1">Check deployment credentials, target project, and Wrangler availability for the Pages release step.</Text>
              </div>
            </div>
            <Badge color={!checks ? "slate" : checks.cloudflare.project_access_ok ? "emerald" : "rose"}>
              {!checks ? "Project check pending" : checks.cloudflare.project_access_ok ? "Project reachable" : "Project check failed"}
            </Badge>
          </div>

          {checks ? (
            <>
              <div className="mt-5 grid gap-3 md:grid-cols-2">
                <AccessDetail label="Project" value={checks.cloudflare.project_name || "-"} />
                <AccessDetail label="Wrangler" value={checks.commands.wrangler_version || "-"} mono />
                <AccessDetail label="Token Present" value={checks.cloudflare.token_present ? "yes" : "no"} />
                <AccessDetail label="Account ID Present" value={checks.cloudflare.account_id_present ? "yes" : "no"} />
              </div>

              {checks.cloudflare.error ? (
                <div className="mt-4 rounded-2xl border border-rose-300 bg-rose-50 p-4 text-sm text-rose-900 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200">
                  <div className="flex items-center gap-2 font-medium">
                    <AlertTriangle className="h-4 w-4" />
                    Cloudflare Error
                  </div>
                  <Text className="mt-2 break-words text-rose-700 dark:text-rose-300">{checks.cloudflare.error}</Text>
                </div>
              ) : null}
            </>
          ) : null}
        </Card>
      </Grid>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <div className="rounded-tremor-default border border-dashed border-tremor-border p-4 dark:border-dark-tremor-border">
            <div className="flex items-center justify-between gap-2">
              <Title>{editingJobId ? "Edit release job" : "New release job"}</Title>
              <Button size="xs" variant="light" onClick={resetForm}>Reset</Button>
            </div>

            <div className="mt-4 space-y-3">
              <input
                className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                placeholder="job_id"
                value={form.job_id}
                disabled={!!editingJobId}
                onChange={(e) => setForm((prev) => ({ ...prev, job_id: e.target.value }))}
              />
              <input
                className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                placeholder="name"
                value={form.name}
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
              />
              <div className="grid grid-cols-2 gap-3">
                <select
                  className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  value={form.priority}
                  onChange={(e) => setForm((prev) => ({ ...prev, priority: e.target.value }))}
                >
                  {["low", "normal", "high", "urgent"].map((priority) => (
                    <option key={priority} value={priority}>{priority}</option>
                  ))}
                </select>
                <select
                  className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  value={form.enabled ? "yes" : "no"}
                  onChange={(e) => setForm((prev) => ({ ...prev, enabled: e.target.value === "yes" }))}
                >
                  <option value="yes">enabled</option>
                  <option value="no">disabled</option>
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <input
                  className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  placeholder="daily_time HH:MM"
                  value={form.daily_time ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, daily_time: e.target.value }))}
                />
                <input
                  type="number"
                  className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  placeholder="interval minutes"
                  value={form.interval_minutes ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, interval_minutes: e.target.value ? Number(e.target.value) : null }))}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <input
                  className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  placeholder="timezone"
                  value={form.timezone ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, timezone: e.target.value }))}
                />
                <input
                  className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  value="GITHUB_DATA_SHARE_REPO_URL"
                  disabled
                  readOnly
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <input
                  className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  placeholder="download repo branch"
                  value={form.github_branch ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, github_branch: e.target.value }))}
                />
                <input
                  className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  placeholder="cloudflare project"
                  value={form.cloudflare_project_name ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, cloudflare_project_name: e.target.value }))}
                />
              </div>
              <textarea
                className="min-h-20 w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                placeholder="commit message template"
                value={form.commit_message_template}
                onChange={(e) => setForm((prev) => ({ ...prev, commit_message_template: e.target.value }))}
              />
              <textarea
                className="min-h-20 w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                placeholder="notes"
                value={form.notes ?? ""}
                onChange={(e) => setForm((prev) => ({ ...prev, notes: e.target.value }))}
              />
              <div className="grid grid-cols-2 gap-2 text-sm">
                {([
                  ["auto_after_crawls", form.auto_after_crawls, "Auto-trigger after all crawl tasks finish"],
                  ["include_git_push", form.include_git_push, "Publish generated downloads to the GitHub data-share repo"],
                  ["include_cloudflare_deploy", form.include_cloudflare_deploy, "Deploy Astro dist to Cloudflare Pages"],
                  ["require_clean_worktree", form.require_clean_worktree, "Block release if repo has unrelated dirty files"],
                ] as Array<[BooleanReleaseField, boolean, string]>).map(([key, value, label]) => (
                  <label key={String(key)} className="flex items-center gap-2 rounded-tremor-default border border-tremor-border px-3 py-2 dark:border-dark-tremor-border">
                    <input
                      type="checkbox"
                      checked={Boolean(value)}
                      onChange={(e) => setForm((prev) => ({ ...prev, [key]: e.target.checked }))}
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
              <Button
                icon={editingJobId ? Pencil : Plus}
                loading={createJob.isPending || updateJob.isPending}
                onClick={submitForm}
              >
                {editingJobId ? "Save changes" : "Create release job"}
              </Button>
            </div>
          </div>
        </Card>

        <Card className="lg:col-span-2">
          <Title>Release Jobs</Title>
          <Text className="mt-1">Each job can be scheduled, triggered manually, or auto-fired after crawl completion.</Text>

          <div className="mt-4 space-y-4">
            {isLoading ? (
              [1, 2].map((idx) => (
                <div key={idx} className="h-28 animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
              ))
            ) : !(jobs?.length) ? (
              <div className="rounded-tremor-default border border-dashed border-tremor-border p-8 text-center dark:border-dark-tremor-border">
                <Text>No data release jobs configured.</Text>
              </div>
            ) : (
              jobs.map((job) => (
                <Card key={job.job_id} className="border border-tremor-border dark:border-dark-tremor-border">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Title>{job.name}</Title>
                        <Badge color={job.enabled ? "emerald" : "slate"}>{job.enabled ? "enabled" : "disabled"}</Badge>
                        <Badge color={statusColor(job.last_status)}>{job.last_status}</Badge>
                        {job.auto_after_crawls ? <Badge color="blue">auto after crawl</Badge> : null}
                      </div>
                      <Text>{scheduleLabel(job)}</Text>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button size="xs" icon={Play} loading={runJob.isPending} onClick={() => runSelectedJob(job.job_id)}>
                        Run now
                      </Button>
                      <Button size="xs" variant="secondary" icon={CheckCircle2} onClick={() => { setSelectedJobId(job.job_id); refetchChecks(); }}>
                        Check access
                      </Button>
                      <Button size="xs" variant="secondary" icon={Pencil} onClick={() => startEdit(job)}>
                        Edit
                      </Button>
                      <Button size="xs" color="rose" variant="secondary" icon={Trash2} loading={deleteJob.isPending} onClick={() => removeJob(job)}>
                        Delete
                      </Button>
                    </div>
                  </div>

                  <Grid numItemsSm={2} numItemsLg={4} className="mt-4 gap-3">
                    <Card className="p-3">
                      <Text>Next run</Text>
                      <Text className="mt-1 font-medium">{formatDateTime(job.next_run_at)}</Text>
                    </Card>
                    <Card className="p-3">
                      <Text>Last task</Text>
                      <Text className="mt-1 break-all font-mono text-xs font-medium">{job.last_task_uuid || "-"}</Text>
                    </Card>
                    <Card className="p-3">
                      <Text>Download branch</Text>
                      <Text className="mt-1 font-medium">{job.github_branch || "-"}</Text>
                    </Card>
                    <Card className="p-3">
                      <Text>Pages project</Text>
                      <Text className="mt-1 font-medium">{job.cloudflare_project_name || "-"}</Text>
                    </Card>
                  </Grid>

                  <div className="mt-4 grid gap-2 text-sm text-tremor-content dark:text-dark-tremor-content md:grid-cols-2">
                    <Text>Priority: {job.priority}</Text>
                    <Text>Last started: {formatDateTime(job.last_started_at)}</Text>
                    <Text>Download repo publish: {job.include_git_push ? "yes" : "no"}</Text>
                    <Text>Last finished: {formatDateTime(job.last_finished_at)}</Text>
                    <Text>Pages deploy: {job.include_cloudflare_deploy ? "yes" : "no"}</Text>
                    <Text>Run count: {job.run_count}</Text>
                    <Text>Require clean worktree: {job.require_clean_worktree ? "yes" : "no"}</Text>
                    <Text>Skipped count: {job.skipped_count}</Text>
                    <Text className="break-words">Last error: {job.last_error || "-"}</Text>
                    {job.notes ? <Text className="break-words md:col-span-2">Notes: {job.notes}</Text> : null}
                  </div>
                </Card>
              ))
            )}
          </div>
        </Card>
      </div>

      <Card>
        <Title>Recent Release Tasks</Title>
        <Text className="mt-1">Every release run is tracked as an `EXPORT_DATA` task. Expand any row to inspect the raw workbook log stream.</Text>

        <div className="mt-4 space-y-3">
          {!releaseTasks?.length ? (
            <div className="rounded-tremor-default border border-dashed border-tremor-border p-6 text-center dark:border-dark-tremor-border">
              <Text>No release tasks yet.</Text>
            </div>
          ) : (
            releaseTasks.map((task) => {
              const expanded = expandedTaskUuid === task.task_uuid;
              const matchingDetail = expanded && expandedTaskDetail?.task_uuid === task.task_uuid ? expandedTaskDetail : undefined;
              const loadingExpandedDetail = expanded && !matchingDetail && (detailLoading || detailFetching);

              return (
                <Card key={task.task_uuid} className="overflow-hidden p-0">
                  <button
                    type="button"
                    className="w-full text-left transition hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-subtle"
                    onClick={() => setExpandedTaskUuid(expanded ? null : task.task_uuid)}
                  >
                    <div className="flex items-center gap-3 px-4 py-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <Text className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                            {task.task_name}
                          </Text>
                          <Badge color={statusColor(task.status)}>{task.status}</Badge>
                        </div>
                        <div className="mt-2 grid gap-1 text-xs text-tremor-content dark:text-dark-tremor-content md:hidden">
                          <Text>Created: {formatDateTime(task.created_at)}</Text>
                          <Text>Completed: {formatDateTime(task.completed_at)}</Text>
                        </div>
                        <Text className="mt-2 break-all font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle md:hidden">
                          {task.task_uuid}
                        </Text>
                        {task.last_error ? (
                          <Text className="mt-2 break-words text-xs text-rose-700 dark:text-rose-300 md:hidden">
                            {task.last_error}
                          </Text>
                        ) : null}
                      </div>

                      <div className="hidden shrink-0 items-center gap-3 md:flex">
                        <div className="w-44">
                          <div className="flex items-center gap-2">
                            <ProgressBar value={task.progress} color={task.progress === 100 ? "emerald" : "teal"} className="flex-1" />
                            <Text>{task.progress}%</Text>
                          </div>
                        </div>
                        <div className="w-44 text-right text-xs text-tremor-content dark:text-dark-tremor-content">
                          <Text>Created: {formatDateTime(task.created_at)}</Text>
                          <Text>Completed: {formatDateTime(task.completed_at)}</Text>
                        </div>
                        <ChevronDown className={`h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform ${expanded ? "rotate-180" : ""}`} />
                      </div>
                    </div>
                  </button>

                  <div className="border-t border-transparent px-4 pb-3 md:hidden">
                    <div className="flex items-center gap-2">
                      <ProgressBar value={task.progress} color={task.progress === 100 ? "emerald" : "teal"} className="flex-1" />
                      <Text>{task.progress}%</Text>
                      <ChevronDown className={`h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform ${expanded ? "rotate-180" : ""}`} />
                    </div>
                  </div>

                  {expanded ? (
                    <div className="border-t border-tremor-border px-4 pb-4 pt-4 dark:border-dark-tremor-border">
                      {task.last_error ? (
                        <div className="mb-4 rounded-2xl border border-rose-300 bg-rose-50 p-4 text-sm text-rose-900 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200">
                          {task.last_error}
                        </div>
                      ) : null}
                      <RawReleaseTaskDetail taskDetail={matchingDetail} detailLoading={loadingExpandedDetail} />
                    </div>
                  ) : null}
                </Card>
              );
            })
          )}
        </div>
      </Card>
    </div>
  );
}
