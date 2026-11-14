
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { useCountries } from "@/shared/config/countries";
import { type AutomationJob, type AutomationJobInput, type CountrySourceConfig, useAutomationConfig, useAutomationJobs, useCreateAutomationJob, useDeleteAutomationJob, useRunAutomationJob, useSourceConfigs, useUpdateAutomationJob } from "@/features/operations/automation/api";
import { useTaskEventStream, useWorkerStatus } from "@/features/operations/tasks/api";

import { getConfiguredSourceOptions } from "@/lib/source-labels";

import { useAppStore } from "@/stores/app-store";

import { defaultForm, toForm, findCountryTimezone, defaultIncludeCurrentMonthForCountry, defaultRevisionWindowForCountry, presetTimeForIndex, type AutomationPreset } from "../form-model";
import { DEFAULT_INTERVAL_MINUTES, intervalMinutesToFields, intervalFieldsToMinutes, describeIntervalMinutes, type ScheduleMode, type IntervalUnit } from "../schedule-model";

export type JobFilter = "all" | "enabled" | "disabled" | "failed";

export function useAutomationController() {
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

  useTaskEventStream({ extraQueryKeys: [["sources-automation"], ["sources-automation-jobs"], ["sources-flow"]] });

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

  return { lang, config, isLoading, jobs, countries, sourceConfigs, workerStatus, runJob, createJob, updateJob, deleteJob, drawerOpen, setDrawerOpen, editingJobId, setEditingJobId, form, setForm, scheduleMode, setScheduleMode, intervalValue, setIntervalValue, intervalUnit, setIntervalUnit, jobSearch, setJobSearch, jobFilter, setJobFilter, schedulerEnabled, workerRunning, saving, sourceConfigByCountry, selectedSourceConfig, selectedSourceOption, selectedSupportsCrawl, selectedSourcePolicy, selectedSupportsFillMissing, selectedSupportsCurrentMonth, selectedUsesDynamicRevisions, selectedRevisionWindowUnit, selectedRevisionWindowSuffix, sources, automationPresets, summary, deliveryStatus, filteredJobs, schedulePreview, syncScheduleControls, resetForm, openNewJob, closeDrawer, applyPreset, startEdit, submitForm, removeJob };
}

export type AutomationController = ReturnType<typeof useAutomationController>;
