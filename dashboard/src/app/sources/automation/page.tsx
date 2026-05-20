"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, Grid, Metric, Text, Title } from "@tremor/react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
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
} from "@/lib/hooks/useSources";
import { useTaskWebSocket, useWorkerStatus } from "@/lib/hooks/useTasks";
import { getCountryDisplayName, type Country, useCountries } from "@/lib/hooks/useCountries";
import { getConfiguredSourceOptions, getSourceDisplayLabel } from "@/lib/source-labels";
import { AlertTriangle, ArrowRight, Bot, Clock3, Cpu, Mail, Pencil, Play, Plus, Sparkles, Trash2 } from "lucide-react";

type ScheduleMode = "daily" | "interval";
type IntervalUnit = "minutes" | "hours";

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
};

const DEFAULT_INTERVAL_MINUTES = 60;
const intervalPresetMinutes = [15, 30, 60, 180, 360, 720] as const;
const dailyPresetTimes = ["00:00", "08:00", "12:00", "18:00"] as const;

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
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return null;
  }
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
  if (job.interval_minutes) {
    return describeIntervalMinutes(job.interval_minutes, lang);
  }
  if (job.daily_time) {
    return lang === "zh"
      ? `每天 ${job.daily_time} (${job.timezone || "UTC"})`
      : `Daily at ${job.daily_time} (${job.timezone || "UTC"})`;
  }
  return lang === "zh" ? "未设置计划" : "No schedule";
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

function presetTimeForIndex(index: number): string {
  const totalMinutes = 8 * 60 + index * 15;
  const hour = Math.floor(totalMinutes / 60) % 24;
  const minute = totalMinutes % 60;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
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
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  const [form, setForm] = useState<AutomationJobInput>(defaultForm);
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>("daily");
  const [intervalValue, setIntervalValue] = useState("1");
  const [intervalUnit, setIntervalUnit] = useState<IntervalUnit>("hours");

  useTaskWebSocket({ extraQueryKeys: [["sources-automation"], ["sources-automation-jobs"], ["sources-flow"]] });

  const schedulerEnabled = Boolean(config?.enabled);
  const sourceConfigByCountry = useMemo(() => {
    const map = new Map<string, CountrySourceConfig>();
    for (const item of sourceConfigs ?? []) {
      map.set(item.country_code.toUpperCase(), item);
    }
    return map;
  }, [sourceConfigs]);
  const selectedSourceConfig = sourceConfigByCountry.get(form.country_code.trim().toUpperCase()) ?? null;
  const selectedSupportsCrawl = Boolean(selectedSourceConfig?.supports_crawl);
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
      }));

    const historyPresets = (sourceConfigs ?? [])
      .filter((item) => item.supports_start_year)
      .map((item) => ({
        id: `${item.country_code.toLowerCase()}-history`,
        label: `${item.country_code} hist`,
        job_id: `${item.country_code.toLowerCase()}-history-backfill`,
        name: `${item.country_code} Historical Backfill`,
        country_code: item.country_code,
        source: item.default_source,
        daily_time: "02:00",
        timezone: item.timezone,
        enabled: false,
        priority: "high",
        fill_missing: true,
        force: true,
      }));

    return [...basePresets, ...historyPresets];
  }, [sourceConfigs]);

  const summary = useMemo(() => {
    const list = jobs ?? [];
    return {
      total: list.length,
      enabled: list.filter((job) => job.enabled).length,
      alerting: config?.email_enabled ? config.admin_emails.length : 0,
    };
  }, [config, jobs]);

  const deliveryStatus = useMemo(() => {
    if (!config) return { color: "slate" as const, label: "Loading" };
    if (!config.admin_emails.length) return { color: "amber" as const, label: "Missing admin emails" };
    if (!config.email_enabled) return { color: "amber" as const, label: "Email not configured" };
    return { color: "emerald" as const, label: "Failure alerts ready" };
  }, [config]);

  const schedulePreview = useMemo(() => {
    if (scheduleMode === "interval") {
      const minutes = intervalFieldsToMinutes(intervalValue, intervalUnit);
      if (!minutes) {
        return lang === "zh" ? "请设置有效的间隔时间" : "Set a valid interval";
      }
      return lang === "zh"
        ? `${describeIntervalMinutes(minutes, lang)}，时区 ${form.timezone || config?.timezone || "UTC"}`
        : `${describeIntervalMinutes(minutes, lang)} in ${form.timezone || config?.timezone || "UTC"}`;
    }
    const timeText = (form.daily_time || "").trim();
    if (!timeText) {
      return lang === "zh" ? "请设置每日执行时间" : "Set a daily run time";
    }
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

  const applyPreset = (presetId: string) => {
    const preset = automationPresets.find((item) => item.id === presetId);
    if (!preset) return;
    const presetConfig = sourceConfigByCountry.get(preset.country_code.toUpperCase());
    const timezone =
      findCountryTimezone(countries, preset.country_code) ||
      presetConfig?.timezone ||
      config?.timezone ||
      defaultForm.timezone;
    setEditingJobId(null);
    const nextForm = {
      ...defaultForm,
      ...preset,
      timezone,
      notes: `${preset.name} preset`,
    };
    setForm(nextForm);
    syncScheduleControls(nextForm);
  };

  const submitForm = async () => {
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
    const nextDailyTime = scheduleMode === "daily"
      ? form.daily_time?.trim() || null
      : null;

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
    resetForm();
  };

  const startEdit = (job: AutomationJob) => {
    setEditingJobId(job.job_id);
    const nextForm = toForm(job);
    setForm(nextForm);
    syncScheduleControls(nextForm);
  };

  const removeJob = async (job: AutomationJob) => {
    const ok = window.confirm(
      lang === "zh" ? `确认删除自动化任务 ${job.name} 吗？` : `Delete automation job ${job.name}?`,
    );
    if (!ok) return;
    await deleteJob.mutateAsync(job.job_id);
    if (editingJobId === job.job_id) resetForm();
  };

  const sources = useMemo(
    () => getConfiguredSourceOptions(selectedSourceConfig, lang, form.country_code),
    [form.country_code, lang, selectedSourceConfig],
  );

  useEffect(() => {
    if (!sources.some((option) => option.value === form.source) && sources[0]) {
      setForm((prev) => ({ ...prev, source: sources[0].value }));
    }
  }, [form.source, sources]);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="teal" className="w-fit">{t(lang, "mod_sources")}</Badge>
        <h1 className="text-3xl font-semibold tracking-tight text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {t(lang, "automation")}
        </h1>
        <Text>
          {lang === "zh"
            ? "这里可以新增、修改、删除自动化抓取任务；邮件收件人与 SMTP 凭证已统一收口到设置中心。"
            : "Create, edit, and delete automation jobs here; email recipients and SMTP credentials are centralized in Settings."}
        </Text>
        {!isLoading && !schedulerEnabled ? (
          <div className="rounded-tremor-default border border-rose-300 bg-rose-50 p-4 text-sm text-rose-900 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200">
            <div className="flex items-center gap-2 font-medium">
              <AlertTriangle className="h-4 w-4" />
              {lang === "zh"
                ? "自动化总开关当前是关闭状态，已保存的 job 不会自动创建爬取任务。"
                : "The scheduler is currently disabled, so saved jobs will not create crawl tasks automatically."}
            </div>
            <Text className="mt-2">
              {lang === "zh"
                ? "请在 `.env` 中将 `AUTOMATION__ENABLED` 设为 `true`，然后重启 API 服务。"
                : "Set `AUTOMATION__ENABLED=true` in `.env`, then restart the API service."}
            </Text>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-3 pt-2">
          <Link
            href="/sources/tasks?scope=all"
            className="inline-flex items-center gap-2 rounded-xl border border-tremor-border bg-tremor-background px-4 py-2 text-sm font-medium text-tremor-content-strong shadow-sm transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background"
          >
            {lang === "zh" ? "查看采集任务" : "Open Crawl Tasks"}
            <ArrowRight className="h-4 w-4" />
          </Link>
          <Link
            href="/sources/flow"
            className="inline-flex items-center gap-2 rounded-xl border border-tremor-border bg-tremor-background px-4 py-2 text-sm font-medium text-tremor-content-strong shadow-sm transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background"
          >
            {lang === "zh" ? "查看数据流程" : "Open Data Flow"}
            <ArrowRight className="h-4 w-4" />
          </Link>
          <Link
            href="/setting"
            className="inline-flex items-center gap-2 rounded-xl border border-teal-200 bg-teal-50 px-4 py-2 text-sm font-medium text-teal-700 shadow-sm transition hover:bg-teal-100 dark:border-teal-900/50 dark:bg-teal-950/25 dark:text-teal-300"
          >
            {lang === "zh" ? "打开设置中心" : "Open Settings"}
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
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
          <Text>Timezone</Text>
          <Metric>{config?.timezone ?? "-"}</Metric>
        </Card>
        <Card decoration="top" decorationColor="rose">
          <Text>Alert Recipients</Text>
          <Metric>{summary.alerting}</Metric>
        </Card>
      </Grid>

      <Grid numItemsLg={3} className="gap-6">
        <Card className="lg:col-span-1">
          <Title>Runtime</Title>
          <div className="mt-4 space-y-3 text-sm">
            <div className="flex items-center gap-2">
              <Clock3 className="h-4 w-4 text-tremor-content-subtle" />
              <Text>Last tick: {formatDateTime(config?.last_tick_at)}</Text>
            </div>
            <div className="flex items-center gap-2">
              <Bot className="h-4 w-4 text-tremor-content-subtle" />
              <Text>Poll interval: {config?.poll_interval_seconds ?? "-"}s</Text>
            </div>
            <div className="flex items-center gap-2">
              <Mail className="h-4 w-4 text-tremor-content-subtle" />
              <Text>Email delivery: {config?.email_enabled ? "configured" : "disabled"}</Text>
            </div>
            <div className="flex items-center gap-2">
              <Cpu className="h-4 w-4 text-tremor-content-subtle" />
              <Text>
                Worker: {workerStatus
                  ? (workerStatus.worker_process_running ? "running" : "stopped")
                  : "checking"}
              </Text>
            </div>
          </div>

          {!workerStatus?.worker_process_running ? (
            <div className="mt-4 rounded-tremor-default border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
              <Text>
                {lang === "zh"
                  ? "自动化只会先创建 queued 任务；如果 worker 没启动，任务不会继续执行。"
                  : "Automation only creates queued tasks first; without a worker process those tasks will not continue running."}
              </Text>
            </div>
          ) : null}

          <div className="mt-6">
            <Title>Notification Status</Title>
            <div className="mt-3 space-y-3">
              <Badge color={deliveryStatus.color}>{deliveryStatus.label}</Badge>
              {!config?.email_enabled ? (
                <div className="rounded-tremor-default border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
                  <div className="flex items-center gap-2 font-medium">
                    <AlertTriangle className="h-4 w-4" />
                    {lang === "zh" ? "设置中心中的 SMTP 还未准备完成。" : "SMTP is not ready in the Settings Center."}
                  </div>
                  <Text className="mt-2">
                    {lang === "zh"
                      ? "请到设置中心补齐 SMTP 主机、账号、密码和发件邮箱。"
                      : "Open Settings to finish the SMTP host, username, password, and from-email setup."}
                  </Text>
                </div>
              ) : null}
              {!config?.admin_emails.length ? (
                <div className="rounded-tremor-default border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
                  <Text>
                    {lang === "zh"
                      ? "请在设置中心补充管理员邮箱，自动化失败提醒才会送达。"
                      : "Add admin email recipients in Settings so automation failure alerts have somewhere to go."}
                  </Text>
                </div>
              ) : null}
            </div>
          </div>

          <div className="mt-6">
            <Title>Admin Emails</Title>
            <div className="mt-3 flex flex-wrap gap-2">
              {(config?.admin_emails ?? []).length > 0 ? (
                config?.admin_emails.map((email) => (
                  <Badge key={email} color="slate">{email}</Badge>
                ))
              ) : (
                <Text>{lang === "zh" ? "尚未配置管理员邮箱" : "No admin emails configured"}</Text>
              )}
            </div>
          </div>

          <div className="mt-6 rounded-tremor-default border border-dashed border-tremor-border p-4 dark:border-dark-tremor-border">
            <div className="flex items-center justify-between gap-2">
              <Title>{editingJobId ? "Edit Job" : "New Job"}</Title>
              <Button size="xs" variant="light" onClick={resetForm}>Reset</Button>
            </div>

            <div className="mt-4">
              <Text>Quick presets</Text>
              <div className="mt-2 flex flex-wrap gap-2">
                {automationPresets.map((preset) => (
                  <Button
                    key={preset.id}
                    size="xs"
                    variant="secondary"
                    icon={Sparkles}
                    onClick={() => applyPreset(preset.id)}
                  >
                    {preset.label}
                  </Button>
                ))}
              </div>
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
              <select
                className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                value={form.country_code}
                onChange={(e) => {
                  const nextCode = e.target.value;
                  const nextConfig = sourceConfigByCountry.get(nextCode.toUpperCase());
                  setForm((prev) => ({
                    ...prev,
                    country_code: nextCode,
                    source: nextConfig?.default_source || prev.source,
                    fill_missing: defaultFillMissingForCountry(nextConfig),
                    timezone:
                      findCountryTimezone(countries, nextCode) ||
                      nextConfig?.timezone ||
                      prev.timezone,
                  }));
                }}
              >
                {(countries ?? []).map((country) => (
                  <option key={country.code} value={country.code}>
                    {getCountryDisplayName(country, lang)}
                  </option>
                ))}
              </select>
              <select
                className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                value={form.source}
                onChange={(e) => setForm((prev) => ({ ...prev, source: e.target.value }))}
                disabled={!selectedSupportsCrawl}
              >
                {sources.map((source) => (
                  <option key={source.value} value={source.value}>{source.label}</option>
                ))}
              </select>
              {!selectedSupportsCrawl ? (
                <div className="rounded-tremor-default border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                  {lang === "zh"
                    ? "这个国家还没有在后端 registry 中声明可采集来源。"
                    : "This country has no crawl source declared in the backend registry yet."}
                </div>
              ) : null}
              <div className="rounded-tremor-default border border-dashed border-tremor-border p-4 dark:border-dark-tremor-border">
                <div className="flex items-center justify-between gap-3">
                  <Text>{lang === "zh" ? "调度方式" : "Schedule"}</Text>
                  <Badge color={scheduleMode === "interval" ? "teal" : "blue"}>
                    {scheduleMode === "interval"
                      ? (lang === "zh" ? "按间隔执行" : "Interval")
                      : (lang === "zh" ? "按天执行" : "Daily")}
                  </Badge>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    size="xs"
                    variant={scheduleMode === "daily" ? "primary" : "secondary"}
                    onClick={() => setScheduleMode("daily")}
                  >
                    {lang === "zh" ? "每天固定时间" : "Daily time"}
                  </Button>
                  <Button
                    size="xs"
                    variant={scheduleMode === "interval" ? "primary" : "secondary"}
                    onClick={() => setScheduleMode("interval")}
                  >
                    {lang === "zh" ? "每隔一段时间" : "Every N"}
                  </Button>
                </div>

                {scheduleMode === "daily" ? (
                  <>
                    <div className="mt-3 grid grid-cols-2 gap-3">
                      <input
                        className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                        placeholder="daily_time HH:MM"
                        value={form.daily_time ?? ""}
                        onChange={(e) => setForm((prev) => ({ ...prev, daily_time: e.target.value }))}
                      />
                      <input
                        className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                        placeholder="timezone"
                        value={form.timezone ?? ""}
                        onChange={(e) => setForm((prev) => ({ ...prev, timezone: e.target.value }))}
                      />
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {dailyPresetTimes.map((timeText) => (
                        <Button
                          key={timeText}
                          size="xs"
                          variant="secondary"
                          onClick={() => setForm((prev) => ({ ...prev, daily_time: timeText }))}
                        >
                          {timeText}
                        </Button>
                      ))}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="mt-3 grid grid-cols-3 gap-3">
                      <input
                        type="number"
                        min="1"
                        className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                        placeholder={lang === "zh" ? "间隔值" : "Interval"}
                        value={intervalValue}
                        onChange={(e) => setIntervalValue(e.target.value)}
                      />
                      <select
                        className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                        value={intervalUnit}
                        onChange={(e) => setIntervalUnit(e.target.value as IntervalUnit)}
                      >
                        <option value="minutes">{lang === "zh" ? "分钟" : "Minutes"}</option>
                        <option value="hours">{lang === "zh" ? "小时" : "Hours"}</option>
                      </select>
                      <input
                        className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                        placeholder="timezone"
                        value={form.timezone ?? ""}
                        onChange={(e) => setForm((prev) => ({ ...prev, timezone: e.target.value }))}
                      />
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {intervalPresetMinutes.map((minutes) => (
                        <Button
                          key={minutes}
                          size="xs"
                          variant="secondary"
                          onClick={() => {
                            const derived = intervalMinutesToFields(minutes);
                            setIntervalValue(derived.value);
                            setIntervalUnit(derived.unit);
                          }}
                        >
                          {lang === "zh"
                            ? (minutes % 60 === 0 ? `${minutes / 60} 小时` : `${minutes} 分钟`)
                            : (minutes % 60 === 0 ? `${minutes / 60}h` : `${minutes}m`)}
                        </Button>
                      ))}
                    </div>
                  </>
                )}

                <Text className="mt-3 text-xs">{schedulePreview}</Text>
              </div>
              <input
                type="number"
                className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                placeholder="retry threshold"
                value={form.retry_threshold}
                onChange={(e) => setForm((prev) => ({ ...prev, retry_threshold: Number(e.target.value || 0) }))}
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
              <textarea
                className="min-h-24 w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                placeholder="notes"
                value={form.notes ?? ""}
                onChange={(e) => setForm((prev) => ({ ...prev, notes: e.target.value }))}
              />
              <div className="grid grid-cols-2 gap-2 text-sm">
                {([
                  ["process", form.process],
                  ["save_raw", form.save_raw],
                  ["fill_missing", form.fill_missing],
                  ["force", form.force],
                ] as Array<[string, boolean]>)
                  .filter(([key]) => key !== "fill_missing" || (selectedSourceConfig?.supports_fill_missing ?? true))
                  .map(([key, value]) => (
                  <label key={key} className="flex items-center gap-2 rounded-tremor-default border border-tremor-border px-3 py-2 dark:border-dark-tremor-border">
                    <input
                      type="checkbox"
                      checked={Boolean(value)}
                      onChange={(e) => setForm((prev) => ({ ...prev, [key]: e.target.checked }))}
                    />
                    <span>{crawlOptionLabels[key]?.[lang] ?? key}</span>
                  </label>
                ))}
              </div>
              <Button
                icon={editingJobId ? Pencil : Plus}
                loading={createJob.isPending || updateJob.isPending}
                disabled={!selectedSupportsCrawl}
                onClick={submitForm}
              >
                {editingJobId ? "Save changes" : "Create job"}
              </Button>
            </div>
          </div>
        </Card>

        <Card className="lg:col-span-2">
          <Title>Automation Jobs</Title>
          <Text className="mt-1">
            {schedulerEnabled
              ? "Jobs here are stored in the database and used directly by the scheduler."
              : (lang === "zh"
                ? "这些 job 已经保存在数据库里，但当前调度器总开关关闭，所以它们不会自动运行。"
                : "These jobs are stored in the database, but the scheduler is disabled, so they will not run automatically.")}
          </Text>

          <div className="mt-4 space-y-4">
            {isLoading ? (
              [1, 2, 3].map((idx) => (
                <div key={idx} className="h-28 animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
              ))
            ) : !(jobs?.length) ? (
              <div className="rounded-tremor-default border border-dashed border-tremor-border p-8 text-center dark:border-dark-tremor-border">
                <Text>No automation jobs configured.</Text>
              </div>
            ) : (
              jobs.map((job) => (
                <Card key={job.job_id} className="border border-tremor-border dark:border-dark-tremor-border">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Title>{job.name}</Title>
                        <Badge color={job.enabled ? "emerald" : "slate"}>{job.enabled ? "enabled" : "disabled"}</Badge>
                        <Badge color="slate">{job.country_code}</Badge>
                        <Badge color="blue">{getSourceDisplayLabel(job.source, lang, job.country_code)}</Badge>
                      </div>
                      <Text>{scheduleLabel(job, lang)}</Text>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Link
                        href={`/sources/tasks?scope=all${job.last_task_uuid ? `&search=${encodeURIComponent(job.last_task_uuid)}` : ""}`}
                        className="inline-flex items-center gap-1 rounded-tremor-default border border-tremor-border px-3 py-1.5 text-xs font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:hover:bg-dark-tremor-background-subtle"
                      >
                        {lang === "zh" ? "查看任务" : "View tasks"}
                        <ArrowRight className="h-3.5 w-3.5" />
                      </Link>
                      <Button size="xs" icon={Play} loading={runJob.isPending} onClick={() => runJob.mutate(job.job_id)}>
                        Run now
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
                      <Text className="mt-1 font-medium">
                        {schedulerEnabled
                          ? formatDateTime(job.next_run_at)
                          : (lang === "zh" ? "调度器已关闭" : "Scheduler off")}
                      </Text>
                    </Card>
                    <Card className="p-3">
                      <Text>Last status</Text>
                      <Text className="mt-1 font-medium">{job.last_status}</Text>
                    </Card>
                    <Card className="p-3">
                      <Text>Retry threshold</Text>
                      <Text className="mt-1 font-medium">{job.retry_threshold}</Text>
                    </Card>
                    <Card className="p-3">
                      <Text>Last task</Text>
                      {job.last_task_uuid ? (
                        <Link
                          href={`/sources/tasks?scope=all&search=${encodeURIComponent(job.last_task_uuid)}`}
                          className="mt-1 block break-all font-mono text-xs font-medium text-tremor-brand hover:underline"
                        >
                          {job.last_task_uuid}
                        </Link>
                      ) : (
                        <Text className="mt-1 break-all font-mono text-xs font-medium">-</Text>
                      )}
                    </Card>
                  </Grid>

                  <div className="mt-4 grid gap-2 text-sm text-tremor-content dark:text-dark-tremor-content md:grid-cols-2">
                    <Text>Priority: {job.priority}</Text>
                    <Text>Last started: {formatDateTime(job.last_started_at)}</Text>
                    <Text>Process: {job.process ? "yes" : "no"}</Text>
                    <Text>Last finished: {formatDateTime(job.last_finished_at)}</Text>
                    <Text>Save raw: {job.save_raw ? "yes" : "no"}</Text>
                    <Text>Run count: {job.run_count}</Text>
                    <Text>Fill missing: {job.fill_missing ? "yes" : "no"}</Text>
                    <Text>Skipped count: {job.skipped_count}</Text>
                    <Text>Force: {job.force ? "yes" : "no"}</Text>
                    <Text className="break-words">Last error: {job.last_error || "-"}</Text>
                    {job.notes ? <Text className="break-words md:col-span-2">Notes: {job.notes}</Text> : null}
                  </div>
                </Card>
              ))
            )}
          </div>
        </Card>
      </Grid>
    </div>
  );
}
