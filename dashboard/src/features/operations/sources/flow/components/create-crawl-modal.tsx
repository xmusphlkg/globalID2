import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { components } from "@/generated/api";
import { ApiError, apiFetch } from "@/lib/api";
import { Dialog } from "@/shared/ui";

import { t } from "@/lib/i18n";
import { type CountrySourceConfig } from "@/features/operations/sources/api";

import { getConfiguredSourceOptions } from "@/lib/source-labels";

import { X, CheckCircle2, Loader2 } from "lucide-react";

export function CreateCrawlModal({
  open,
  countryId,
  countryName,
  countryCode,
  sourceConfig,
  lang,
  onClose,
}: {
  open: boolean;
  countryId: number;
  countryName: string;
  countryCode?: string | null;
  sourceConfig?: CountrySourceConfig | null;
  lang: "en" | "zh";
  onClose: () => void;
}) {
  const normalizedCountryCode = (countryCode || sourceConfig?.country_code || "").trim().toUpperCase();
  const sourceOptions = useMemo(
    () => getConfiguredSourceOptions(sourceConfig, lang, normalizedCountryCode),
    [lang, normalizedCountryCode, sourceConfig],
  );
  const supportedMode = Boolean(sourceConfig?.supports_crawl && sourceOptions.length);
  const supportsSourceFile = Boolean(sourceConfig?.supports_source_file);
  const supportsSourceDir = Boolean(sourceConfig?.supports_source_dir);
  const [source, setSource] = useState("all");
  const selectedSourceOption = sourceConfig?.source_options.find((option) => option.value === source);
  const sourcePolicy = selectedSourceOption?.source_policy ?? sourceConfig?.source_policy ?? null;
  const supportsFillMissing = selectedSourceOption?.supports_fill_missing
    ?? sourceConfig?.supports_fill_missing
    ?? !["IS", "US"].includes(normalizedCountryCode);
  const supportsCurrentMonth = Boolean(sourcePolicy?.supports_current_month);
  const usesDynamicRevisions = Boolean(sourcePolicy?.dynamic_revision_enabled);
  const revisionWindowUnit = sourcePolicy?.revision_window_unit ?? "months";
  const revisionWindowSuffix = revisionWindowUnit === "weeks" ? "w" : revisionWindowUnit === "years" ? "y" : "m";
  const supportsStartYear = Boolean(
    selectedSourceOption?.supports_start_year
      ?? sourceConfig?.supports_start_year,
  );
  const isReviewedHistorySource = selectedSourceOption?.source_kind === "history";
  const [priority, setPriority] = useState("normal");
  const [force, setForce] = useState(false);
  const [process, setProcess] = useState(true);
  const [saveRaw, setSaveRaw] = useState(true);
  const [fillMissing, setFillMissing] = useState(selectedSourceOption?.default_fill_missing ?? sourceConfig?.default_fill_missing ?? true);
  const [includeCurrentMonth, setIncludeCurrentMonth] = useState(
    sourcePolicy?.default_include_current_month ?? false,
  );
  const [revisionWindowMonths, setRevisionWindowMonths] = useState(
    Math.max(1, sourcePolicy?.default_revision_window ?? sourcePolicy?.default_revision_window_months ?? 3),
  );
  const [historyStartYear, setHistoryStartYear] = useState(selectedSourceOption?.default_start_year ?? sourceConfig?.default_start_year ?? 2001);
  const [sourceFile, setSourceFile] = useState("");
  const [sourceDir, setSourceDir] = useState("");
  const [error, setError] = useState<string | null>(null);
  const client = useQueryClient();
  const submitLock = useRef(false);
  const { mutate: startCrawl, isPending, isSuccess, data: result, reset } = useMutation({
    mutationFn: (payload: Omit<components["schemas"]["SurveillanceRunCreate"], "line">) => apiFetch<components["schemas"]["TaskReferenceOut"]>("/collection/runs", { method: "POST", body: JSON.stringify({ line: "surveillance", ...payload }) }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["sources-flow"] }); client.invalidateQueries({ queryKey: ["tasks"] }); },
  });
  useEffect(() => { if (open) { reset(); setError(null); submitLock.current = false; } }, [open, reset]);

  useEffect(() => {
    setFillMissing(sourceConfig?.default_fill_missing ?? true);
    setIncludeCurrentMonth(sourceConfig?.source_policy?.default_include_current_month ?? false);
    setRevisionWindowMonths(Math.max(1, sourceConfig?.source_policy?.default_revision_window_months ?? 3));
    setHistoryStartYear(sourceConfig?.default_start_year ?? 2001);
    setSourceFile("");
    setSourceDir("");
  }, [
    open,
    sourceConfig?.default_fill_missing,
    sourceConfig?.default_start_year,
    sourceConfig?.source_policy?.default_include_current_month,
    sourceConfig?.source_policy?.default_revision_window_months,
  ]);

  useEffect(() => {
    if (sourceOptions.length > 0) {
      setSource(sourceOptions[0].value);
    }
  }, [sourceOptions]);

  useEffect(() => {
    setFillMissing(selectedSourceOption?.default_fill_missing ?? sourceConfig?.default_fill_missing ?? true);
    setIncludeCurrentMonth(sourcePolicy?.default_include_current_month ?? false);
    setRevisionWindowMonths(Math.max(1, sourcePolicy?.default_revision_window ?? sourcePolicy?.default_revision_window_months ?? 3));
    setHistoryStartYear(selectedSourceOption?.default_start_year ?? sourceConfig?.default_start_year ?? 2001);
  }, [
    selectedSourceOption?.default_fill_missing,
    selectedSourceOption?.default_start_year,
    sourceConfig?.default_fill_missing,
    sourceConfig?.default_start_year,
    sourcePolicy?.default_include_current_month,
    sourcePolicy?.default_revision_window,
    sourcePolicy?.default_revision_window_months,
  ]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (submitLock.current) return;
    submitLock.current = true;
    setError(null);
    startCrawl(
      {
        country_code: normalizedCountryCode,
        source,
        force,
        process,
        save_raw: saveRaw,
        fill_missing: fillMissing,
        include_current_month: supportsCurrentMonth ? includeCurrentMonth : false,
        revision_window_months: usesDynamicRevisions ? revisionWindowMonths : 3,
        start_year: supportsStartYear ? historyStartYear : undefined,
        source_file: supportsSourceFile && sourceFile.trim() ? sourceFile.trim() : undefined,
        source_dir: supportsSourceDir && sourceDir.trim() ? sourceDir.trim() : undefined,
        priority,
      },
      {
        onSettled: () => { submitLock.current = false; },
        onError: (err: unknown) => {
          const msg = err instanceof ApiError && err.status === 409 ? `${t(lang, "cc_collection_conflict")} ${err.message}` : err instanceof Error ? err.message : String(err);
          setError(msg);
        },
      },
    );
  };

  if (!open) return null;

  const inputCls =
    "w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis";
  const labelCls =
    "mb-1 block text-xs font-medium text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis";

  return (
    <Dialog open={open} onClose={onClose} title={t(lang, "source_ingestion_new")}><div className="relative">
        <button
          onClick={onClose}
          aria-label={t(lang, "source_close")}
          className="absolute right-0 top-0 inline-flex h-9 w-9 items-center justify-center rounded-md text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
        >
          <X className="h-5 w-5" />
        </button>

        {isSuccess ? (
          <div className="flex flex-col items-center gap-2 py-4 text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-8 w-8" />
            <span className="text-sm font-medium">{t(lang, "flow_task_created")}</span>
            <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {t(lang, "source_submitted")}
            </span>
            {result?.task_uuid && <Link className="cc-button" href={`/collection/runs/${encodeURIComponent(result.task_uuid)}`}>{t(lang, "source_open_run")}</Link>}
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4 pt-10">
            {!supportedMode && (
              <div className="rounded-tremor-default border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                {lang === "zh"
                  ? `${countryName || normalizedCountryCode || "当前国家"} 的自动爬取流程尚未配置。`
                  : `Automated crawl workflow is not configured for ${countryName || normalizedCountryCode || "this country"} yet.`}
              </div>
            )}

            {/* Data source selector */}
            <div>
              <label className={labelCls}>
                {lang === "zh" ? "数据源" : "Data Source"}
              </label>
              <select aria-label={lang === "zh" ? "数据源" : "Data Source"} value={source} onChange={(e) => setSource(e.target.value)} className={inputCls} disabled={!supportedMode}>
                {sourceOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>

            {isReviewedHistorySource ? (
              <div className="rounded-tremor-default border border-violet-200 bg-violet-50 px-3 py-2 text-xs text-violet-800 dark:border-violet-900/50 dark:bg-violet-950/25 dark:text-violet-200">
                {lang === "zh"
                  ? (normalizedCountryCode === "IE"
                    ? "该来源回补 HPSC 2004–2020 年度历史，并与 2021 年起的周度源分开保存；NA 保留为缺失。"
                    : "该来源按已审核官方工作簿目录全量检查；不会合成缺失期。")
                  : (normalizedCountryCode === "IE"
                    ? "This source backfills HPSC annual history for 2004–2020 and keeps it separate from the weekly source beginning in 2021; NA remains missing."
                    : "This source checks the complete reviewed official-workbook catalogue without synthesizing missing periods.")}
              </div>
            ) : null}

            {sourcePolicy ? (
              <div className="rounded-tremor-default border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-800 dark:border-sky-900/50 dark:bg-sky-950/25 dark:text-sky-200">
                <p className="font-semibold">{lang === "zh" ? "来源策略" : "Source policy"}</p>
                <p className="mt-1">
                  {supportsCurrentMonth
                    ? `${lang === "zh" ? "当前月可按临时数据接入" : "Current month can be ingested as provisional"} (${sourcePolicy.current_month_status})`
                    : (lang === "zh" ? "仅接入已闭合月份" : "Closed months only")}
                  {usesDynamicRevisions
                    ? ` · ${lang === "zh" ? "默认修订窗口" : "default revision window"} ${sourcePolicy.default_revision_window ?? sourcePolicy.default_revision_window_months}${revisionWindowSuffix}`
                    : ""}
                  {` · ${sourcePolicy.public_release_enabled
                    ? (lang === "zh" ? "允许公开" : "public")
                    : (lang === "zh" ? "仅内部" : "internal only")}`}
                </p>
              </div>
            ) : null}

            {/* Priority */}
            <div>
              <label className={labelCls}>{t(lang, "priority")}</label>
              <select aria-label={t(lang, "priority")} value={priority} onChange={(e) => setPriority(e.target.value)} className={inputCls}>
                {["low", "normal", "high", "urgent"].map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>

            {/* Toggle options */}
            <div className="space-y-2.5">
              <label className="flex items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis cursor-pointer">
                <input type="checkbox" checked={process} onChange={(e) => setProcess(e.target.checked)}
                  className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted" />
                {lang === "zh" ? "获取后自动处理数据" : "Process data after crawl"}
              </label>
              <label className="flex items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis cursor-pointer">
                <input type="checkbox" checked={saveRaw} onChange={(e) => setSaveRaw(e.target.checked)}
                  className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted" />
                {lang === "zh" ? "保存 raw 原始抓取数据（默认）" : "Save raw fetched data (default)"}
              </label>
              {supportsFillMissing && (
                <label className="flex items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis cursor-pointer">
                  <input type="checkbox" checked={fillMissing} onChange={(e) => setFillMissing(e.target.checked)}
                    className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted" />
                  {lang === "zh"
                    ? `回填缺失${sourcePolicy?.temporal_granularity === "weekly" ? "周" : sourcePolicy?.temporal_granularity === "annual" ? "年份" : "月份"}`
                    : `Backfill missing ${sourcePolicy?.temporal_granularity === "weekly" ? "weeks" : sourcePolicy?.temporal_granularity === "annual" ? "years" : "months"}`}
                </label>
              )}
              {supportsCurrentMonth && (
                <label className="flex items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis cursor-pointer">
                  <input
                    type="checkbox"
                    checked={includeCurrentMonth}
                    onChange={(e) => setIncludeCurrentMonth(e.target.checked)}
                    className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted"
                  />
                  {lang === "zh" ? "接入当前月（标记为临时数据）" : "Include current month (provisional)"}
                </label>
              )}
              {usesDynamicRevisions && (
                <div>
                  <label className={labelCls}>
                    {revisionWindowUnit === "weeks"
                      ? (lang === "zh" ? "动态修订窗口（周）" : "Revision window (weeks)")
                      : revisionWindowUnit === "years"
                        ? (lang === "zh" ? "动态修订窗口（年）" : "Revision window (years)")
                        : (lang === "zh" ? "动态修订窗口（月）" : "Revision window (months)")}
                  </label>
                  <input
                    aria-label={lang === "zh" ? "修订窗口" : "Revision window"}
                    type="number"
                    min={1}
                    max={revisionWindowUnit === "weeks" ? 52 : revisionWindowUnit === "years" ? 10 : 24}
                    value={revisionWindowMonths}
                    onChange={(e) => setRevisionWindowMonths(
                      Math.max(1, Math.min(revisionWindowUnit === "weeks" ? 52 : revisionWindowUnit === "years" ? 10 : 24, Number(e.target.value || 1))),
                    )}
                    className={inputCls}
                  />
                  <p className="mt-1 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {lang === "zh"
                      ? `抓取时重新读取并覆盖最近${revisionWindowUnit === "weeks" ? "周" : revisionWindowUnit === "years" ? "年份" : "月份"}，以吸收来源修订。`
                      : `Recent ${revisionWindowUnit} are re-fetched and upserted to absorb source revisions.`}
                  </p>
                </div>
              )}
              {(supportsStartYear || supportsSourceFile || supportsSourceDir) && (
                <div className="space-y-2.5">
                  {supportsStartYear && (
                    <div>
                      <label className={labelCls}>
                        {normalizedCountryCode === "IS"
                          ? (lang === "zh" ? "当前看板起始年份过滤" : "Current-dashboard start-year filter")
                          : (lang === "zh" ? "历史起始年份" : "History Start Year")}
                      </label>
                      <input
                        aria-label={lang === "zh" ? "起始年份" : "Start year"}
                        type="number"
                        min={selectedSourceOption?.default_start_year ?? sourceConfig?.default_start_year ?? 1900}
                        max={selectedSourceOption?.history_end_year ?? new Date().getFullYear()}
                        value={historyStartYear}
                        onChange={(e) => setHistoryStartYear(Number(e.target.value || selectedSourceOption?.default_start_year || sourceConfig?.default_start_year || 2001))}
                        className={inputCls}
                      />
                      {normalizedCountryCode === "IS" ? (
                        <p className="mt-1 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {lang === "zh"
                            ? "只过滤 all / annual / STI / respiratory 当前来源；不改变历史工作簿目录。"
                            : "Filters only current all / annual / STI / respiratory sources; it does not alter the historical workbook catalogue."}
                        </p>
                      ) : null}
                    </div>
                  )}
                  {supportsSourceFile && (
                    <div>
                      <label className={labelCls}>
                        {lang === "zh" ? "来源文件" : "Source File"}
                      </label>
                      <input
                        type="text"
                        aria-label={lang === "zh" ? "来源文件" : "Source file"}
                        value={sourceFile}
                        onChange={(e) => setSourceFile(e.target.value)}
                        placeholder="data/raw/<country>/export.csv"
                        className={inputCls}
                      />
                    </div>
                  )}
                  {supportsSourceDir && (
                    <div>
                      <label className={labelCls}>
                        {lang === "zh" ? "来源目录" : "Source Directory"}
                      </label>
                      <input
                        type="text"
                        aria-label={lang === "zh" ? "来源目录" : "Source directory"}
                        value={sourceDir}
                        onChange={(e) => setSourceDir(e.target.value)}
                        placeholder="data/raw/<country>/"
                        className={inputCls}
                      />
                    </div>
                  )}
                </div>
              )}
              <label className="flex items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis cursor-pointer">
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)}
                  className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted" />
                <span className={force ? "text-amber-600 dark:text-amber-400" : ""}>
                  {lang === "zh" ? "强制模式（忽略数据库，重新爬取全部）" : "Force mode (ignore DB, re-crawl all)"}
                </span>
              </label>
            </div>

            {/* Error message */}
            {error && (
              <div role="alert" className="rounded-tremor-default border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
                {error}
              </div>
            )}

            {/* Actions */}
            <div className="flex justify-end gap-3 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="rounded-tremor-default border border-tremor-border px-4 py-2 text-sm text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
              >
                {t(lang, "flow_cancel")}
              </button>
              <button
                type="submit"
                disabled={isPending || !supportedMode}
                className="flex items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 py-2 text-sm font-medium text-tremor-brand-inverted transition hover:opacity-90 disabled:opacity-60 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
              >
                {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {t(lang, "source_ingestion_start")}
              </button>
            </div>
          </form>
        )}
      </div>
    </Dialog>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────
