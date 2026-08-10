"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Bell,
  Bot,
  CalendarClock,
  CheckCircle2,
  Clock3,
  Cpu,
  Mail,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  Settings2,
  Sparkles,
  Trash2,
} from "lucide-react";

import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DetailDrawer } from "@/components/ui/DetailDrawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { getCountryDisplayName, type Country, useCountries } from "@/shared/config/countries";
import {
  type AutomationJob,
  type AutomationJobInput,
  type CountrySourceConfig,
  useAutomationConfig,
  useAutomationJobs,
  useCreateAutomationJob,
  useDeleteAutomationJob,
  useRunAutomationJob,
  useSourceConfigs,
  useUpdateAutomationJob,
} from "@/features/operations/automation/api";
import { useTaskWebSocket, useWorkerStatus } from "@/features/operations/tasks/api";
import { t } from "@/lib/i18n";
import { getConfiguredSourceOptions, getSourceDisplayLabel } from "@/lib/source-labels";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

type ScheduleMode = "daily" | "interval";
type IntervalUnit = "minutes" | "hours";
type JobFilter = "all" | "enabled" | "disabled" | "failed";

type AutomationPreset = Partial<AutomationJobInput> & {
  id: string;
  label: string;
  job_id: string;
  name: string;
  country_code: string;
  source: string;
  daily_time: string;
};

const defaultForm: AutomationJobInput = {
  job_id: "",
  name: "",
  country_code: "CN",
  source: "all",
  enabled: true,
  priority: "normal",
  process: true,
  save_raw: true,
  fill_missing: true,
  force: false,
  include_current_month: false,
  revision_window_months: 3,
  retry_threshold: 3,
  interval_minutes: null,
  daily_time: "08:00",
  timezone: "Asia/Shanghai",
  notes: "",
};

const crawlOptionLabels: Record<string, { en: string; zh: string }> = {
  process: { en: "Process data after crawl", zh: "抓取后自动处理数据" },
  save_raw: { en: "Save raw fetched data", zh: "保存 raw 原始抓取数据" },
  fill_missing: { en: "Backfill missing months", zh: "回填缺失月份" },
  force: { en: "Force re-fetch", zh: "强制重新抓取" },
  include_current_month: { en: "Include current month (provisional)", zh: "接入当前月（临时数据）" },
};

const DEFAULT_INTERVAL_MINUTES = 60;
const dailyPresetTimes = ["00:00", "08:00", "12:00", "18:00"] as const;
const intervalPresetMinutes = [15, 30, 60, 180, 360, 720] as const;
const inputClass =
  "h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted disabled:bg-tremor-background-subtle disabled:text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function intervalMinutesToFields(minutes?: number | null): { value: string; unit: IntervalUnit } {
  const safeMinutes = minutes && minutes > 0 ? minutes : DEFAULT_INTERVAL_MINUTES;
  if (safeMinutes % 60 === 0) {
    return { value: String(safeMinutes / 60), unit: "hours" };
  }
  return { value: String(safeMinutes), unit: "minutes" };
}

function intervalFieldsToMinutes(value: string, unit: IntervalUnit): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return unit === "hours" ? parsed * 60 : parsed;
}

function describeIntervalMinutes(minutes: number, lang: "en" | "zh"): string {
  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return lang === "zh" ? `每 ${hours} 小时运行一次` : `Every ${hours} hour(s)`;
  }
  return lang === "zh" ? `每 ${minutes} 分钟运行一次` : `Every ${minutes} minute(s)`;
}

function scheduleLabel(
  job: { interval_minutes?: number | null; daily_time?: string | null; timezone?: string | null },
  lang: "en" | "zh",
): string {
  if (job.interval_minutes) return describeIntervalMinutes(job.interval_minutes, lang);
  if (job.daily_time) {
    return lang === "zh"
      ? `每天 ${job.daily_time} (${job.timezone || "UTC"})`
      : `Daily at ${job.daily_time} (${job.timezone || "UTC"})`;
  }
  return lang === "zh" ? "手动运行" : "Manual";
}

function toForm(job: AutomationJob): AutomationJobInput {
  return {
    job_id: job.job_id,
    name: job.name,
    country_code: job.country_code,
    source: job.source,
    enabled: job.enabled,
    priority: job.priority,
    process: job.process,
    save_raw: job.save_raw,
    fill_missing: job.fill_missing,
    force: job.force,
    include_current_month: job.include_current_month ?? false,
    revision_window_months: job.revision_window_months ?? 3,
    retry_threshold: job.retry_threshold,
    interval_minutes: job.interval_minutes ?? null,
    daily_time: job.daily_time ?? "",
    timezone: job.timezone ?? "",
    notes: job.notes ?? "",
  };
}

function findCountryTimezone(countries: Country[] | undefined, code: string): string | null {
  return countries?.find((country) => country.code === code)?.timezone ?? null;
}

function defaultFillMissingForCountry(config?: CountrySourceConfig | null): boolean {
  return config?.default_fill_missing ?? true;
}

function defaultIncludeCurrentMonthForCountry(config?: CountrySourceConfig | null): boolean {
  return config?.source_policy?.default_include_current_month ?? false;
}

function defaultRevisionWindowForCountry(config?: CountrySourceConfig | null): number {
  return Math.max(1, config?.source_policy?.default_revision_window ?? config?.source_policy?.default_revision_window_months ?? 3);
}

function presetTimeForIndex(index: number): string {
  const totalMinutes = 8 * 60 + index * 15;
  const hour = Math.floor(totalMinutes / 60) % 24;
  const minute = totalMinutes % 60;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
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
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void;
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

function InlineStatus({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
}) {
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

function AlertPanel({ tone, children }: { tone: "warning" | "danger" | "info"; children: ReactNode }) {
  const toneClass =
    tone === "danger"
      ? "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/50 dark:bg-rose-950/25 dark:text-rose-200"
      : tone === "warning"
        ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/25 dark:text-amber-200"
        : "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900/50 dark:bg-sky-950/25 dark:text-sky-200";

  return (
    <div className={cn("rounded-tremor-default border px-4 py-3 text-sm", toneClass)}>
      {children}
    </div>
  );
}

function JobHealthBadge({ job }: { job: AutomationJob }) {
  if (!job.enabled) return <StatusBadge status="disabled">disabled</StatusBadge>;
  if (job.last_status === "failed") return <StatusBadge status="failed">failed</StatusBadge>;
  return <StatusBadge status="enabled">enabled</StatusBadge>;
}

export default function SourcesAutomationPage() {
  const { lang } = useAppStore();
  const { data: config, isLoading } = useAutomationConfig();
  const { data: jobs } = useAutomationJobs();
  const { data: countries } = useCountries();
  const { data: sourceConfigs } = useSourceConfigs(lang);
  const { data: workerStatus } = useWorkerStatus();
  const runJob = useRunAutomationJob();
  const createJob = useCreateAutomationJob();
  const updateJob = useUpdateAutomationJob();
  const deleteJob = useDeleteAutomationJob();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  const [form, setForm] = useState<AutomationJobInput>(defaultForm);
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>("daily");
  const [intervalValue, setIntervalValue] = useState("1");
  const [intervalUnit, setIntervalUnit] = useState<IntervalUnit>("hours");
  const [jobSearch, setJobSearch] = useState("");
  const [jobFilter, setJobFilter] = useState<JobFilter>("all");

  useTaskWebSocket({ extraQueryKeys: [["sources-automation"], ["sources-automation-jobs"], ["sources-flow"]] });

  const schedulerEnabled = Boolean(config?.enabled);
  const workerRunning = Boolean(workerStatus?.worker_process_running);
  const saving = createJob.isPending || updateJob.isPending;

  const sourceConfigByCountry = useMemo(() => {
    const map = new Map<string, CountrySourceConfig>();
    for (const item of sourceConfigs ?? []) {
      map.set(item.country_code.toUpperCase(), item);
    }
    return map;
  }, [sourceConfigs]);

  const selectedSourceConfig = sourceConfigByCountry.get(form.country_code.trim().toUpperCase()) ?? null;
  const selectedSourceOption = selectedSourceConfig?.source_options.find(
    (option) => option.value === form.source,
  );
  const selectedSupportsCrawl = Boolean(selectedSourceConfig?.supports_crawl);
  const selectedSourcePolicy = selectedSourceOption?.source_policy ?? selectedSourceConfig?.source_policy ?? null;
  const selectedSupportsFillMissing = selectedSourceOption?.supports_fill_missing
    ?? selectedSourceConfig?.supports_fill_missing
    ?? true;
  const selectedSupportsCurrentMonth = Boolean(selectedSourcePolicy?.supports_current_month);
  const selectedUsesDynamicRevisions = Boolean(selectedSourcePolicy?.dynamic_revision_enabled);
  const selectedRevisionWindowUnit = selectedSourcePolicy?.revision_window_unit ?? "months";
  const selectedRevisionWindowSuffix = selectedRevisionWindowUnit === "weeks" ? "w" : selectedRevisionWindowUnit === "years" ? "y" : "m";

  const sources = useMemo(
    () => getConfiguredSourceOptions(selectedSourceConfig, lang, form.country_code),
    [form.country_code, lang, selectedSourceConfig],
  );

  const automationPresets = useMemo<AutomationPreset[]>(() => {
    const basePresets = (sourceConfigs ?? [])
      .filter((item) => item.supports_crawl)
      .map((item, index) => ({
        id: item.country_code.toLowerCase(),
        label: item.country_code,
        job_id: `${item.country_code.toLowerCase()}-daily`,
        name: `${item.country_code} Daily Crawl`,
        country_code: item.country_code,
        source: item.default_source,
        daily_time: presetTimeForIndex(index),
        timezone: item.timezone,
        fill_missing: item.default_fill_missing,
        include_current_month: defaultIncludeCurrentMonthForCountry(item),
        revision_window_months: defaultRevisionWindowForCountry(item),
      }));

    const historyPresets = (sourceConfigs ?? [])
      .filter((item) => item.supports_start_year && item.country_code !== "IS")
      .flatMap((item) => {
        const configuredHistory = item.source_options.filter((option) => option.source_kind === "history");
        const targets = configuredHistory.length > 0 ? configuredHistory : [undefined];
        return targets.map((historySource) => {
          const historyPolicy = historySource?.source_policy ?? item.source_policy;
          const source = historySource?.value ?? item.default_source;
          const sourceSuffix = configuredHistory.length > 1 ? `-${source.replaceAll("_", "-")}` : "";
          return {
            id: `${item.country_code.toLowerCase()}-history${sourceSuffix}`,
            label: `${item.country_code} hist${configuredHistory.length > 1 ? ` ${source}` : ""}`,
            job_id: `${item.country_code.toLowerCase()}-history${sourceSuffix}-backfill`,
            name: `${item.country_code} Historical Backfill${configuredHistory.length > 1 ? ` — ${source}` : ""}`,
            country_code: item.country_code,
            source,
            daily_time: "02:00",
            timezone: item.timezone,
            enabled: false,
            priority: "high",
            fill_missing: historySource?.default_fill_missing ?? true,
            force: true,
            include_current_month: defaultIncludeCurrentMonthForCountry(item),
            revision_window_months: Math.max(1, historyPolicy?.default_revision_window ?? historyPolicy?.default_revision_window_months ?? 3),
          };
        });
      });

    return [...basePresets, ...historyPresets];
  }, [sourceConfigs]);

  const summary = useMemo(() => {
    const list = jobs ?? [];
    return {
      total: list.length,
      enabled: list.filter((job) => job.enabled).length,
      disabled: list.filter((job) => !job.enabled).length,
      failed: list.filter((job) => job.last_status === "failed").length,
      runCount: list.reduce((total, job) => total + job.run_count, 0),
      alerting: config?.email_enabled ? config.admin_emails.length : 0,
    };
  }, [config, jobs]);

  const deliveryStatus = useMemo(() => {
    if (!config) return { tone: "neutral" as const, label: "Loading" };
    if (!config.admin_emails.length) return { tone: "warning" as const, label: "Missing admin emails" };
    if (!config.email_enabled) return { tone: "warning" as const, label: "Email not configured" };
    return { tone: "success" as const, label: "Failure alerts ready" };
  }, [config]);

  const filteredJobs = useMemo(() => {
    const search = jobSearch.trim().toLowerCase();
    return (jobs ?? []).filter((job) => {
      const matchesFilter =
        jobFilter === "all" ||
        (jobFilter === "enabled" && job.enabled) ||
        (jobFilter === "disabled" && !job.enabled) ||
        (jobFilter === "failed" && job.last_status === "failed");

      if (!matchesFilter) return false;
      if (!search) return true;

      return [
        job.job_id,
        job.name,
        job.country_code,
        job.source,
        job.priority,
        job.last_status,
        job.last_task_uuid ?? "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(search);
    });
  }, [jobFilter, jobSearch, jobs]);

  const schedulePreview = useMemo(() => {
    if (scheduleMode === "interval") {
      const minutes = intervalFieldsToMinutes(intervalValue, intervalUnit);
      if (!minutes) return lang === "zh" ? "请设置有效的间隔时间" : "Set a valid interval";
      return lang === "zh"
        ? `${describeIntervalMinutes(minutes, lang)}，时区 ${form.timezone || config?.timezone || "UTC"}`
        : `${describeIntervalMinutes(minutes, lang)} in ${form.timezone || config?.timezone || "UTC"}`;
    }

    const timeText = (form.daily_time || "").trim();
    if (!timeText) return lang === "zh" ? "请设置每日执行时间" : "Set a daily run time";
    return lang === "zh"
      ? `每天 ${timeText} 运行，时区 ${form.timezone || config?.timezone || "UTC"}`
      : `Runs daily at ${timeText} in ${form.timezone || config?.timezone || "UTC"}`;
  }, [config?.timezone, form.daily_time, form.timezone, intervalUnit, intervalValue, lang, scheduleMode]);

  const syncScheduleControls = (payload: { interval_minutes?: number | null; daily_time?: string | null }) => {
    if (payload.interval_minutes) {
      const derived = intervalMinutesToFields(payload.interval_minutes);
      setScheduleMode("interval");
      setIntervalValue(derived.value);
      setIntervalUnit(derived.unit);
      return;
    }
    const derived = intervalMinutesToFields(DEFAULT_INTERVAL_MINUTES);
    setScheduleMode("daily");
    setIntervalValue(derived.value);
    setIntervalUnit(derived.unit);
  };

  const resetForm = () => {
    setEditingJobId(null);
    const nextForm = {
      ...defaultForm,
      timezone: config?.timezone || defaultForm.timezone,
    };
    setForm(nextForm);
    syncScheduleControls(nextForm);
  };

  const openNewJob = () => {
    resetForm();
    setDrawerOpen(true);
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    resetForm();
  };

  const applyPreset = (presetId: string) => {
    const preset = automationPresets.find((item) => item.id === presetId);
    if (!preset) return;
    const presetConfig = sourceConfigByCountry.get(preset.country_code.toUpperCase());
    const timezone =
      findCountryTimezone(countries, preset.country_code) ||
      presetConfig?.timezone ||
      config?.timezone ||
      defaultForm.timezone;
    const nextForm = {
      ...defaultForm,
      ...preset,
      timezone,
      notes: `${preset.name} preset`,
    };
    setEditingJobId(null);
    setForm(nextForm);
    syncScheduleControls(nextForm);
    setDrawerOpen(true);
  };

  const startEdit = (job: AutomationJob) => {
    setEditingJobId(job.job_id);
    const nextForm = toForm(job);
    setForm(nextForm);
    syncScheduleControls(nextForm);
    setDrawerOpen(true);
  };

  const submitForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedSupportsCrawl) {
      window.alert(
        lang === "zh"
          ? "当前国家还没有配置自动采集来源，暂时不能创建自动化任务。"
          : "This country does not have configured crawl sources yet.",
      );
      return;
    }

    const nextIntervalMinutes = scheduleMode === "interval"
      ? intervalFieldsToMinutes(intervalValue, intervalUnit)
      : null;
    const nextDailyTime = scheduleMode === "daily" ? form.daily_time?.trim() || null : null;

    if (scheduleMode === "interval" && !nextIntervalMinutes) {
      window.alert(lang === "zh" ? "请填写有效的执行间隔" : "Please enter a valid interval");
      return;
    }
    if (scheduleMode === "daily" && !nextDailyTime) {
      window.alert(lang === "zh" ? "请填写每日执行时间" : "Please enter a daily run time");
      return;
    }

    const payload: AutomationJobInput = {
      ...form,
      job_id: form.job_id.trim(),
      name: form.name.trim(),
      country_code: form.country_code.trim().toUpperCase(),
      source: form.source.trim().toLowerCase(),
      priority: form.priority.trim().toLowerCase(),
      daily_time: nextDailyTime,
      timezone: form.timezone?.trim() || config?.timezone || "UTC",
      notes: form.notes?.trim() || null,
      interval_minutes: nextIntervalMinutes,
    };

    if (editingJobId) {
      await updateJob.mutateAsync({ jobId: editingJobId, payload });
    } else {
      await createJob.mutateAsync(payload);
    }

    setDrawerOpen(false);
    resetForm();
  };

  const removeJob = async (job: AutomationJob) => {
    const ok = window.confirm(
      lang === "zh" ? `确认删除自动化任务 ${job.name} 吗？` : `Delete automation job ${job.name}?`,
    );
    if (!ok) return;
    await deleteJob.mutateAsync(job.job_id);
    if (editingJobId === job.job_id) {
      setDrawerOpen(false);
      resetForm();
    }
  };

  useEffect(() => {
    if (!sources.some((option) => option.value === form.source) && sources[0]) {
      setForm((prev) => ({ ...prev, source: sources[0].value }));
    }
  }, [form.source, sources]);

  const columns: DataTableColumn<AutomationJob>[] = [
    {
      key: "status",
      header: lang === "zh" ? "状态" : "Status",
      render: (job) => <JobHealthBadge job={job} />,
    },
    {
      key: "job",
      header: lang === "zh" ? "任务" : "Job",
      render: (job) => (
        <div className="min-w-[240px] max-w-[420px]">
          <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {job.name}
          </p>
          <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {job.job_id}
          </p>
        </div>
      ),
    },
    {
      key: "source",
      header: lang === "zh" ? "国家/来源" : "Country / Source",
      render: (job) => (
        <div className="min-w-[150px]">
          <p className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{job.country_code}</p>
          <p className="mt-1 truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {getSourceDisplayLabel(job.source, lang, job.country_code)}
          </p>
          {sourceConfigByCountry
            .get(job.country_code.toUpperCase())
            ?.source_options.find((option) => option.value === job.source)
            ?.source_kind === "history" ? (
              <p className="mt-1 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {job.source === "is_doh_legacy_icd"
                  ? (lang === "zh" ? "已审核历史工作簿 · 登记诊断量（非病例通知）" : "Reviewed historical workbooks · registered diagnoses (not case notifications)")
                  : (lang === "zh" ? "已审核历史工作簿全量检查" : "Full reviewed historical-workbook check")}
              </p>
            ) : (
              (() => {
                const jobConfig = sourceConfigByCountry.get(job.country_code.toUpperCase());
                const policy = jobConfig?.source_options.find((option) => option.value === job.source)?.source_policy ?? jobConfig?.source_policy;
                if (!policy?.supports_current_month && !policy?.dynamic_revision_enabled) return null;
                return (
                  <p className="mt-1 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {job.include_current_month
                      ? (lang === "zh" ? "含当前月（临时）" : "Current month included")
                      : (lang === "zh" ? "仅已闭合月份" : "Closed months only")}
                    {policy.dynamic_revision_enabled
                      ? ` · ${lang === "zh" ? "修订窗口" : "revision window"} ${job.revision_window_months ?? policy.default_revision_window ?? policy.default_revision_window_months}${policy.revision_window_unit === "weeks" ? "w" : policy.revision_window_unit === "years" ? "y" : "m"}`
                      : ""}
                  </p>
                );
              })()
            )}
        </div>
      ),
    },
    {
      key: "schedule",
      header: lang === "zh" ? "计划" : "Schedule",
      render: (job) => (
        <div className="min-w-[180px]">
          <p className="text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {scheduleLabel(job, lang)}
          </p>
          <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {schedulerEnabled ? formatDateTime(job.next_run_at) : (lang === "zh" ? "调度器已关闭" : "Scheduler off")}
          </p>
        </div>
      ),
    },
    {
      key: "last",
      header: lang === "zh" ? "最近运行" : "Last Run",
      render: (job) => (
        <div className="min-w-[130px]">
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
            onClick={(event) => {
              event.stopPropagation();
              runJob.mutate(job.job_id);
            }}
            icon={<Play className="h-4 w-4" />}
          >
            {lang === "zh" ? "运行" : "Run"}
          </ActionButton>
          <ActionButton
            onClick={(event) => {
              event.stopPropagation();
              startEdit(job);
            }}
            icon={<Pencil className="h-4 w-4" />}
          >
            {lang === "zh" ? "编辑" : "Edit"}
          </ActionButton>
          <ActionButton
            tone="danger"
            disabled={deleteJob.isPending}
            onClick={(event) => {
              event.stopPropagation();
              void removeJob(job);
            }}
            icon={<Trash2 className="h-4 w-4" />}
          >
            {lang === "zh" ? "删除" : "Delete"}
          </ActionButton>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_sources")}
        title={t(lang, "automation")}
        description={
          lang === "zh"
            ? "管理自动采集 job、调度状态、失败通知和最近执行结果。"
            : "Manage automated collection jobs, scheduler state, failure alerts, and recent execution outcomes."
        }
        meta={
          <>
            <StatusBadge status={schedulerEnabled ? "enabled" : "disabled"}>
              {schedulerEnabled ? (lang === "zh" ? "调度器开启" : "Scheduler on") : (lang === "zh" ? "调度器关闭" : "Scheduler off")}
            </StatusBadge>
            <StatusBadge status={workerRunning ? "enabled" : "stopped"}>
              {workerRunning ? (lang === "zh" ? "Worker 运行中" : "Worker running") : (lang === "zh" ? "Worker 停止" : "Worker stopped")}
            </StatusBadge>
            <StatusBadge tone={deliveryStatus.tone}>{deliveryStatus.label}</StatusBadge>
          </>
        }
        actions={
          <>
            <Link
              href="/sources/tasks?scope=all"
              className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              {lang === "zh" ? "采集任务" : "Crawl Tasks"}
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              href="/sources/flow"
              className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              {lang === "zh" ? "数据流程" : "Data Flow"}
              <ArrowRight className="h-4 w-4" />
            </Link>
            <ActionButton tone="primary" onClick={openNewJob} icon={<Plus className="h-4 w-4" />}>
              {lang === "zh" ? "新建 job" : "New job"}
            </ActionButton>
          </>
        }
      />

      {!isLoading && !schedulerEnabled ? (
        <AlertPanel tone="danger">
          <div className="flex gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="font-medium">
                {lang === "zh"
                  ? "自动化总开关当前关闭，已保存的 job 不会自动创建爬取任务。"
                  : "The scheduler is disabled, so saved jobs will not create crawl tasks automatically."}
              </p>
              <p className="mt-1">
                {lang === "zh"
                  ? "请在 `.env` 中将 `AUTOMATION__ENABLED` 设为 `true`，然后重启 API 服务。"
                  : "Set `AUTOMATION__ENABLED=true` in `.env`, then restart the API service."}
              </p>
            </div>
          </div>
        </AlertPanel>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={lang === "zh" ? "调度器" : "Scheduler"}
          value={config?.enabled ? "On" : "Off"}
          icon={<CalendarClock className="h-4 w-4" />}
          tone={schedulerEnabled ? "success" : "danger"}
          hint={lang === "zh" ? `轮询间隔 ${config?.poll_interval_seconds ?? "-"}s` : `Poll interval ${config?.poll_interval_seconds ?? "-"}s`}
        />
        <MetricTile
          label={lang === "zh" ? "启用 job" : "Enabled Jobs"}
          value={`${summary.enabled}/${summary.total}`}
          icon={<Bot className="h-4 w-4" />}
          tone="primary"
          hint={lang === "zh" ? `${summary.disabled} 个停用` : `${summary.disabled} disabled`}
        />
        <MetricTile
          label={lang === "zh" ? "最近失败" : "Recent Failures"}
          value={summary.failed}
          icon={<AlertTriangle className="h-4 w-4" />}
          tone={summary.failed > 0 ? "danger" : "success"}
          hint={lang === "zh" ? "按最近状态统计" : "Based on last status"}
        />
        <MetricTile
          label={lang === "zh" ? "提醒收件人" : "Alert Recipients"}
          value={summary.alerting}
          icon={<Bell className="h-4 w-4" />}
          tone={summary.alerting > 0 ? "success" : "warning"}
          hint={lang === "zh" ? `累计运行 ${summary.runCount} 次` : `${summary.runCount} total runs`}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="space-y-5">
          <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <div className="mb-1 flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {lang === "zh" ? "运行状态" : "Runtime"}
              </h2>
              <Settings2 className="h-4 w-4 text-tremor-content-subtle" />
            </div>
            <InlineStatus
              icon={<Clock3 className="h-4 w-4" />}
              label={lang === "zh" ? "最近 tick" : "Last tick"}
              value={formatDateTime(config?.last_tick_at)}
            />
            <InlineStatus
              icon={<Cpu className="h-4 w-4" />}
              label="Worker"
              value={workerStatus ? (workerRunning ? "running" : "stopped") : "checking"}
            />
            <InlineStatus
              icon={<Mail className="h-4 w-4" />}
              label={lang === "zh" ? "邮件发送" : "Email delivery"}
              value={config?.email_enabled ? "configured" : "disabled"}
            />
          </section>

          {!workerRunning ? (
            <AlertPanel tone="warning">
              {lang === "zh"
                ? "自动化只会先创建 queued 任务；如果 worker 没启动，任务不会继续执行。"
                : "Automation can create queued tasks, but they will not continue without a running worker."}
            </AlertPanel>
          ) : null}

          <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {lang === "zh" ? "通知" : "Notifications"}
            </h2>
            <div className="mt-3 space-y-3">
              <StatusBadge tone={deliveryStatus.tone}>{deliveryStatus.label}</StatusBadge>
              {!config?.email_enabled ? (
                <AlertPanel tone="warning">
                  {lang === "zh"
                    ? "SMTP 尚未准备完成，请到设置中心补齐主机、账号、密码和发件邮箱。"
                    : "SMTP is not ready. Open Settings to finish the host, username, password, and from-email setup."}
                </AlertPanel>
              ) : null}
              {!config?.admin_emails.length ? (
                <AlertPanel tone="warning">
                  {lang === "zh"
                    ? "请在设置中心补充管理员邮箱，自动化失败提醒才会送达。"
                    : "Add admin email recipients in Settings so automation failure alerts have somewhere to go."}
                </AlertPanel>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {(config?.admin_emails ?? []).length > 0 ? (
                  config?.admin_emails.map((email) => (
                    <StatusBadge key={email} tone="neutral">{email}</StatusBadge>
                  ))
                ) : (
                  <p className="text-sm text-tremor-content dark:text-dark-tremor-content">
                    {lang === "zh" ? "尚未配置管理员邮箱" : "No admin emails configured"}
                  </p>
                )}
              </div>
              <Link
                href="/setting"
                className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
              >
                {lang === "zh" ? "打开设置中心" : "Open Settings"}
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </section>
        </aside>

        <section className="space-y-3">
          <FilterToolbar>
            <input
              type="search"
              value={jobSearch}
              onChange={(event) => setJobSearch(event.target.value)}
              placeholder={lang === "zh" ? "搜索 job、国家、来源或最近任务" : "Search jobs, countries, sources, or last task"}
              className={cn(inputClass, "min-w-[240px] flex-1")}
            />
            <select
              value={jobFilter}
              onChange={(event) => setJobFilter(event.target.value as JobFilter)}
              className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
              aria-label={lang === "zh" ? "筛选 job" : "Filter jobs"}
            >
              <option value="all">{lang === "zh" ? "全部 job" : "All jobs"}</option>
              <option value="enabled">{lang === "zh" ? "已启用" : "Enabled"}</option>
              <option value="disabled">{lang === "zh" ? "已停用" : "Disabled"}</option>
              <option value="failed">{lang === "zh" ? "最近失败" : "Failed last run"}</option>
            </select>
            <ActionButton onClick={openNewJob} icon={<Plus className="h-4 w-4" />} tone="primary">
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
              columns={columns}
              rows={filteredJobs}
              getRowKey={(job) => job.job_id}
              selectedRowKey={drawerOpen ? editingJobId : null}
              onRowClick={startEdit}
              emptyState={
                <EmptyState
                  icon={<Bot className="h-10 w-10" />}
                  title={lang === "zh" ? "暂无自动化 job" : "No automation jobs"}
                  description={
                    jobSearch || jobFilter !== "all"
                      ? lang === "zh"
                        ? "当前筛选条件下没有匹配的 job。"
                        : "No jobs match the current filters."
                      : lang === "zh"
                        ? "创建一个 job 后，调度器会按计划生成采集任务。"
                        : "Create a job to let the scheduler generate crawl tasks on a schedule."
                  }
                />
              }
            />
          )}
        </section>
      </div>

      <DetailDrawer
        open={drawerOpen}
        title={editingJobId ? (lang === "zh" ? "编辑自动化 job" : "Edit Automation Job") : (lang === "zh" ? "新建自动化 job" : "New Automation Job")}
        subtitle={editingJobId ?? (lang === "zh" ? "配置国家、来源、调度和执行选项" : "Configure country, source, schedule, and execution options")}
        onClose={closeDrawer}
      >
        <form className="space-y-5" onSubmit={submitForm}>
          <FormSection title={lang === "zh" ? "基础信息" : "Basic Info"}>
            {!editingJobId ? (
              <div>
                <p className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {lang === "zh" ? "快速预设" : "Quick presets"}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {automationPresets.map((preset) => (
                    <button
                      key={preset.id}
                      type="button"
                      onClick={() => applyPreset(preset.id)}
                      className="inline-flex h-8 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-2.5 text-xs font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
                    >
                      <Sparkles className="h-3.5 w-3.5" />
                      {preset.label}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Job ID">
                <input
                  className={inputClass}
                  value={form.job_id}
                  disabled={Boolean(editingJobId)}
                  required
                  onChange={(event) => setForm((prev) => ({ ...prev, job_id: event.target.value }))}
                />
              </Field>
              <Field label={lang === "zh" ? "名称" : "Name"}>
                <input
                  className={inputClass}
                  value={form.name}
                  required
                  onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                />
              </Field>
              <Field label={lang === "zh" ? "国家" : "Country"}>
                <select
                  className={inputClass}
                  value={form.country_code}
                  onChange={(event) => {
                    const nextCode = event.target.value;
                    const nextConfig = sourceConfigByCountry.get(nextCode.toUpperCase());
                    setForm((prev) => ({
                      ...prev,
                      country_code: nextCode,
                      source: nextConfig?.default_source || prev.source,
                      fill_missing: defaultFillMissingForCountry(nextConfig),
                      include_current_month: defaultIncludeCurrentMonthForCountry(nextConfig),
                      revision_window_months: defaultRevisionWindowForCountry(nextConfig),
                      timezone: findCountryTimezone(countries, nextCode) || nextConfig?.timezone || prev.timezone,
                    }));
                  }}
                >
                  {(countries ?? []).map((country) => (
                    <option key={country.code} value={country.code}>
                      {getCountryDisplayName(country, lang)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={lang === "zh" ? "来源" : "Source"}>
                <select
                  className={inputClass}
                  value={form.source}
                  disabled={!selectedSupportsCrawl}
                  onChange={(event) => {
                    const nextSource = event.target.value;
                    const nextOption = selectedSourceConfig?.source_options.find((option) => option.value === nextSource);
                    const nextPolicy = nextOption?.source_policy ?? selectedSourceConfig?.source_policy;
                    setForm((prev) => ({
                      ...prev,
                      source: nextSource,
                      fill_missing: nextOption?.default_fill_missing ?? selectedSourceConfig?.default_fill_missing ?? prev.fill_missing,
                      include_current_month: nextPolicy?.default_include_current_month ?? false,
                      revision_window_months: Math.max(1, nextPolicy?.default_revision_window ?? nextPolicy?.default_revision_window_months ?? 3),
                    }));
                  }}
                >
                  {sources.map((source) => (
                    <option key={source.value} value={source.value}>
                      {source.label}
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            {selectedSourceOption?.source_kind === "history" ? (
              <AlertPanel tone="warning">
                {form.source === "hpsc_annual"
                  ? (lang === "zh" ? "该任务回补 HPSC 2004–2020 年度历史；与 2021 年起周度源分开保存，NA 不补零。" : "This job backfills HPSC annual history for 2004–2020, stored separately from the weekly source beginning in 2021; NA is not zero-filled.")
                  : form.source === "hpsc_weekly_archive"
                  ? (lang === "zh" ? "该任务从 Lenus 与网页档案重建 HPSC 历史周报快照；只取当周列，目录缺周保持未知且不补零。许可不阻断内部接入，但该来源禁止公开发布。" : "This job rebuilds historical HPSC weekly snapshots from Lenus and web archives; it uses only the current-week column and leaves uncatalogued weeks unknown. Licence checks do not block internal ingestion, but public release is disabled.")
                  : form.source === "is_doh_legacy_icd"
                  ? (lang === "zh" ? "该任务读取历史 ICD 登记诊断量，仅用于来源事实与溯源，不作为病例通知曲线。" : "This job reads legacy ICD registered diagnoses for source facts and provenance, not as a case-notification curve.")
                  : (lang === "zh" ? "该任务检查已审核历史工作簿全目录；不使用 start_year 或合成缺失月份。" : "This job checks the full reviewed historical-workbook catalogue; it does not use start_year or synthesize missing months.")}
              </AlertPanel>
            ) : null}

            {!selectedSupportsCrawl ? (
              <AlertPanel tone="warning">
                {lang === "zh"
                  ? "这个国家还没有在后端 registry 中声明可采集来源。"
                  : "This country has no crawl source declared in the backend registry yet."}
              </AlertPanel>
            ) : null}

            {selectedSourcePolicy ? (
              <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
                <p className="text-xs font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {lang === "zh" ? "来源策略" : "Source policy"}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <StatusBadge tone={selectedSupportsCurrentMonth ? "info" : "neutral"}>
                    {selectedSupportsCurrentMonth
                      ? `${lang === "zh" ? "当前月可接入" : "Current month supported"} · ${selectedSourcePolicy.current_month_status}`
                      : (lang === "zh" ? "仅闭合月份" : "Closed months only")}
                  </StatusBadge>
                  <StatusBadge tone={selectedUsesDynamicRevisions ? "success" : "neutral"}>
                    {selectedUsesDynamicRevisions
                      ? `${lang === "zh" ? "动态修订" : "Dynamic revisions"} · ${selectedSourcePolicy.default_revision_window ?? selectedSourcePolicy.default_revision_window_months}${selectedRevisionWindowSuffix}`
                      : (lang === "zh" ? "无动态修订" : "No dynamic revisions")}
                  </StatusBadge>
                  <StatusBadge tone={selectedSourcePolicy.public_release_enabled ? "success" : "warning"}>
                    {selectedSourcePolicy.public_release_enabled
                      ? (lang === "zh" ? "公开发布" : "Public release")
                      : (lang === "zh" ? "仅内部" : "Internal only")}
                  </StatusBadge>
                  {selectedSourcePolicy.source_update_cadence ? (
                    <StatusBadge>{selectedSourcePolicy.source_update_cadence}</StatusBadge>
                  ) : null}
                  {selectedSourcePolicy.publication_day ? (
                    <StatusBadge>
                      {lang === "zh"
                        ? `每月 ${selectedSourcePolicy.publication_day} 日发布`
                        : `Publishes on day ${selectedSourcePolicy.publication_day}`}
                    </StatusBadge>
                  ) : null}
                </div>
              </div>
            ) : null}
          </FormSection>

          <FormSection title={lang === "zh" ? "调度" : "Schedule"}>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setScheduleMode("daily")}
                className={cn(
                  "h-9 rounded-tremor-default border px-3 text-sm font-medium transition",
                  scheduleMode === "daily"
                    ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted"
                    : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border",
                )}
              >
                {lang === "zh" ? "每天固定时间" : "Daily time"}
              </button>
              <button
                type="button"
                onClick={() => setScheduleMode("interval")}
                className={cn(
                  "h-9 rounded-tremor-default border px-3 text-sm font-medium transition",
                  scheduleMode === "interval"
                    ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted"
                    : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border",
                )}
              >
                {lang === "zh" ? "每隔一段时间" : "Every N"}
              </button>
            </div>

            {scheduleMode === "daily" ? (
              <>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label={lang === "zh" ? "每日执行时间" : "Daily time"}>
                    <input
                      className={inputClass}
                      placeholder="HH:MM"
                      value={form.daily_time ?? ""}
                      onChange={(event) => setForm((prev) => ({ ...prev, daily_time: event.target.value }))}
                    />
                  </Field>
                  <Field label={lang === "zh" ? "时区" : "Timezone"}>
                    <input
                      className={inputClass}
                      value={form.timezone ?? ""}
                      onChange={(event) => setForm((prev) => ({ ...prev, timezone: event.target.value }))}
                    />
                  </Field>
                </div>
                <div className="flex flex-wrap gap-2">
                  {dailyPresetTimes.map((timeText) => (
                    <button
                      key={timeText}
                      type="button"
                      onClick={() => setForm((prev) => ({ ...prev, daily_time: timeText }))}
                      className="h-8 rounded-tremor-default border border-tremor-border bg-tremor-background px-2.5 text-xs font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border"
                    >
                      {timeText}
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <>
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label={lang === "zh" ? "间隔值" : "Interval"}>
                    <input
                      type="number"
                      min="1"
                      className={inputClass}
                      value={intervalValue}
                      onChange={(event) => setIntervalValue(event.target.value)}
                    />
                  </Field>
                  <Field label={lang === "zh" ? "单位" : "Unit"}>
                    <select
                      className={inputClass}
                      value={intervalUnit}
                      onChange={(event) => setIntervalUnit(event.target.value as IntervalUnit)}
                    >
                      <option value="minutes">{lang === "zh" ? "分钟" : "Minutes"}</option>
                      <option value="hours">{lang === "zh" ? "小时" : "Hours"}</option>
                    </select>
                  </Field>
                  <Field label={lang === "zh" ? "时区" : "Timezone"}>
                    <input
                      className={inputClass}
                      value={form.timezone ?? ""}
                      onChange={(event) => setForm((prev) => ({ ...prev, timezone: event.target.value }))}
                    />
                  </Field>
                </div>
                <div className="flex flex-wrap gap-2">
                  {intervalPresetMinutes.map((minutes) => (
                    <button
                      key={minutes}
                      type="button"
                      onClick={() => {
                        const derived = intervalMinutesToFields(minutes);
                        setIntervalValue(derived.value);
                        setIntervalUnit(derived.unit);
                      }}
                      className="h-8 rounded-tremor-default border border-tremor-border bg-tremor-background px-2.5 text-xs font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border"
                    >
                      {lang === "zh"
                        ? minutes % 60 === 0 ? `${minutes / 60} 小时` : `${minutes} 分钟`
                        : minutes % 60 === 0 ? `${minutes / 60}h` : `${minutes}m`}
                    </button>
                  ))}
                </div>
              </>
            )}

            <AlertPanel tone="info">{schedulePreview}</AlertPanel>
          </FormSection>

          <FormSection title={lang === "zh" ? "执行选项" : "Execution"}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={lang === "zh" ? "优先级" : "Priority"}>
                <select
                  className={inputClass}
                  value={form.priority}
                  onChange={(event) => setForm((prev) => ({ ...prev, priority: event.target.value }))}
                >
                  {["low", "normal", "high", "urgent"].map((priority) => (
                    <option key={priority} value={priority}>
                      {priority}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={lang === "zh" ? "失败阈值" : "Retry threshold"}>
                <input
                  type="number"
                  min="0"
                  className={inputClass}
                  value={form.retry_threshold}
                  onChange={(event) => setForm((prev) => ({ ...prev, retry_threshold: Number(event.target.value || 0) }))}
                />
              </Field>
              {selectedUsesDynamicRevisions ? (
                <Field
                  label={selectedRevisionWindowUnit === "weeks"
                    ? (lang === "zh" ? "动态修订窗口（周）" : "Revision window (weeks)")
                    : selectedRevisionWindowUnit === "years"
                      ? (lang === "zh" ? "动态修订窗口（年）" : "Revision window (years)")
                      : (lang === "zh" ? "动态修订窗口（月）" : "Revision window (months)")}
                  hint={lang === "zh" ? "每次运行会重新抓取并覆盖这个窗口。" : "Each run re-fetches and upserts this window."}
                >
                  <input
                    type="number"
                    min="1"
                    max={selectedRevisionWindowUnit === "weeks" ? "52" : selectedRevisionWindowUnit === "years" ? "10" : "24"}
                    className={inputClass}
                    value={form.revision_window_months}
                    onChange={(event) => setForm((prev) => ({
                      ...prev,
                      revision_window_months: Math.max(1, Math.min(selectedRevisionWindowUnit === "weeks" ? 52 : selectedRevisionWindowUnit === "years" ? 10 : 24, Number(event.target.value || 1))),
                    }))}
                  />
                </Field>
              ) : null}
              <Field label={lang === "zh" ? "启用状态" : "Enabled"}>
                <select
                  className={inputClass}
                  value={form.enabled ? "yes" : "no"}
                  onChange={(event) => setForm((prev) => ({ ...prev, enabled: event.target.value === "yes" }))}
                >
                  <option value="yes">enabled</option>
                  <option value="no">disabled</option>
                </select>
              </Field>
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              {([
                ["process", form.process],
                ["save_raw", form.save_raw],
                ["fill_missing", form.fill_missing],
                ["force", form.force],
                ["include_current_month", form.include_current_month],
              ] as Array<[keyof Pick<AutomationJobInput, "process" | "save_raw" | "fill_missing" | "force" | "include_current_month">, boolean]>)
                .filter(([key]) => {
                  if (key === "fill_missing") return selectedSupportsFillMissing;
                  if (key === "include_current_month") return selectedSupportsCurrentMonth;
                  return true;
                })
                .map(([key, value]) => (
                  <label
                    key={key}
                    className="flex min-h-12 cursor-pointer items-center gap-3 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                  >
                    <input
                      type="checkbox"
                      checked={Boolean(value)}
                      onChange={(event) => setForm((prev) => ({ ...prev, [key]: event.target.checked }))}
                      className="h-4 w-4 rounded border-tremor-border text-tremor-brand"
                    />
                    <span>{key === "fill_missing"
                      ? (lang === "zh"
                        ? `回填缺失${selectedSourcePolicy?.temporal_granularity === "weekly" ? "周" : selectedSourcePolicy?.temporal_granularity === "annual" ? "年份" : "月份"}`
                        : `Backfill missing ${selectedSourcePolicy?.temporal_granularity === "weekly" ? "weeks" : selectedSourcePolicy?.temporal_granularity === "annual" ? "years" : "months"}`)
                      : crawlOptionLabels[key]?.[lang] ?? key}</span>
                  </label>
                ))}
            </div>
          </FormSection>

          <FormSection title={lang === "zh" ? "备注" : "Notes"}>
            <textarea
              className="min-h-24 w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
              value={form.notes ?? ""}
              onChange={(event) => setForm((prev) => ({ ...prev, notes: event.target.value }))}
            />
          </FormSection>

          <div className="flex flex-wrap justify-end gap-2 pt-2">
            <ActionButton onClick={resetForm} icon={<RotateCcw className="h-4 w-4" />}>
              {lang === "zh" ? "重置" : "Reset"}
            </ActionButton>
            <ActionButton
              type="submit"
              tone="primary"
              disabled={saving || !selectedSupportsCrawl}
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
    </div>
  );
}
