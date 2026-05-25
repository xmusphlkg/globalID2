"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type FormEvent, type MouseEvent, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  CheckCircle2,
  Clock3,
  Cloud,
  ExternalLink,
  GitBranch,
  Mail,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Trash2,
  Wrench,
} from "lucide-react";

import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DetailDrawer } from "@/components/ui/DetailDrawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
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
import { useSettings } from "@/lib/hooks/useSettings";
import {
  type TaskItem,
  useCancelTask,
  useTaskDetail,
  useTasks,
  useTaskWebSocket,
  useWorkerStatus,
} from "@/lib/hooks/useTasks";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
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
  | "enabled"
  | "auto_after_crawls"
  | "include_git_push"
  | "include_cloudflare_deploy"
  | "require_clean_worktree";
type JobFilter = "all" | "enabled" | "disabled" | "failed" | "auto";

const inputClass =
  "h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted disabled:bg-tremor-background-subtle disabled:text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function relativeTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.max(0, Math.round(diffMs / 1000));
  const diffMin = Math.round(diffSec / 60);
  const diffHr = Math.round(diffMin / 60);
  const diffDay = Math.round(diffHr / 24);
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${diffDay}d ago`;
}

function scheduleLabel(job: { interval_minutes?: number | null; daily_time?: string | null; timezone?: string | null }): string {
  if (job.interval_minutes) return `Every ${job.interval_minutes} min`;
  if (job.daily_time) return `Daily ${job.daily_time} (${job.timezone || "UTC"})`;
  return "Manual / auto after data task";
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

function statusTone(status?: string | null): "neutral" | "info" | "success" | "warning" | "danger" | "primary" {
  const normalized = (status || "").toLowerCase();
  if (normalized === "completed" || normalized === "enabled" || normalized === "ready") return "success";
  if (normalized === "running" || normalized === "queued" || normalized === "retrying") return "warning";
  if (normalized === "failed" || normalized === "blocked" || normalized === "stopped") return "danger";
  if (normalized === "disabled" || normalized === "cancelled" || normalized === "skipped") return "neutral";
  return "info";
}

function priorityTone(priority: string): "neutral" | "info" | "success" | "warning" | "danger" | "primary" {
  if (priority === "critical") return "danger";
  if (priority === "high") return "warning";
  if (priority === "normal") return "primary";
  return "neutral";
}

function FormSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-b border-tremor-border pb-5 last:border-b-0 last:pb-0 dark:border-dark-tremor-border">
      <h3 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Field({
  label,
  children,
  hint,
  className,
}: {
  label: string;
  children: ReactNode;
  hint?: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("block space-y-1.5", className)}>
      <span className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {label}
      </span>
      {children}
      {hint ? <span className="block text-xs text-tremor-content dark:text-dark-tremor-content">{hint}</span> : null}
    </label>
  );
}

function ActionButton({
  children,
  icon,
  tone = "neutral",
  disabled,
  onClick,
  type = "button",
  className,
}: {
  children: ReactNode;
  icon?: ReactNode;
  tone?: "neutral" | "primary" | "danger";
  disabled?: boolean;
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
  type?: "button" | "submit";
  className?: string;
}) {
  const toneClass =
    tone === "primary"
      ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted hover:bg-tremor-brand/90"
      : tone === "danger"
        ? "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300"
        : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle";

  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 items-center justify-center gap-2 rounded-tremor-default border px-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-55",
        toneClass,
        className,
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function AlertPanel({ tone, children }: { tone: "warning" | "danger" | "info"; children: ReactNode }) {
  const toneClass =
    tone === "danger"
      ? "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/50 dark:bg-rose-950/25 dark:text-rose-200"
      : tone === "warning"
        ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/25 dark:text-amber-200"
        : "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900/50 dark:bg-sky-950/25 dark:text-sky-200";

  return <div className={cn("rounded-tremor-default border px-4 py-3 text-sm", toneClass)}>{children}</div>;
}

function RuntimeLine({ icon, label, value }: { icon: ReactNode; label: string; value: ReactNode }) {
  return (
    <div className="flex items-start gap-3 border-b border-tremor-border py-3 last:border-b-0 dark:border-dark-tremor-border">
      <div className="mt-0.5 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{icon}</div>
      <div className="min-w-0">
        <p className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{label}</p>
        <div className="mt-1 text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">{value}</div>
      </div>
    </div>
  );
}

function AccessDetail({ label, value, mono = false }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
      <p className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{label}</p>
      <div className={cn("mt-1 text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong", mono ? "break-all font-mono text-xs" : "break-words")}>
        {value}
      </div>
    </div>
  );
}

function CheckOutput({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="rounded-tremor-default border border-dashed border-tremor-border bg-tremor-background-muted/50 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/30">
      <p className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{label}</p>
      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-tremor-content dark:text-dark-tremor-content">
        {value}
      </pre>
    </div>
  );
}

function ProgressCell({ value, status }: { value: number; status: string }) {
  const clamped = Math.min(100, Math.max(0, value));
  const color = status === "failed" ? "#c24139" : clamped >= 100 ? "#0d9488" : "#0f766e";
  return (
    <div className="flex min-w-[130px] items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-tremor-full bg-tremor-background-muted dark:bg-dark-tremor-background-muted">
        <div className="h-full rounded-tremor-full" style={{ width: `${clamped}%`, background: color }} />
      </div>
      <span className="w-10 text-right text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{value}%</span>
    </div>
  );
}

export default function DataReleasePage() {
  const { lang } = useAppStore();
  const { data: config } = useDataReleaseConfig();
  const { data: jobs, isLoading } = useDataReleaseJobs();
  const { data: workerStatus } = useWorkerStatus();
  const { data: settings } = useSettings();
  const { data: releaseTasks, refetch: releaseTasksRefetch } = useTasks(undefined, "export_data", undefined, undefined, 20);
  const cancelTask = useCancelTask();
  const runJob = useRunDataReleaseJob();
  const createJob = useCreateDataReleaseJob();
  const updateJob = useUpdateDataReleaseJob();
  const deleteJob = useDeleteDataReleaseJob();

  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [jobDrawerOpen, setJobDrawerOpen] = useState(false);
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  const [form, setForm] = useState<DataReleaseJobInput>(defaultForm);
  const [jobSearch, setJobSearch] = useState("");
  const [jobFilter, setJobFilter] = useState<JobFilter>("all");
  const [taskDetailUuid, setTaskDetailUuid] = useState<string | null>(null);

  const { data: checks, refetch: refetchChecks, isFetching: checkingAccess, isLoading: loadingChecks } = useDataReleaseChecks(selectedJobId);
  const { data: taskDetail, isFetching: taskDetailLoading } = useTaskDetail(taskDetailUuid);

  useTaskWebSocket({ extraQueryKeys: [["data-release"], ["data-release-jobs"], ["data-release-checks"], ["tasks"], ["task"]] });

  const selectedJob = useMemo(() => jobs?.find((job) => job.job_id === selectedJobId) ?? null, [jobs, selectedJobId]);

  useEffect(() => {
    if (!selectedJobId && jobs?.length) setSelectedJobId(jobs[0].job_id);
  }, [jobs, selectedJobId]);

  const summary = useMemo(() => {
    const rows = jobs ?? [];
    return {
      total: rows.length,
      enabled: rows.filter((job) => job.enabled).length,
      disabled: rows.filter((job) => !job.enabled).length,
      auto: rows.filter((job) => job.auto_after_crawls).length,
      failed: rows.filter((job) => job.last_status === "failed").length,
      runs: rows.reduce((total, job) => total + job.run_count, 0),
    };
  }, [jobs]);

  const filteredJobs = useMemo(() => {
    const search = jobSearch.trim().toLowerCase();
    return (jobs ?? []).filter((job) => {
      const matchesFilter =
        jobFilter === "all" ||
        (jobFilter === "enabled" && job.enabled) ||
        (jobFilter === "disabled" && !job.enabled) ||
        (jobFilter === "failed" && job.last_status === "failed") ||
        (jobFilter === "auto" && job.auto_after_crawls);
      if (!matchesFilter) return false;
      if (!search) return true;

      return [
        job.job_id,
        job.name,
        job.priority,
        job.last_status,
        job.github_remote,
        job.github_branch ?? "",
        job.cloudflare_project_name ?? "",
        job.last_task_uuid ?? "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(search);
    });
  }, [jobFilter, jobSearch, jobs]);

  const accessStatus = !checks ? (loadingChecks || checkingAccess ? "Checking" : "Pending") : checks.overall_ready ? "Ready" : "Blocked";
  const accessTone = !checks ? "neutral" : checks.overall_ready ? "success" : "danger";
  const smtpReady = Boolean(settings?.smtp.alerting_ready);
  const smtpConfigured = Boolean(settings?.smtp.smtp_configured);
  const smtpTone = smtpReady ? "success" : smtpConfigured ? "warning" : "neutral";
  const schedulerEnabled = Boolean(config?.enabled);
  const workerRunning = Boolean(workerStatus?.worker_process_running);
  const saving = createJob.isPending || updateJob.isPending;

  const resetForm = () => {
    setEditingJobId(null);
    setForm({
      ...defaultForm,
      timezone: config?.timezone || defaultForm.timezone,
      github_branch: selectedJob?.github_branch || settings?.github.default_github_branch || defaultForm.github_branch,
      cloudflare_project_name: selectedJob?.cloudflare_project_name || settings?.cloudflare.default_cloudflare_project_name || defaultForm.cloudflare_project_name,
      github_remote: selectedJob?.github_remote || settings?.github.default_github_remote || defaultForm.github_remote,
    });
  };

  const openCreateDrawer = () => {
    setEditingJobId(null);
    setForm({
      ...defaultForm,
      timezone: config?.timezone || "UTC",
      github_remote: settings?.github.default_github_remote || defaultForm.github_remote,
      github_branch: settings?.github.default_github_branch || defaultForm.github_branch,
      cloudflare_project_name: settings?.cloudflare.default_cloudflare_project_name || defaultForm.cloudflare_project_name,
    });
    setJobDrawerOpen(true);
  };

  const openEditDrawer = (job: DataReleaseJob) => {
    setEditingJobId(job.job_id);
    setSelectedJobId(job.job_id);
    setForm(toForm(job));
    setJobDrawerOpen(true);
  };

  const closeJobDrawer = () => {
    setJobDrawerOpen(false);
    resetForm();
  };

  const updateFormField = (patch: Partial<DataReleaseJobInput>) => setForm((prev) => ({ ...prev, ...patch }));

  const submitForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
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
    setJobDrawerOpen(false);
    resetForm();
  };

  const removeJob = async (job: DataReleaseJob) => {
    if (!window.confirm(`Delete release job "${job.name}"?`)) return;
    await deleteJob.mutateAsync(job.job_id);
    if (selectedJobId === job.job_id) setSelectedJobId(null);
    if (editingJobId === job.job_id) closeJobDrawer();
  };

  const runSelectedJob = async (jobId: string) => {
    const result = await runJob.mutateAsync(jobId);
    if (result.task_uuid) setTaskDetailUuid(result.task_uuid);
  };

  const handleCancelTask = async (taskUuid: string) => {
    if (!window.confirm("Cancel this release task?")) return;
    await cancelTask.mutateAsync(taskUuid);
  };

  const jobColumns = useMemo<DataTableColumn<DataReleaseJob>[]>(
    () => [
      {
        key: "status",
        header: lang === "zh" ? "状态" : "Status",
        render: (job) => (
          <div className="space-y-1">
            <StatusBadge tone={job.enabled ? "success" : "neutral"}>{job.enabled ? "enabled" : "disabled"}</StatusBadge>
            <StatusBadge tone={priorityTone(job.priority)}>{job.priority}</StatusBadge>
          </div>
        ),
      },
      {
        key: "job",
        header: lang === "zh" ? "发布 job" : "Release Job",
        render: (job) => (
          <div className="min-w-[240px] max-w-[440px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{job.name}</p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{job.job_id}</p>
          </div>
        ),
      },
      {
        key: "schedule",
        header: lang === "zh" ? "计划" : "Schedule",
        render: (job) => (
          <div className="min-w-[180px]">
            <p className="text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">{scheduleLabel(job)}</p>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {schedulerEnabled ? formatDateTime(job.next_run_at) : (lang === "zh" ? "调度器关闭" : "Scheduler off")}
            </p>
          </div>
        ),
      },
      {
        key: "targets",
        header: lang === "zh" ? "目标" : "Targets",
        render: (job) => (
          <div className="min-w-[190px] text-sm text-tremor-content dark:text-dark-tremor-content">
            <p className="truncate">
              Git: {job.github_remote}/{job.github_branch || "-"}
            </p>
            <p className="mt-1 truncate">CF: {job.cloudflare_project_name || "-"}</p>
          </div>
        ),
      },
      {
        key: "last",
        header: lang === "zh" ? "最近运行" : "Last Run",
        render: (job) => (
          <div className="min-w-[150px]">
            <StatusBadge status={job.last_status}>{job.last_status || "-"}</StatusBadge>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {formatDateTime(job.last_finished_at || job.last_started_at)}
            </p>
          </div>
        ),
      },
      {
        key: "runs",
        header: lang === "zh" ? "次数" : "Runs",
        render: (job) => (
          <div className="whitespace-nowrap text-sm text-tremor-content dark:text-dark-tremor-content">
            <span className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{job.run_count}</span>
            <span className="text-tremor-content-subtle dark:text-dark-tremor-content-subtle"> / {job.skipped_count}</span>
          </div>
        ),
      },
      {
        key: "actions",
        header: "",
        className: "text-right",
        render: (job) => (
          <div className="flex min-w-[230px] justify-end gap-2">
            <ActionButton
              disabled={runJob.isPending}
              icon={<Play className="h-4 w-4" />}
              onClick={(event) => {
                event.stopPropagation();
                void runSelectedJob(job.job_id);
              }}
            >
              {lang === "zh" ? "运行" : "Run"}
            </ActionButton>
            <ActionButton
              icon={<Pencil className="h-4 w-4" />}
              onClick={(event) => {
                event.stopPropagation();
                openEditDrawer(job);
              }}
            >
              {lang === "zh" ? "编辑" : "Edit"}
            </ActionButton>
            <ActionButton
              tone="danger"
              disabled={deleteJob.isPending}
              icon={<Trash2 className="h-4 w-4" />}
              onClick={(event) => {
                event.stopPropagation();
                void removeJob(job);
              }}
            >
              {lang === "zh" ? "删除" : "Delete"}
            </ActionButton>
          </div>
        ),
      },
    ],
    [deleteJob.isPending, lang, runJob.isPending, schedulerEnabled],
  );

  const taskColumns = useMemo<DataTableColumn<TaskItem>[]>(
    () => [
      {
        key: "status",
        header: lang === "zh" ? "状态" : "Status",
        render: (task) => <StatusBadge status={task.status}>{task.status}</StatusBadge>,
      },
      {
        key: "task",
        header: lang === "zh" ? "任务" : "Task",
        render: (task) => (
          <div className="min-w-[240px] max-w-[460px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{task.task_name || task.task_type}</p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{task.task_uuid}</p>
          </div>
        ),
      },
      {
        key: "progress",
        header: lang === "zh" ? "进度" : "Progress",
        render: (task) => <ProgressCell value={task.progress} status={task.status} />,
      },
      {
        key: "created",
        header: lang === "zh" ? "创建时间" : "Created",
        render: (task) => (
          <div className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle" title={formatDateTime(task.created_at)}>
            {relativeTime(task.created_at)}
          </div>
        ),
      },
      {
        key: "actions",
        header: "",
        className: "text-right",
        render: (task) => {
          const canCancel = ["pending", "queued", "running", "retrying"].includes(task.status) && !task.cancel_requested;
          return (
            <div className="flex min-w-[150px] justify-end gap-2">
              <ActionButton
                disabled={!canCancel || cancelTask.isPending}
                tone={canCancel ? "danger" : "neutral"}
                icon={<Ban className="h-4 w-4" />}
                onClick={(event) => {
                  event.stopPropagation();
                  if (canCancel) void handleCancelTask(task.task_uuid);
                }}
              >
                {canCancel ? (lang === "zh" ? "取消" : "Cancel") : "-"}
              </ActionButton>
            </div>
          );
        },
      },
    ],
    [cancelTask.isPending, lang],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_database")}
        title={t(lang, "data_release")}
        description={
          lang === "zh"
            ? "统一管理站点数据导出、Git 发布、Cloudflare 部署和发布前访问检查。"
            : "Manage site data export, Git publishing, Cloudflare deployment, and preflight access checks."
        }
        meta={
          <>
            <StatusBadge status={schedulerEnabled ? "enabled" : "disabled"}>
              {schedulerEnabled ? (lang === "zh" ? "调度器开启" : "Scheduler on") : (lang === "zh" ? "调度器关闭" : "Scheduler off")}
            </StatusBadge>
            <StatusBadge status={workerRunning ? "enabled" : "stopped"}>
              {workerRunning ? (lang === "zh" ? "Worker 运行中" : "Worker running") : (lang === "zh" ? "Worker 停止" : "Worker stopped")}
            </StatusBadge>
            <StatusBadge tone={accessTone}>{accessStatus}</StatusBadge>
          </>
        }
        actions={
          <>
            <Link
              href="/setting"
              className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              {lang === "zh" ? "设置中心" : "Settings"}
              <ExternalLink className="h-4 w-4" />
            </Link>
            <ActionButton
              disabled={!selectedJob || runJob.isPending}
              onClick={() => selectedJob && void runSelectedJob(selectedJob.job_id)}
              icon={<Play className="h-4 w-4" />}
            >
              {lang === "zh" ? "运行选中" : "Run Selected"}
            </ActionButton>
            <ActionButton tone="primary" onClick={openCreateDrawer} icon={<Plus className="h-4 w-4" />}>
              {lang === "zh" ? "新建 job" : "New job"}
            </ActionButton>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={lang === "zh" ? "调度器" : "Scheduler"}
          value={config?.enabled ? "On" : "Off"}
          icon={<RefreshCw className="h-4 w-4" />}
          tone={schedulerEnabled ? "success" : "danger"}
          hint={lang === "zh" ? `轮询间隔 ${config?.poll_interval_seconds ?? "-"}s` : `Poll interval ${config?.poll_interval_seconds ?? "-"}s`}
        />
        <MetricTile
          label={lang === "zh" ? "启用 job" : "Enabled Jobs"}
          value={`${summary.enabled}/${summary.total}`}
          icon={<Wrench className="h-4 w-4" />}
          tone="primary"
          hint={lang === "zh" ? `${summary.disabled} 个停用` : `${summary.disabled} disabled`}
        />
        <MetricTile
          label={lang === "zh" ? "自动触发" : "Auto Trigger"}
          value={summary.auto}
          icon={<Clock3 className="h-4 w-4" />}
          tone="info"
          hint={lang === "zh" ? `累计运行 ${summary.runs} 次` : `${summary.runs} total runs`}
        />
        <MetricTile
          label={lang === "zh" ? "访问检查" : "Access"}
          value={accessStatus}
          icon={<ShieldCheck className="h-4 w-4" />}
          tone={accessTone === "danger" ? "danger" : accessTone === "success" ? "success" : "neutral"}
          hint={checks?.checked_at ? formatDateTime(checks.checked_at) : (lang === "zh" ? "选择 job 后检查" : "Select a job to check")}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="space-y-5">
          <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <div className="mb-1 flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {lang === "zh" ? "运行与检查" : "Runtime & Checks"}
              </h2>
              <ActionButton
                disabled={!selectedJobId || checkingAccess}
                onClick={() => void refetchChecks()}
                icon={<RefreshCw className="h-4 w-4" />}
                className="h-8 px-2.5"
              >
                {lang === "zh" ? "刷新" : "Check"}
              </ActionButton>
            </div>

            <RuntimeLine icon={<Clock3 className="h-4 w-4" />} label={lang === "zh" ? "最近 tick" : "Last tick"} value={formatDateTime(config?.last_tick_at)} />
            <RuntimeLine icon={<Wrench className="h-4 w-4" />} label={lang === "zh" ? "轮询间隔" : "Poll interval"} value={`${config?.poll_interval_seconds ?? "-"}s`} />
            <RuntimeLine icon={<ShieldCheck className="h-4 w-4" />} label="Worker" value={workerRunning ? "running" : "stopped"} />
            <RuntimeLine icon={<CheckCircle2 className="h-4 w-4" />} label={lang === "zh" ? "当前 job" : "Selected job"} value={selectedJob?.name || "-"} />

            {checks?.blockers?.length ? (
              <div className="mt-3">
                <AlertPanel tone="danger">
                  <div className="flex gap-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <div>
                      <p className="font-medium">{lang === "zh" ? "发布阻塞项" : "Blockers"}</p>
                      <div className="mt-1 space-y-1">
                        {checks.blockers.map((blocker) => (
                          <p key={blocker}>{blocker}</p>
                        ))}
                      </div>
                    </div>
                  </div>
                </AlertPanel>
              </div>
            ) : null}
          </section>

          <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <div className="flex items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                <GitBranch className="h-4 w-4" />
                GitHub
              </h2>
              <StatusBadge tone={!checks ? "neutral" : checks.git.read_access_ok && checks.git.write_access_ok ? "success" : "danger"}>
                {!checks ? "Pending" : checks.git.read_access_ok && checks.git.write_access_ok ? "OK" : "Failed"}
              </StatusBadge>
            </div>
            <div className="mt-3 grid gap-2">
              <AccessDetail label="Branch" value={checks?.git.branch || settings?.github.github_data_share_repo_branch || "-"} />
              <AccessDetail label="Repo" value={checks?.git.repo_url || settings?.github.github_data_share_repo_url || "-"} mono />
              <div className="grid gap-2 sm:grid-cols-2">
                <AccessDetail label="Release Changes" value={String(checks?.git.dirty_release_paths.length ?? 0)} />
                <AccessDetail label="Blocking Changes" value={String(checks?.git.dirty_blocking_paths.length ?? 0)} />
              </div>
              <CheckOutput label="Read Check" value={checks?.git.read_check_output} />
              <CheckOutput label="Write Check" value={checks?.git.write_check_output} />
            </div>
          </section>

          <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <div className="flex items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                <Cloud className="h-4 w-4" />
                Cloudflare
              </h2>
              <StatusBadge tone={!checks ? "neutral" : checks.cloudflare.project_access_ok ? "success" : "danger"}>
                {!checks ? "Pending" : checks.cloudflare.project_access_ok ? "OK" : "Failed"}
              </StatusBadge>
            </div>
            <div className="mt-3 grid gap-2">
              <AccessDetail label="Project" value={checks?.cloudflare.project_name || settings?.cloudflare.default_cloudflare_project_name || "-"} />
              <AccessDetail label="Wrangler" value={checks?.commands.wrangler_version || "-"} mono />
              <div className="grid gap-2 sm:grid-cols-2">
                <AccessDetail label="Token" value={checks?.cloudflare.token_present ? "Yes" : settings?.cloudflare.cloudflare_api_token_present ? "Saved" : "No"} />
                <AccessDetail label="Account ID" value={checks?.cloudflare.account_id_present ? "Yes" : settings?.cloudflare.cloudflare_account_id_present ? "Saved" : "No"} />
              </div>
              {checks?.cloudflare.error ? <AlertPanel tone="danger">{checks.cloudflare.error}</AlertPanel> : null}
            </div>
          </section>

          <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <div className="flex items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                <Mail className="h-4 w-4" />
                SMTP
              </h2>
              <StatusBadge tone={smtpTone}>
                {smtpReady ? "Ready" : smtpConfigured ? "Needs Recipients" : "Managed in Settings"}
              </StatusBadge>
            </div>
            <p className="mt-3 text-sm text-tremor-content dark:text-dark-tremor-content">
              {lang === "zh"
                ? "发布页只读取统一设置；连接测试、发测试邮件和收件人管理都在设置中心完成。"
                : "Data release reads shared SMTP settings. Connection tests, test emails, and recipient management live in Settings."}
            </p>
            <div className="mt-3 grid gap-2">
              <AccessDetail label="From" value={settings?.smtp.smtp_from_email || "-"} />
              <AccessDetail label="Recipients" value={settings?.smtp.admin_emails?.length ? settings.smtp.admin_emails.join(", ") : "-"} />
            </div>
            <Link
              href="/setting"
              className="mt-3 inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              {lang === "zh" ? "打开设置中心" : "Open Settings"}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </section>
        </aside>

        <main className="space-y-5">
          <section className="space-y-3">
            <FilterToolbar>
              <input
                type="search"
                value={jobSearch}
                onChange={(event) => setJobSearch(event.target.value)}
                placeholder={lang === "zh" ? "搜索 job、分支、Cloudflare 项目或最近任务" : "Search jobs, branches, Cloudflare projects, or last task"}
                className={cn(inputClass, "min-w-[240px] flex-1")}
              />
              <select
                value={jobFilter}
                onChange={(event) => setJobFilter(event.target.value as JobFilter)}
                className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                aria-label={lang === "zh" ? "筛选发布 job" : "Filter release jobs"}
              >
                <option value="all">{lang === "zh" ? "全部 job" : "All jobs"}</option>
                <option value="enabled">{lang === "zh" ? "已启用" : "Enabled"}</option>
                <option value="disabled">{lang === "zh" ? "已停用" : "Disabled"}</option>
                <option value="failed">{lang === "zh" ? "最近失败" : "Failed last run"}</option>
                <option value="auto">{lang === "zh" ? "自动触发" : "Auto trigger"}</option>
              </select>
              <ActionButton tone="primary" onClick={openCreateDrawer} icon={<Plus className="h-4 w-4" />}>
                {lang === "zh" ? "新建" : "New"}
              </ActionButton>
            </FilterToolbar>

            {isLoading ? (
              <div className="space-y-3">
                {[1, 2, 3].map((item) => (
                  <div
                    key={item}
                    className="h-16 animate-pulse rounded-tremor-default border border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  />
                ))}
              </div>
            ) : (
              <DataTable
                columns={jobColumns}
                rows={filteredJobs}
                getRowKey={(job) => job.job_id}
                selectedRowKey={selectedJobId}
                onRowClick={(job) => setSelectedJobId(job.job_id)}
                emptyState={
                  <EmptyState
                    icon={<Wrench className="h-10 w-10" />}
                    title={lang === "zh" ? "暂无发布 job" : "No release jobs"}
                    description={
                      jobSearch || jobFilter !== "all"
                        ? lang === "zh"
                          ? "当前筛选条件下没有匹配的发布 job。"
                          : "No release jobs match the current filters."
                        : lang === "zh"
                          ? "创建一个 job 后，就可以统一执行导出、Git 发布和 Cloudflare 部署。"
                          : "Create a job to run export, Git publishing, and Cloudflare deployment together."
                    }
                  />
                }
              />
            )}
          </section>

          <section className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {lang === "zh" ? "最近发布任务" : "Recent Release Tasks"}
                </h2>
                <p className="text-sm text-tremor-content dark:text-dark-tremor-content">
                  {lang === "zh" ? "查看最近导出/发布任务的进度和日志。" : "Inspect recent export and release task progress and logs."}
                </p>
              </div>
              <ActionButton onClick={() => void releaseTasksRefetch()} icon={<RefreshCw className="h-4 w-4" />}>
                {lang === "zh" ? "刷新" : "Refresh"}
              </ActionButton>
            </div>

            <DataTable
              columns={taskColumns}
              rows={releaseTasks ?? []}
              getRowKey={(task) => task.task_uuid}
              selectedRowKey={taskDetailUuid}
              onRowClick={(task) => setTaskDetailUuid(task.task_uuid)}
              emptyState={
                <EmptyState
                  icon={<Clock3 className="h-10 w-10" />}
                  title={lang === "zh" ? "暂无发布任务" : "No release tasks yet"}
                  description={lang === "zh" ? "运行发布 job 后，这里会显示任务进度。" : "Run a release job to see task progress here."}
                />
              }
            />
          </section>
        </main>
      </div>

      <DetailDrawer
        open={jobDrawerOpen}
        title={editingJobId ? (lang === "zh" ? "编辑发布 job" : "Edit Release Job") : (lang === "zh" ? "新建发布 job" : "New Release Job")}
        subtitle={editingJobId ?? (lang === "zh" ? "配置导出、Git 发布和 Cloudflare 部署" : "Configure export, Git publishing, and Cloudflare deployment")}
        onClose={closeJobDrawer}
      >
        <form className="space-y-5" onSubmit={submitForm}>
          <FormSection title={lang === "zh" ? "基础信息" : "Basic Info"}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Job ID">
                <input
                  className={inputClass}
                  value={form.job_id}
                  disabled={Boolean(editingJobId)}
                  required
                  onChange={(event) => updateFormField({ job_id: event.target.value })}
                />
              </Field>
              <Field label={lang === "zh" ? "名称" : "Name"}>
                <input
                  className={inputClass}
                  value={form.name}
                  required
                  onChange={(event) => updateFormField({ name: event.target.value })}
                />
              </Field>
              <Field label={lang === "zh" ? "优先级" : "Priority"}>
                <select className={inputClass} value={form.priority} onChange={(event) => updateFormField({ priority: event.target.value })}>
                  <option value="critical">Critical</option>
                  <option value="high">High</option>
                  <option value="normal">Normal</option>
                  <option value="low">Low</option>
                </select>
              </Field>
              <Field label={lang === "zh" ? "时区" : "Timezone"}>
                <input className={inputClass} value={form.timezone ?? ""} onChange={(event) => updateFormField({ timezone: event.target.value })} />
              </Field>
            </div>
          </FormSection>

          <FormSection title={lang === "zh" ? "调度" : "Schedule"}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={lang === "zh" ? "间隔分钟" : "Interval minutes"} hint={lang === "zh" ? "留空表示手动或数据任务后自动触发" : "Leave empty for manual or auto-after-data-task runs"}>
                <input
                  type="number"
                  min="1"
                  className={inputClass}
                  value={form.interval_minutes ?? ""}
                  onChange={(event) => updateFormField({ interval_minutes: event.target.value ? Number(event.target.value) : null })}
                />
              </Field>
              <Field label={lang === "zh" ? "每日时间" : "Daily time"} hint="HH:MM">
                <input className={inputClass} value={form.daily_time ?? ""} onChange={(event) => updateFormField({ daily_time: event.target.value || null })} />
              </Field>
            </div>
            <AlertPanel tone="info">{scheduleLabel(form)}</AlertPanel>
          </FormSection>

          <FormSection title="GitHub">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Remote">
                <input className={inputClass} value={form.github_remote} onChange={(event) => updateFormField({ github_remote: event.target.value })} />
              </Field>
              <Field label="Branch">
                <input className={inputClass} value={form.github_branch ?? ""} onChange={(event) => updateFormField({ github_branch: event.target.value })} />
              </Field>
            </div>
            <Field label={lang === "zh" ? "提交信息模板" : "Commit message template"}>
              <input
                className={cn(inputClass, "font-mono")}
                value={form.commit_message_template}
                onChange={(event) => updateFormField({ commit_message_template: event.target.value })}
              />
            </Field>
          </FormSection>

          <FormSection title="Cloudflare">
            <Field label={lang === "zh" ? "项目名" : "Project name"}>
              <input
                className={inputClass}
                value={form.cloudflare_project_name ?? ""}
                onChange={(event) => updateFormField({ cloudflare_project_name: event.target.value })}
              />
            </Field>
          </FormSection>

          <FormSection title={lang === "zh" ? "执行选项" : "Execution"}>
            <div className="grid gap-2 sm:grid-cols-2">
              {([
                ["enabled", form.enabled, lang === "zh" ? "启用" : "Enabled"],
                ["auto_after_crawls", form.auto_after_crawls, lang === "zh" ? "数据任务后自动发布" : "Auto after data tasks"],
                ["include_git_push", form.include_git_push, lang === "zh" ? "包含 Git push" : "Include Git push"],
                ["include_cloudflare_deploy", form.include_cloudflare_deploy, lang === "zh" ? "包含 Cloudflare 部署" : "Include Cloudflare deploy"],
                ["require_clean_worktree", form.require_clean_worktree, lang === "zh" ? "要求干净工作区" : "Require clean worktree"],
              ] as Array<[BooleanReleaseField, boolean, string]>).map(([field, checked, label]) => (
                <label
                  key={field}
                  className="flex min-h-12 cursor-pointer items-center gap-3 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(event) => updateFormField({ [field]: event.target.checked })}
                    className="h-4 w-4 rounded border-tremor-border text-tremor-brand"
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </FormSection>

          <FormSection title={lang === "zh" ? "备注" : "Notes"}>
            <textarea
              value={form.notes ?? ""}
              onChange={(event) => updateFormField({ notes: event.target.value })}
              className="min-h-24 w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            />
          </FormSection>

          <div className="flex flex-wrap justify-end gap-2 pt-2">
            <ActionButton onClick={resetForm} icon={<RotateCcw className="h-4 w-4" />}>
              {lang === "zh" ? "重置" : "Reset"}
            </ActionButton>
            <ActionButton
              type="submit"
              tone="primary"
              disabled={saving || !form.job_id.trim() || !form.name.trim()}
              icon={editingJobId ? <Pencil className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
            >
              {saving
                ? lang === "zh" ? "保存中" : "Saving"
                : editingJobId
                  ? lang === "zh" ? "保存修改" : "Save changes"
                  : lang === "zh" ? "创建 job" : "Create job"}
            </ActionButton>
          </div>
        </form>
      </DetailDrawer>

      <DetailDrawer
        open={Boolean(taskDetailUuid)}
        title={lang === "zh" ? "发布任务详情" : "Release Task Detail"}
        subtitle={taskDetailUuid ?? undefined}
        onClose={() => setTaskDetailUuid(null)}
      >
        <TaskDetailPanel
          taskDetail={taskDetail}
          detailLoading={taskDetailLoading}
          emptyMessage={lang === "zh" ? "无法加载任务详情" : "Failed to load task detail"}
          logDisplayMode="minimal"
        />
      </DetailDrawer>
    </div>
  );
}
