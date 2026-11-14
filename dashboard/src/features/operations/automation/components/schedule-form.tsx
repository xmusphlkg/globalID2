

import { Pencil, Plus, RotateCcw, Sparkles } from "lucide-react";

import { DetailDrawer } from "@/components/ui/DetailDrawer";

import { StatusBadge } from "@/components/ui/StatusBadge";
import { getCountryDisplayName } from "@/shared/config/countries";
import { type AutomationJobInput } from "@/features/operations/automation/api";

import { cn } from "@/lib/utils";

import type { AutomationController } from "../hooks/use-automation-controller";
import { ActionButton, AlertPanel, Field, FormSection, inputClass } from "./form-controls";
import { findCountryTimezone, defaultFillMissingForCountry, defaultIncludeCurrentMonthForCountry, defaultRevisionWindowForCountry } from "../form-model";
import { dailyPresetTimes, intervalPresetMinutes, intervalMinutesToFields, type IntervalUnit } from "../schedule-model";

const crawlOptionLabels: Record<string, { en: string; zh: string }> = {
  process: { en: "Process data after crawl", zh: "抓取后自动处理数据" },
  save_raw: { en: "Save raw fetched data", zh: "保存 raw 原始抓取数据" },
  fill_missing: { en: "Backfill missing months", zh: "回填缺失月份" },
  force: { en: "Force re-fetch", zh: "强制重新抓取" },
  include_current_month: { en: "Include current month (provisional)", zh: "接入当前月（临时数据）" },
};

export function ScheduleForm({ controller }: { controller: AutomationController }) {
  const { lang, countries, drawerOpen, editingJobId, form, setForm, scheduleMode, setScheduleMode, intervalValue, setIntervalValue, intervalUnit, setIntervalUnit, saving, sourceConfigByCountry, selectedSourceConfig, selectedSourceOption, selectedSupportsCrawl, selectedSourcePolicy, selectedSupportsFillMissing, selectedSupportsCurrentMonth, selectedUsesDynamicRevisions, selectedRevisionWindowUnit, selectedRevisionWindowSuffix, sources, automationPresets, schedulePreview, resetForm, closeDrawer, applyPreset, submitForm } = controller;
  return (
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
  );
}
