"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, Grid, Metric, Text, Title } from "@tremor/react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import {
  type AutomationJob,
  type AutomationJobInput,
  useAutomationConfig,
  useAutomationJobs,
  useCreateAutomationJob,
  useDeleteAutomationJob,
  useRunAutomationJob,
  useUpdateAutomationJob,
} from "@/lib/hooks/useSources";
import { useTaskWebSocket } from "@/lib/hooks/useTasks";
import { getCountryDisplayName, type Country, useCountries } from "@/lib/hooks/useCountries";
import { getSourceDisplayLabel, getSourceOptionsForCountry } from "@/lib/source-labels";
import { AlertTriangle, Bot, Clock3, Mail, Pencil, Play, Plus, Sparkles, Trash2 } from "lucide-react";

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

const presetTemplates = [
  { id: "cn", job_id: "cn-daily", name: "CN Daily Crawl", country_code: "CN", source: "all", daily_time: "08:00" },
  { id: "us", job_id: "us-daily", name: "US Daily Crawl", country_code: "US", source: "nndss_api", daily_time: "08:15" },
  { id: "jp", job_id: "jp-daily", name: "JP Daily Crawl", country_code: "JP", source: "jp_weekly", daily_time: "08:30" },
  { id: "au", job_id: "au-daily", name: "AU Daily Crawl", country_code: "AU", source: "all", daily_time: "08:45" },
] as const;

const crawlOptionLabels: Record<string, { en: string; zh: string }> = {
  process: { en: "Process data after crawl", zh: "抓取后自动处理数据" },
  save_raw: { en: "Save raw fetched data", zh: "保存 raw 原始抓取数据" },
  fill_missing: { en: "Backfill missing months", zh: "回填缺失月份" },
  force: { en: "Force re-fetch", zh: "强制重新抓取" },
};

function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function scheduleLabel(job: { interval_minutes?: number | null; daily_time?: string | null; timezone?: string | null }): string {
  if (job.interval_minutes) return `Every ${job.interval_minutes} minute(s)`;
  if (job.daily_time) return `Daily at ${job.daily_time} (${job.timezone || "UTC"})`;
  return "No schedule";
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

export default function SourcesAutomationPage() {
  const { lang } = useAppStore();
  const { data: config, isLoading } = useAutomationConfig();
  const { data: jobs } = useAutomationJobs();
  const { data: countries } = useCountries();
  const runJob = useRunAutomationJob();
  const createJob = useCreateAutomationJob();
  const updateJob = useUpdateAutomationJob();
  const deleteJob = useDeleteAutomationJob();
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  const [form, setForm] = useState<AutomationJobInput>(defaultForm);

  useTaskWebSocket({ extraQueryKeys: [["sources-automation"], ["sources-automation-jobs"], ["sources-flow"]] });

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

  const resetForm = () => {
    setEditingJobId(null);
    setForm({
      ...defaultForm,
      timezone: config?.timezone || defaultForm.timezone,
    });
  };

  const applyPreset = (presetId: (typeof presetTemplates)[number]["id"]) => {
    const preset = presetTemplates.find((item) => item.id === presetId);
    if (!preset) return;
    const timezone = findCountryTimezone(countries, preset.country_code) || config?.timezone || defaultForm.timezone;
    setEditingJobId(null);
    setForm({
      ...defaultForm,
      ...preset,
      timezone,
      notes: `${preset.name} preset`,
    });
  };

  const submitForm = async () => {
    const payload: AutomationJobInput = {
      ...form,
      job_id: form.job_id.trim(),
      name: form.name.trim(),
      country_code: form.country_code.trim().toUpperCase(),
      source: form.source.trim().toLowerCase(),
      priority: form.priority.trim().toLowerCase(),
      daily_time: form.daily_time?.trim() || null,
      timezone: form.timezone?.trim() || config?.timezone || "UTC",
      notes: form.notes?.trim() || null,
      interval_minutes: form.interval_minutes ? Number(form.interval_minutes) : null,
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
    setForm(toForm(job));
  };

  const removeJob = async (job: AutomationJob) => {
    const ok = window.confirm(
      lang === "zh" ? `确认删除自动化任务 ${job.name} 吗？` : `Delete automation job ${job.name}?`,
    );
    if (!ok) return;
    await deleteJob.mutateAsync(job.job_id);
    if (editingJobId === job.job_id) resetForm();
  };

  const sources = getSourceOptionsForCountry(form.country_code, lang);

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
            ? "这里可以新增、修改、删除自动化抓取任务；邮件收件人与 Microsoft Graph 凭证仍通过 env 管理。"
            : "Create, edit, and delete automation jobs here; email recipients and Microsoft Graph credentials remain env-managed."}
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
          </div>

          <div className="mt-6">
            <Title>Notification Status</Title>
            <div className="mt-3 space-y-3">
              <Badge color={deliveryStatus.color}>{deliveryStatus.label}</Badge>
              {!config?.email_enabled ? (
                <div className="rounded-tremor-default border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
                  <div className="flex items-center gap-2 font-medium">
                    <AlertTriangle className="h-4 w-4" />
                    Microsoft Graph mail is not fully configured.
                  </div>
                  <Text className="mt-2">
                    Set `AUTOMATION__GRAPH_ENABLED=true` and provide tenant, client, secret, and sender user id in `.env`.
                  </Text>
                </div>
              ) : null}
              {!config?.admin_emails.length ? (
                <div className="rounded-tremor-default border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
                  <Text>Add `AUTOMATION__ADMIN_EMAILS_RAW` in `.env` to receive failure alerts.</Text>
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
                <Text>No admin emails configured</Text>
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
                {presetTemplates.map((preset) => (
                  <Button
                    key={preset.id}
                    size="xs"
                    variant="secondary"
                    icon={Sparkles}
                    onClick={() => applyPreset(preset.id)}
                  >
                    {preset.country_code}
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
                onChange={(e) => setForm((prev) => ({ ...prev, country_code: e.target.value }))}
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
              >
                {sources.map((source) => (
                  <option key={source.value} value={source.value}>{source.label}</option>
                ))}
              </select>
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
                  type="number"
                  className="w-full rounded-tremor-default border border-tremor-border px-3 py-2 text-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  placeholder="retry threshold"
                  value={form.retry_threshold}
                  onChange={(e) => setForm((prev) => ({ ...prev, retry_threshold: Number(e.target.value || 0) }))}
                />
              </div>
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
                ] as Array<[string, boolean]>).map(([key, value]) => (
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
                onClick={submitForm}
              >
                {editingJobId ? "Save changes" : "Create job"}
              </Button>
            </div>
          </div>
        </Card>

        <Card className="lg:col-span-2">
          <Title>Automation Jobs</Title>
          <Text className="mt-1">Jobs here are stored in the database and used directly by the scheduler.</Text>

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
                      <Text>{scheduleLabel(job)}</Text>
                    </div>
                    <div className="flex flex-wrap gap-2">
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
                      <Text className="mt-1 font-medium">{formatDateTime(job.next_run_at)}</Text>
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
                      <Text className="mt-1 break-all font-mono text-xs font-medium">{job.last_task_uuid || "-"}</Text>
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
