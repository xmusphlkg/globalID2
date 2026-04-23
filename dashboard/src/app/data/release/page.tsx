"use client";

import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Card, Grid, Metric, ProgressBar, Text, Title } from "@tremor/react";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Mail,
  ChevronDown,
  Clock3,
  Cloud,
  ExternalLink,
  GitBranch,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Wrench,
  X,
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
import { useSettings } from "@/lib/hooks/useSettings";
import {
  useCancelTask,
  useTaskDetail,
  useTasks,
  useTaskWebSocket,
  useWorkerStatus,
} from "@/lib/hooks/useTasks";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
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

function relativeTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.round(diffMs / 1000);
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

function statusColor(status: string) {
  switch (status) {
    case "completed": return "emerald" as const;
    case "running": case "queued": return "amber" as const;
    case "failed": return "rose" as const;
    case "skipped": case "cancelled": return "slate" as const;
    default: return "blue" as const;
  }
}

function RuntimeTile({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-tremor-border bg-tremor-background px-3 py-2 shadow-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background">
      <div className="flex items-center gap-1.5 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {icon}
        <span className="text-[10px] font-semibold uppercase tracking-[0.15em]">{label}</span>
      </div>
      <p className="mt-1.5 text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong truncate">{value}</p>
    </div>
  );
}

function AccessDetail({ label, value, mono = false }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="rounded-xl border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
      <p className="text-[10px] font-semibold uppercase tracking-[0.15em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{label}</p>
      <div className={`mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong ${mono ? "break-all font-mono" : "break-words"}`}>{value}</div>
    </div>
  );
}

function CheckOutput({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="rounded-xl border border-dashed border-tremor-border bg-tremor-background-muted/60 p-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/30">
      <p className="text-[10px] font-semibold uppercase tracking-[0.15em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{label}</p>
      <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] text-tremor-content dark:text-dark-tremor-content">{value}</pre>
    </div>
  );
}

// --- Modal for job create/edit ---
function JobModal({
  open, onClose, form, onChange, onSubmit, isSubmitting, isNew, selectedJob,
}: {
  open: boolean;
  onClose: () => void;
  form: DataReleaseJobInput;
  onChange: (patch: Partial<DataReleaseJobInput>) => void;
  onSubmit: () => Promise<void>;
  isSubmitting: boolean;
  isNew: boolean;
  selectedJob: DataReleaseJob | null;
}) {
  const overlayRef = useRef<HTMLDivElement>(null);

  const handleOverlayClick = useCallback((e: React.MouseEvent) => {
    if (e.target === overlayRef.current) onClose();
  }, [onClose]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    if (open) { document.addEventListener("keydown", handler); document.body.style.overflow = "hidden"; }
    return () => { document.removeEventListener("keydown", handler); document.body.style.overflow = ""; };
  }, [open, onClose]);

  if (!open) return null;

  const ToggleField = ({ field, label }: { field: BooleanReleaseField; label: string }) => (
    <label className="flex cursor-pointer items-center gap-2">
      <input type="checkbox" checked={form[field] as boolean} onChange={(e) => onChange({ [field]: e.target.checked })} className="h-4 w-4 rounded border-tremor-border text-teal-600 focus:ring-teal-500" />
      <span className="text-sm text-tremor-content dark:text-dark-tremor-content">{label}</span>
    </label>
  );

  return (
    <div ref={overlayRef} className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-12 backdrop-blur-sm" onClick={handleOverlayClick}>
      <div className="w-full max-w-3xl max-h-[80vh] overflow-y-auto rounded-2xl bg-white shadow-xl dark:bg-gray-900">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-tremor-border bg-white px-6 py-4 dark:border-dark-tremor-border dark:bg-gray-900">
          <Title className="!text-lg">{isNew ? "New Release Job" : "Edit Release Job"}</Title>
          <Button size="xs" variant="light" icon={X} onClick={onClose} />
        </div>

        <div className="space-y-6 p-6">
          {/* Basic info */}
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Job ID *</label>
              <input value={form.job_id} onChange={(e) => onChange({ job_id: e.target.value })} disabled={!isNew} placeholder="e.g. prod-main" className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong disabled:opacity-50" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Name *</label>
              <input value={form.name} onChange={(e) => onChange({ name: e.target.value })} placeholder="Display name" className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong" />
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Priority</label>
              <select value={form.priority} onChange={(e) => onChange({ priority: e.target.value })} className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                <option value="critical">Critical</option><option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Interval (min)</label>
              <input type="number" value={form.interval_minutes ?? ""} onChange={(e) => onChange({ interval_minutes: e.target.value ? Number(e.target.value) : null })} placeholder="Leave empty for manual/auto" className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Daily Time</label>
              <input value={form.daily_time ?? ""} onChange={(e) => onChange({ daily_time: e.target.value || null })} placeholder="e.g. 02:00" className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background" />
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Timezone</label>
              <input value={form.timezone ?? ""} onChange={(e) => onChange({ timezone: e.target.value })} placeholder="UTC" className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Schedule</label>
              <p className="text-xs text-tremor-content dark:text-dark-tremor-content py-2">{scheduleLabel(form)}</p>
            </div>
          </div>

          {/* GitHub */}
          <div className="space-y-3 rounded-xl border border-tremor-border p-4 dark:border-dark-tremor-border">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">GitHub</p>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Remote</label>
                <input value={form.github_remote} onChange={(e) => onChange({ github_remote: e.target.value })} className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Branch</label>
                <input value={form.github_branch ?? ""} onChange={(e) => onChange({ github_branch: e.target.value })} className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background" />
              </div>
            </div>
          </div>

          {/* Cloudflare */}
          <div className="space-y-3 rounded-xl border border-tremor-border p-4 dark:border-dark-tremor-border">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Cloudflare</p>
            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Project</label>
              <input value={form.cloudflare_project_name ?? ""} onChange={(e) => onChange({ cloudflare_project_name: e.target.value })} className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background" />
            </div>
          </div>

          {/* Commit message */}
          <div>
            <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Commit Message Template</label>
            <input value={form.commit_message_template} onChange={(e) => onChange({ commit_message_template: e.target.value })} className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm font-mono dark:border-dark-tremor-border dark:bg-dark-tremor-background" />
          </div>

          {/* Toggles */}
          <div className="grid gap-3 md:grid-cols-2">
            <label className="flex cursor-pointer items-center gap-2"><input type="checkbox" checked={form.enabled} onChange={(e) => onChange({ enabled: e.target.checked })} className="h-4 w-4 rounded border-tremor-border text-teal-600 focus:ring-teal-500" /><span className="text-sm text-tremor-content dark:text-dark-tremor-content">Enabled</span></label>
            <ToggleField field="auto_after_crawls" label="Auto after data tasks" />
            <ToggleField field="include_git_push" label="Include Git push" />
            <ToggleField field="include_cloudflare_deploy" label="Include Cloudflare deploy" />
            <ToggleField field="require_clean_worktree" label="Require clean worktree" />
          </div>

          {/* Notes */}
          <div>
            <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Notes</label>
            <textarea value={form.notes ?? ""} onChange={(e) => onChange({ notes: e.target.value })} rows={2} className="w-full rounded-lg border border-tremor-border bg-tremor-background px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background resize-none" />
          </div>
        </div>

        <div className="sticky bottom-0 flex items-center justify-end gap-2 border-t border-tremor-border bg-white px-6 py-4 dark:border-dark-tremor-border dark:bg-gray-900">
          <Button size="sm" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button size="sm" variant="primary" onClick={onSubmit} disabled={isSubmitting || !form.job_id.trim() || !form.name.trim()} loading={isSubmitting}>
            {isNew ? "Create" : "Save"}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ======================== MAIN PAGE ========================

export default function DataReleasePage() {
  const { lang } = useAppStore();
  const { data: config } = useDataReleaseConfig();
  const { data: jobs, isLoading } = useDataReleaseJobs();
  const { data: workerStatus } = useWorkerStatus();
  const { data: settings } = useSettings();
  const { data: releaseTasks, refetch: releaseTasksRefetch } = useTasks(undefined, "export_data", undefined, undefined, 20);
  const cancelTask = useCancelTask();

  // Selection state
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [expandedTaskUuid, setExpandedTaskUuid] = useState<string | null>(null);

  // Job modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  const [form, setForm] = useState<DataReleaseJobInput>(defaultForm);

  // Checks
  const { data: checks, refetch: refetchChecks, isFetching: checkingAccess, isLoading: loadingChecks } = useDataReleaseChecks(selectedJobId);
  const { data: taskDetail } = useTaskDetail(expandedTaskUuid);

  // Mutations
  const runJob = useRunDataReleaseJob();
  const createJob = useCreateDataReleaseJob();
  const updateJob = useUpdateDataReleaseJob();
  const deleteJob = useDeleteDataReleaseJob();

  useTaskWebSocket({ extraQueryKeys: [["data-release"], ["data-release-jobs"], ["data-release-checks"], ["tasks"], ["task"]] });

  useEffect(() => { if (!selectedJobId && jobs?.length) setSelectedJobId(jobs[0].job_id); }, [jobs, selectedJobId]);

  const selectedJob = useMemo(() => jobs?.find((j) => j.job_id === selectedJobId) ?? null, [jobs, selectedJobId]);

  const summary = useMemo(() => {
    const rows = jobs ?? [];
    return { total: rows.length, enabled: rows.filter((j) => j.enabled).length, auto: rows.filter((j) => j.auto_after_crawls).length };
  }, [jobs]);

  const accessStatus = !checks ? (loadingChecks || checkingAccess ? "Checking…" : "Pending") : checks.overall_ready ? "Ready" : "Blocked";
  const accessColor = !checks ? "slate" : checks.overall_ready ? "emerald" : "rose";
  const smtpReady = Boolean(settings?.smtp.alerting_ready);
  const smtpConfigured = Boolean(settings?.smtp.smtp_configured);
  const smtpBadgeColor = smtpReady ? "emerald" : smtpConfigured ? "amber" : "slate";

  const resetForm = () => {
    setEditingJobId(null);
    setForm({ ...defaultForm, timezone: config?.timezone || defaultForm.timezone, github_branch: selectedJob?.github_branch || defaultForm.github_branch, cloudflare_project_name: selectedJob?.cloudflare_project_name || defaultForm.cloudflare_project_name });
  };

  const openCreateModal = () => { setEditingJobId(null); setForm({ ...defaultForm, timezone: config?.timezone || "UTC" }); setModalOpen(true); };
  const openEditModal = (job: DataReleaseJob) => { setEditingJobId(job.job_id); setForm(toForm(job)); setModalOpen(true); };
  const closeModal = () => { setModalOpen(false); resetForm(); };

  const submitForm = async () => {
    const payload: DataReleaseJobInput = { ...form, job_id: form.job_id.trim(), name: form.name.trim(), priority: form.priority.trim().toLowerCase(), github_remote: form.github_remote.trim() || "origin", github_branch: form.github_branch?.trim() || null, cloudflare_project_name: form.cloudflare_project_name?.trim() || null, commit_message_template: form.commit_message_template.trim() || defaultForm.commit_message_template, daily_time: form.daily_time?.trim() || null, timezone: form.timezone?.trim() || config?.timezone || "UTC", notes: form.notes?.trim() || null, interval_minutes: form.interval_minutes ? Number(form.interval_minutes) : null };
    if (editingJobId) await updateJob.mutateAsync({ jobId: editingJobId, payload });
    else { await createJob.mutateAsync(payload); setSelectedJobId(payload.job_id); }
    closeModal();
  };

  const removeJob = async (job: DataReleaseJob) => {
    if (!window.confirm(`Delete release job "${job.name}"?`)) return;
    await deleteJob.mutateAsync(job.job_id);
    if (selectedJobId === job.job_id) setSelectedJobId(null);
    if (editingJobId === job.job_id) closeModal();
  };

  const runSelectedJob = async (jobId: string) => {
    const result = await runJob.mutateAsync(jobId);
    if (result.task_uuid) setExpandedTaskUuid(result.task_uuid);
  };

  const handleCancelTask = async (taskUuid: string) => {
    if (!window.confirm("Cancel this release task?")) return;
    await cancelTask.mutateAsync(taskUuid);
  };

  const updateFormField = (patch: Partial<DataReleaseJobInput>) => setForm((prev) => ({ ...prev, ...patch }));

  // Which tasks to show as collapsed

  return (
    <div className="mx-auto w-full max-w-7xl space-y-5 px-4 py-6 md:px-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Badge color="teal">{t(lang, "mod_database")}</Badge>
            <h1 className="text-2xl font-semibold tracking-tight text-tremor-content-strong dark:text-dark-tremor-content-strong">{t(lang, "data_release")}</h1>
          </div>
          <Text className="text-sm">{lang === "zh" ? "统一管理站点数据导出、Git 发布和 Cloudflare 部署的工作流。" : "Unified workflow for site data export, Git publishing, and Cloudflare deployment."}</Text>
          <Text className="text-xs text-tremor-content-subtle">
            {lang === "zh"
              ? "SMTP、GitHub 和 Cloudflare 的默认配置已统一迁移到设置中心。"
              : "SMTP, GitHub, and Cloudflare defaults now live in the Settings Center."}
          </Text>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/setting"
            className="inline-flex items-center gap-1 rounded-lg border border-tremor-border bg-tremor-background px-3 py-1.5 text-xs font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
          >
            {lang === "zh" ? "打开设置中心" : "Open Settings"}
            <ExternalLink className="h-3.5 w-3.5" />
          </Link>
          <Button size="xs" variant="secondary" icon={Plus} onClick={openCreateModal}>New Job</Button>
          <Button size="xs" variant="primary" icon={Play} disabled={!selectedJob} onClick={() => selectedJob && runSelectedJob(selectedJob.job_id)}>Run Selected</Button>
        </div>
      </div>

      {/* Summary cards */}
      <Grid numItemsSm={2} numItemsLg={4} className="gap-3">
        <Card decoration="top" decorationColor="blue"><Text>Scheduler</Text><Metric>{config?.enabled ? "On" : "Off"}</Metric></Card>
        <Card decoration="top" decorationColor="teal"><Text>Jobs</Text><Metric>{summary.enabled}/{summary.total}</Metric></Card>
        <Card decoration="top" decorationColor="amber"><Text>Auto Trigger</Text><Metric>{summary.auto}</Metric></Card>
        <Card decoration="top" decorationColor={accessColor}><Text>Access</Text><Metric>{accessStatus}</Metric></Card>
      </Grid>

      {/* Runtime & Access + Checks */}
      <Card>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="rounded-lg bg-tremor-background-muted p-1.5 text-tremor-content-strong dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-strong"><RefreshCw className="h-4 w-4" /></div>
            <Title className="!text-sm font-medium">Runtime & Access</Title>
            <Badge color={accessColor} size="xs">{accessStatus}</Badge>
            {selectedJob && <Badge color="slate" size="xs">{selectedJob.name}</Badge>}
          </div>
          <Button size="xs" variant="light" icon={RefreshCw} loading={checkingAccess} onClick={() => refetchChecks()} />
        </div>
        <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          <RuntimeTile icon={<Clock3 className="h-3.5 w-3.5" />} label="Last Tick" value={formatDateTime(config?.last_tick_at)} />
          <RuntimeTile icon={<Wrench className="h-3.5 w-3.5" />} label="Poll Interval" value={`${config?.poll_interval_seconds ?? "-"}s`} />
          <RuntimeTile icon={<ShieldCheck className="h-3.5 w-3.5" />} label="Worker" value={workerStatus?.worker_process_running ? "Running" : "Stopped"} />
          <RuntimeTile icon={<CheckCircle2 className="h-3.5 w-3.5" />} label="Job" value={selectedJob?.name || "None"} />
        </div>

        {checks?.blockers?.length ? (
          <div className="mx-4 mb-3 rounded-xl border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200">
            <div className="flex items-center gap-1.5 font-medium"><AlertTriangle className="h-3.5 w-3.5" />Blockers</div>
            <div className="mt-1 space-y-0.5">{checks.blockers.map((b) => <Text key={b} className="!text-xs">{b}</Text>)}</div>
          </div>
        ) : null}
      </Card>

      {/* Three-column: GitHub / Cloudflare / SMTP */}
      <div className="grid gap-3 lg:grid-cols-3">
        {/* GitHub */}
        <Card className="border-0 shadow-none">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="rounded-lg bg-tremor-background-muted p-1.5 text-tremor-content-strong dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-strong"><GitBranch className="h-4 w-4" /></div>
              <Title className="!text-sm font-medium">GitHub</Title>
              <Badge color={!checks ? "slate" : checks.git.read_access_ok && checks.git.write_access_ok ? "emerald" : "rose"} size="xs">{!checks ? "Pending" : checks.git.read_access_ok && checks.git.write_access_ok ? "OK" : "Failed"}</Badge>
            </div>
            <Button size="xs" variant="light" icon={RefreshCw} loading={checkingAccess} onClick={() => refetchChecks()} />
          </div>
          <div className="mt-4 space-y-2">
            {checks ? (
              <>
                <div className="grid gap-2 md:grid-cols-2">
                  <AccessDetail label="Branch" value={checks.git.branch || "-"} />
                  <AccessDetail label="Repo" value={checks.git.repo_url || "-"} mono />
                  <AccessDetail label="Release Changes" value={String(checks.git.dirty_release_paths.length)} />
                  <AccessDetail label="Blocking Changes" value={String(checks.git.dirty_blocking_paths.length)} />
                </div>
                <CheckOutput label="Read Check" value={checks.git.read_check_output} />
                <CheckOutput label="Write Check" value={checks.git.write_check_output} />
              </>
            ) : <Text className="!text-xs">Run a preflight check to load status.</Text>}
          </div>
        </Card>

        {/* Cloudflare */}
        <Card className="border-0 shadow-none">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="rounded-lg bg-tremor-background-muted p-1.5 text-tremor-content-strong dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-strong"><Cloud className="h-4 w-4" /></div>
              <Title className="!text-sm font-medium">Cloudflare</Title>
              <Badge color={!checks ? "slate" : checks.cloudflare.project_access_ok ? "emerald" : "rose"} size="xs">{!checks ? "Pending" : checks.cloudflare.project_access_ok ? "OK" : "Failed"}</Badge>
            </div>
            <Button size="xs" variant="light" icon={RefreshCw} loading={checkingAccess} onClick={() => refetchChecks()} />
          </div>
          <div className="mt-4 space-y-2">
            {checks ? (
              <>
                <div className="grid gap-2 md:grid-cols-2">
                  <AccessDetail label="Project" value={checks.cloudflare.project_name || "-"} />
                  <AccessDetail label="Wrangler" value={checks.commands.wrangler_version || "-"} mono />
                  <AccessDetail label="Token" value={checks.cloudflare.token_present ? "Yes" : "No"} />
                  <AccessDetail label="Account ID" value={checks.cloudflare.account_id_present ? "Yes" : "No"} />
                </div>
                {checks.cloudflare.error && (
                  <div className="rounded-xl border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200">
                    <div className="flex items-center gap-1.5 font-medium"><AlertTriangle className="h-3.5 w-3.5" />Error</div>
                    <Text className="mt-1 !text-xs break-words text-rose-700 dark:text-rose-300">{checks.cloudflare.error}</Text>
                  </div>
                )}
              </>
            ) : <Text className="!text-xs">Run a preflight check to load status.</Text>}
          </div>
        </Card>

        {/* SMTP */}
        <Card className="border-0 shadow-none">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="rounded-lg bg-tremor-background-muted p-1.5 text-tremor-content-strong dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-strong"><Mail className="h-4 w-4" /></div>
              <Title className="!text-sm font-medium">SMTP</Title>
              <Badge color={smtpBadgeColor} size="xs">
                {smtpReady ? "Ready" : smtpConfigured ? "Needs Recipients" : "Managed in Settings"}
              </Badge>
            </div>
            <Link
              href="/setting"
              className="inline-flex items-center gap-1 rounded-full border border-tremor-border px-3 py-1.5 text-xs font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
            >
              {lang === "zh" ? "去设置" : "Open Settings"}
              <ExternalLink className="h-3.5 w-3.5" />
            </Link>
          </div>
          <div className="mt-4 space-y-3">
            <Text className="!text-xs text-tremor-content-subtle">
              {lang === "zh"
                ? "数据发布只读取统一设置，不再在这里单独维护 SMTP。测试连接、发测试邮件和收件人管理都在设置中心完成。"
                : "Data release only reads the shared SMTP settings now. Connection tests, test emails, and recipient management all happen in the Settings Center."}
            </Text>
            <div className="grid gap-2 md:grid-cols-2">
              <AccessDetail label="Source" value={settings?.smtp.source || "env"} />
              <AccessDetail label="From" value={settings?.smtp.smtp_from_email || "-"} />
              <AccessDetail
                label="Recipients"
                value={settings?.smtp.admin_emails?.length ? settings.smtp.admin_emails.join(", ") : "-"}
              />
              <AccessDetail
                label="Password"
                value={settings?.smtp.smtp_password_present ? "Saved" : "Missing"}
              />
            </div>
          </div>
        </Card>
      </div>

      {/* Release Jobs */}
      <Card>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="rounded-lg bg-tremor-background-muted p-1.5 text-tremor-content-strong dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-strong"><Wrench className="h-4 w-4" /></div>
            <Title className="!text-sm font-medium">Release Jobs</Title>
          </div>
          <Button size="xs" variant="light" icon={Plus} onClick={openCreateModal} />
        </div>
        <div className="mt-4 space-y-2">
        {!jobs?.length ? (
          <Text className="!text-xs">No release jobs configured. Click "New Job" to create one.</Text>
        ) : (
          jobs.map((job) => (
            <JobRow key={job.job_id} job={job} isSelected={job.job_id === selectedJobId} onSelect={() => setSelectedJobId(job.job_id)} onRun={() => runSelectedJob(job.job_id)} onEdit={() => openEditModal(job)} onDelete={() => removeJob(job)} />
          ))
        )}
        </div>
      </Card>

      {/* Recent Release Tasks */}
      <Card>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="rounded-lg bg-tremor-background-muted p-1.5 text-tremor-content-strong dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-strong"><Clock3 className="h-4 w-4" /></div>
            <Title className="!text-sm font-medium">Recent Release Tasks</Title>
          </div>
          <Button size="xs" variant="light" icon={RefreshCw} onClick={() => releaseTasksRefetch()} />
        </div>

        <div className="mt-4 space-y-3">
          {!releaseTasks?.length ? (
            <div className="flex flex-col items-center justify-center rounded-tremor-default border border-dashed border-tremor-border p-10 text-center dark:border-dark-tremor-border">
              <Clock3 className="mb-3 h-10 w-10 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
              <Text>No release tasks yet.</Text>
            </div>
          ) : (
            releaseTasks.map((task) => {
              const expanded = expandedTaskUuid === task.task_uuid;
              const canCancel = ["pending", "queued", "running", "retrying"].includes(task.status) && !task.cancel_requested;
              return (
                <Card key={task.task_uuid} className="p-0">
                  <div className="flex items-center gap-3 px-4 py-3">
                    <button
                      className="min-w-0 flex-1 rounded-tremor-default text-left transition hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-subtle"
                      onClick={() => setExpandedTaskUuid(expanded ? null : task.task_uuid)}
                    >
                      <div className="flex min-w-0 items-center gap-3 rounded-tremor-default px-2 py-1.5">
                        <div className="flex shrink-0 flex-wrap items-center gap-2">
                          <Badge color={statusColor(task.status)}>{task.status}</Badge>
                          <Badge color="slate">Release</Badge>
                        </div>
                        <span className="min-w-0 flex-1 truncate text-sm font-medium leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                          {task.task_name || task.task_type}
                        </span>
                        <div className="hidden shrink-0 items-center gap-2 md:flex md:w-40">
                          <ProgressBar value={task.progress} color={task.progress === 100 ? "emerald" : "teal"} className="flex-1" />
                          <Text>{task.progress}%</Text>
                        </div>
                        <Text className="hidden shrink-0 md:block" title={formatDateTime(task.created_at)}>{relativeTime(task.created_at)}</Text>
                        <ChevronDown className={`h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform ${expanded ? "rotate-180" : ""}`} />
                      </div>
                    </button>
                    <div className="flex shrink-0 items-center">
                      <Button
                        size="xs"
                        color={canCancel ? "rose" : "slate"}
                        variant={canCancel ? "secondary" : "light"}
                        disabled={!canCancel || cancelTask.isPending}
                        icon={Ban}
                        className={canCancel ? "" : "opacity-55"}
                        onClick={() => handleCancelTask(task.task_uuid)}
                      >
                        {canCancel ? (task.cancel_requested ? "Cancelling" : "Cancel") : ""}
                      </Button>
                    </div>
                  </div>

                  {expanded && (
                    <div className="border-t border-tremor-border px-4 pb-4 pt-3 dark:border-dark-tremor-border">
                      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-tremor-content md:hidden">
                        <span>{task.progress}%</span>
                        <span>{formatDateTime(task.created_at)}</span>
                      </div>
                      <TaskDetailPanel taskDetail={taskDetail ?? undefined} detailLoading={false} emptyMessage="Failed to load task detail" logDisplayMode="minimal" />
                    </div>
                  )}
                </Card>
              );
            })
          )}
        </div>
      </Card>

      <JobModal open={modalOpen} onClose={closeModal} form={form} onChange={updateFormField} onSubmit={submitForm} isSubmitting={createJob.isPending || updateJob.isPending} isNew={!editingJobId} selectedJob={selectedJob} />
    </div>
  );
}

// --- Compact job row ---
function JobRow({ job, isSelected, onSelect, onRun, onEdit, onDelete }: {
  job: DataReleaseJob; isSelected: boolean; onSelect: () => void; onRun: () => void; onEdit: () => void; onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className={`${isSelected ? "bg-teal-50/50 dark:bg-teal-950/20" : ""}`}>
      <div className="flex items-center justify-between px-4 py-2.5">
        <div className="flex items-center gap-3">
          <button type="button" className={`h-3 w-3 rounded-full border-2 transition-colors ${isSelected ? "border-teal-500 bg-teal-500" : "border-tremor-border dark:border-dark-tremor-border"}`} onClick={onSelect} />
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{job.name}</span>
              <Badge color={job.enabled ? "emerald" : "slate"} size="xs">{job.enabled ? "On" : "Off"}</Badge>
              <Badge color={statusColor(job.priority)} size="xs">{job.priority}</Badge>
            </div>
            <Text className="!text-xs mt-0.5">{scheduleLabel(job)}</Text>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button size="xs" variant="light" icon={Play} onClick={onRun} title="Run now" />
          <Button size="xs" variant="light" icon={Pencil} onClick={onEdit} title="Edit" />
          <Button size="xs" variant="light" icon={Trash2} onClick={onDelete} title="Delete" />
          <button type="button" onClick={() => setExpanded(!expanded)} className="ml-1 p-1 text-tremor-content-subtle hover:text-tremor-content dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content">
            <ChevronDown className={`h-4 w-4 transition-transform ${expanded ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>
      {expanded && (
        <div className="border-t border-tremor-border bg-tremor-background-muted/40 px-4 py-3 text-xs text-tremor-content dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/30 dark:text-dark-tremor-content space-y-2">
          <div className="grid gap-x-4 gap-y-1 md:grid-cols-3">
            <span><span className="font-medium">Git:</span> {job.github_remote}/{job.github_branch || "-"}</span>
            <span><span className="font-medium">CF Project:</span> {job.cloudflare_project_name || "-"}</span>
            <span><span className="font-medium">Auto after tasks:</span> {job.auto_after_crawls ? "Yes" : "No"}</span>
            <span><span className="font-medium">Git push:</span> {job.include_git_push ? "Yes" : "No"}</span>
            <span><span className="font-medium">CF deploy:</span> {job.include_cloudflare_deploy ? "Yes" : "No"}</span>
            <span><span className="font-medium">Clean worktree:</span> {job.require_clean_worktree ? "Yes" : "No"}</span>
          </div>
          {job.notes && <p className="mt-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{job.notes}</p>}
          <p className="font-mono text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{job.commit_message_template}</p>
        </div>
      )}
    </div>
  );
}
