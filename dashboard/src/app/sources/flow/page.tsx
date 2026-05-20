"use client";

import { useEffect, useMemo, useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import {
  type CountrySourceConfig,
  type DataSourceFlow,
  type StageInfo,
  useSourceConfigs,
  useSourcesFlow,
  useStartCrawl,
} from "@/lib/hooks/useSources";
import { useTaskWebSocket } from "@/lib/hooks/useTasks";
import { formatDate } from "@/lib/utils";
import { getConfiguredSourceOptions, getSourceDisplayLabel } from "@/lib/source-labels";
import {
  Badge,
  Card,
  Grid,
  Text,
  Title,
  ProgressBar,
} from "@tremor/react";
import type { Color } from "@tremor/react";
import {
  GitBranch,
  Download,
  Cog,
  Plus,
  X,
  ChevronRight,
  CheckCircle2,
  Circle,
  AlertCircle,
  Loader2,
} from "lucide-react";

// ── Colour mapping ──────────────────────────────────────────────────────────
const statusBadge: Record<string, Color> = {
  pending: "slate",
  queued: "blue",
  running: "yellow",
  completed: "emerald",
  skipped: "gray",
  failed: "rose",
  cancelled: "slate",
  retrying: "yellow",
};

function StageIcon({ stage, status }: { stage: string; status: string | null }) {
  const cls = "h-5 w-5 shrink-0";
  if (status === "running") return <Loader2 className={`${cls} animate-spin text-amber-500`} />;
  if (status === "completed") return <CheckCircle2 className={`${cls} text-emerald-500`} />;
  if (status === "skipped") return <Circle className={`${cls} text-slate-400`} />;
  if (status === "failed") return <AlertCircle className={`${cls} text-rose-500`} />;
  // stage default icon
  if (stage === "fetch_list") return <Download className={`${cls} text-blue-400`} />;
  if (stage === "incremental_check") return <GitBranch className={`${cls} text-violet-400`} />;
  if (stage === "process_store") return <Cog className={`${cls} text-teal-400`} />;
  if (stage === "finalize") return <Circle className={`${cls} text-orange-400`} />;
  return <Circle className={`${cls} text-tremor-content-subtle`} />;
}

const STAGE_LABEL_KEYS: Record<string, string> = {
  fetch_list: "flow_stage_fetch_list",
  incremental_check: "flow_stage_incremental_check",
  process_store: "flow_stage_process_store",
  finalize: "flow_stage_finalize",
};

// ── Stage pill ───────────────────────────────────────────────────────────────
function StagePill({
  stage,
  lang,
}: {
  stage: StageInfo;
  lang: "en" | "zh";
}) {
  const labelKey = STAGE_LABEL_KEYS[stage.stage] ?? stage.stage;
  const label = t(lang, labelKey as Parameters<typeof t>[1]);
  const isDone = stage.status === "completed";
  const isRun = stage.status === "running";
  const isFail = stage.status === "failed";

  return (
    <div
      className={`flex flex-col items-center justify-center gap-1.5 rounded-tremor-default border p-2.5 text-center transition w-[100px] shrink-0 min-h-[96px]
        ${isDone ? "border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/30" : ""}
        ${isRun ? "border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30" : ""}
        ${isFail ? "border-rose-200 bg-rose-50 dark:border-rose-800 dark:bg-rose-950/30" : ""}
        ${!stage.status ? "border-tremor-border bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle opacity-60" : ""}
      `}
    >
      <StageIcon stage={stage.stage} status={stage.status} />
      <span className="text-xs font-medium text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis leading-tight">
        {label}
      </span>
      {stage.status ? (
        <Badge color={statusBadge[stage.status] ?? "slate"} className="px-1.5 py-0 text-[10px]">
          {stage.status}
        </Badge>
      ) : (
        <span className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          —
        </span>
      )}
      {stage.status === "running" && (
        <ProgressBar value={stage.progress} color="amber" className="h-1.5 w-full mt-auto" />
      )}
      {stage.last_run && stage.status !== "running" && (
        <span className="text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle truncate max-w-full">
          {formatDate(stage.last_run).split(" ")[0]}
        </span>
      )}
    </div>
  );
}

// ── Row per data source ──────────────────────────────────────────────────────
function FlowRow({
  flow,
  lang,
}: {
  flow: DataSourceFlow;
  lang: "en" | "zh";
}) {
  const coverageStart = flow.earliest_date ?? (
    flow.history_start_year ? `${flow.history_start_year}-01-01` : null
  );
  const coverageText = coverageStart && flow.latest_date
    ? `${coverageStart} → ${flow.latest_date}`
    : coverageStart
      ? (lang === "zh" ? `配置起始：${coverageStart}` : `Configured from ${coverageStart}`)
      : null;

  return (
    <Card className="p-4 overflow-hidden">
      <div className="flex flex-col md:flex-row md:items-center gap-4">
        {/* Source name + stats */}
        <div className="w-full md:w-[320px] flex-shrink-0 space-y-1.5">
          {flow.country_name ? (
            <div className="flex items-center gap-2">
              <Badge color="teal">{flow.country_code || flow.country_name}</Badge>
              <Text className="text-xs">{flow.country_name}</Text>
            </div>
          ) : null}
          <Title className="text-sm font-semibold break-words whitespace-normal leading-tight">{flow.data_source}</Title>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span className="inline-flex items-center gap-1 text-tremor-content dark:text-dark-tremor-content">
              {flow.record_count.toLocaleString()} records
            </span>
            {flow.latest_date && (
              <span className="inline-flex items-center gap-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {t(lang, "latest_date")}: {flow.latest_date}
              </span>
            )}
            {coverageText && (
              <span className="inline-flex items-center gap-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh" ? "历史覆盖" : "History"}: {coverageText}
              </span>
            )}
          </div>
          {flow.latest_task_uuid && (
            <div className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "最近任务" : "Latest task"}: {getSourceDisplayLabel(flow.latest_task_source, lang, flow.country_code)}
              {" · "}
              {flow.latest_task_status || "-"}
              {flow.latest_task_time ? ` · ${formatDate(flow.latest_task_time)}` : ""}
            </div>
          )}
        </div>

        {/* Pipeline stages */}
        <div className="flex flex-1 items-center gap-2 overflow-x-auto pb-2 md:pb-0 scrollbar-hide py-1">
          {flow.stages.map((stage, idx) => (
            <div key={stage.stage} className="flex items-center gap-2">
              <StagePill stage={stage} lang={lang} />
              {idx < flow.stages.length - 1 && (
                <ChevronRight className="h-4 w-4 shrink-0 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
              )}
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

// ── Create crawl task modal ──────────────────────────────────────────────────
function CreateCrawlModal({
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
  const supportsFillMissing = sourceConfig?.supports_fill_missing ?? normalizedCountryCode !== "US";
  const supportsStartYear = Boolean(sourceConfig?.supports_start_year);
  const supportsSourceFile = Boolean(sourceConfig?.supports_source_file);
  const supportsSourceDir = Boolean(sourceConfig?.supports_source_dir);
  const [source, setSource] = useState("all");
  const [priority, setPriority] = useState("normal");
  const [force, setForce] = useState(false);
  const [process, setProcess] = useState(true);
  const [saveRaw, setSaveRaw] = useState(true);
  const [fillMissing, setFillMissing] = useState(sourceConfig?.default_fill_missing ?? true);
  const [historyStartYear, setHistoryStartYear] = useState(sourceConfig?.default_start_year ?? 2001);
  const [sourceFile, setSourceFile] = useState("");
  const [sourceDir, setSourceDir] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { mutate: startCrawl, isPending, isSuccess } = useStartCrawl();

  useEffect(() => {
    setFillMissing(sourceConfig?.default_fill_missing ?? true);
    setHistoryStartYear(sourceConfig?.default_start_year ?? 2001);
    setSourceFile("");
    setSourceDir("");
  }, [open, sourceConfig?.default_fill_missing, sourceConfig?.default_start_year]);

  useEffect(() => {
    if (sourceOptions.length > 0) {
      setSource(sourceOptions[0].value);
    }
  }, [sourceOptions]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    startCrawl(
      {
        country_id: countryId,
        source,
        force,
        process,
        save_raw: saveRaw,
        fill_missing: fillMissing,
        start_year: supportsStartYear ? historyStartYear : undefined,
        source_file: supportsSourceFile && sourceFile.trim() ? sourceFile.trim() : undefined,
        source_dir: supportsSourceDir && sourceDir.trim() ? sourceDir.trim() : undefined,
        priority,
      },
      {
        onSuccess: () => {
          setTimeout(onClose, 1200);
        },
        onError: (err: unknown) => {
          const msg = err instanceof Error ? err.message : String(err);
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="relative max-h-[90vh] w-full max-w-md overflow-y-auto rounded-tremor-default bg-tremor-background p-6 shadow-xl dark:bg-dark-tremor-background">
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
        >
          <X className="h-5 w-5" />
        </button>

        <Title className="mb-4">{t(lang, "flow_new_crawl_task")}</Title>

        {isSuccess ? (
          <div className="flex flex-col items-center gap-2 py-4 text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-8 w-8" />
            <span className="text-sm font-medium">{t(lang, "flow_task_created")}</span>
            <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "任务已开始执行，可在任务页面查看进度" : "Task is now running. Check Tasks page for progress."}
            </span>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
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
              <select value={source} onChange={(e) => setSource(e.target.value)} className={inputCls} disabled={!supportedMode}>
                {sourceOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>

            {/* Priority */}
            <div>
              <label className={labelCls}>{t(lang, "priority")}</label>
              <select value={priority} onChange={(e) => setPriority(e.target.value)} className={inputCls}>
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
                  {lang === "zh" ? "回填缺失月份" : "Backfill missing months"}
                </label>
              )}
              {(supportsStartYear || supportsSourceFile || supportsSourceDir) && (
                <div className="space-y-2.5">
                  {supportsStartYear && (
                    <div>
                      <label className={labelCls}>
                        {lang === "zh" ? "历史起始年份" : "History Start Year"}
                      </label>
                      <input
                        type="number"
                        min={sourceConfig?.default_start_year ?? 1900}
                        max={new Date().getFullYear()}
                        value={historyStartYear}
                        onChange={(e) => setHistoryStartYear(Number(e.target.value || sourceConfig?.default_start_year || 2001))}
                        className={inputCls}
                      />
                    </div>
                  )}
                  {supportsSourceFile && (
                    <div>
                      <label className={labelCls}>
                        {lang === "zh" ? "来源文件" : "Source File"}
                      </label>
                      <input
                        type="text"
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
              <div className="rounded-tremor-default border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
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
                {lang === "zh" ? "开始爬取" : "Start Crawl"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────
export default function SourcesFlowPage() {
  const { lang, countryId, countryName, countryCode } = useAppStore();
  const [scopeMode, setScopeMode] = useState<"selected" | "all">("selected");
  const effectiveCountryId = scopeMode === "all" ? null : countryId;
  const effectiveCountryName =
    scopeMode === "all"
      ? (lang === "zh" ? "全部国家" : "All countries")
      : countryName;

  const { data: flows, isLoading, error } = useSourcesFlow(effectiveCountryId);
  const { data: sourceConfigs } = useSourceConfigs(lang);
  const sourceConfigByCountry = useMemo(() => {
    const map = new Map<string, CountrySourceConfig>();
    for (const config of sourceConfigs ?? []) {
      map.set(config.country_code.toUpperCase(), config);
    }
    return map;
  }, [sourceConfigs]);
  const selectedSourceConfig = sourceConfigByCountry.get((countryCode || "").toUpperCase()) ?? null;
  const countrySupported = scopeMode === "all" || Boolean(selectedSourceConfig?.supports_crawl);

  const [modalOpen, setModalOpen] = useState(false);

  useTaskWebSocket({ extraQueryKeys: [["sources-flow"]] });

  const openModal = () => setModalOpen(true);
  const closeModal = () => setModalOpen(false);

  // summary counts
  const totalSources = flows?.length ?? 0;
  const activeFlows = flows?.filter((f) =>
    f.stages.some((s) => s.status === "running"),
  ).length ?? 0;
  const failedFlows = flows?.filter((f) =>
    f.stages.some((s) => s.status === "failed"),
  ).length ?? 0;
  const completedFlows = flows?.filter((f) =>
    f.stages.every((s) => !s.status || s.status === "completed"),
  ).length ?? 0;
  const flowsByCountry = useMemo(() => {
    const groups = new Map<string, DataSourceFlow[]>();
    for (const flow of flows ?? []) {
      const key = flow.country_code || flow.country_name || "Unknown";
      const current = groups.get(key) ?? [];
      current.push(flow);
      groups.set(key, current);
    }
    return Array.from(groups.entries())
      .map(([key, items]) => ({
        key,
        countryName: items[0]?.country_name || key,
        countryCode: items[0]?.country_code || null,
        items: items.sort((a, b) => b.record_count - a.record_count),
      }))
      .sort((a, b) => a.countryName.localeCompare(b.countryName));
  }, [flows]);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      {/* Header */}
      <div className="space-y-2">
        <Badge color="teal" className="w-fit">{t(lang, "mod_sources")}</Badge>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {t(lang, "flow_title")}
            </h1>
            <Text>{t(lang, "flow_subtitle")}</Text>
          </div>
          {countryId && countrySupported && (
            <button
              onClick={openModal}
              className="flex items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 py-2 text-sm font-medium text-tremor-brand-inverted shadow-tremor-input transition hover:opacity-90 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
            >
              <Plus className="h-4 w-4" />
              {t(lang, "flow_new_crawl_task")}
            </button>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-tremor-border bg-tremor-background p-1.5 shadow-sm">
          <button
            onClick={() => setScopeMode("selected")}
            className={`rounded-xl px-4 py-2 text-sm font-medium transition ${scopeMode === "selected" ? "bg-tremor-brand text-tremor-brand-inverted" : "text-tremor-content-strong"}`}
          >
            {lang === "zh" ? "当前国家" : "Selected country"}
          </button>
          <button
            onClick={() => setScopeMode("all")}
            className={`rounded-xl px-4 py-2 text-sm font-medium transition ${scopeMode === "all" ? "bg-tremor-brand text-tremor-brand-inverted" : "text-tremor-content-strong"}`}
          >
            {lang === "zh" ? "全部国家" : "All countries"}
          </button>
        </div>
      </div>

      {scopeMode === "selected" && countryId && !countrySupported && (
        <Card>
          <Text>
            {lang === "zh"
              ? "当前国家的自动爬取工作流尚未设计，flow 仅展示已入库数据来源。"
              : "Automated crawl workflow is not designed for this country yet. Flow currently shows ingested source data only."}
          </Text>
        </Card>
      )}

      {/* KPI row */}
      <Grid numItemsSm={2} numItemsLg={4} className="gap-4">
        <Card decoration="top" decorationColor="blue">
          <Text>{t(lang, "flow_kpi_sources")}</Text>
          <p className="mt-1 text-3xl font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {totalSources}
          </p>
        </Card>
        <Card decoration="top" decorationColor="amber">
          <Text>{t(lang, "flow_kpi_active")}</Text>
          <p className="mt-1 text-3xl font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {activeFlows}
          </p>
        </Card>
        <Card decoration="top" decorationColor="emerald">
          <Text>{t(lang, "flow_kpi_completed")}</Text>
          <p className="mt-1 text-3xl font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {completedFlows}
          </p>
        </Card>
        <Card decoration="top" decorationColor="rose">
          <Text>{t(lang, "flow_kpi_failed")}</Text>
          <p className="mt-1 text-3xl font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {failedFlows}
          </p>
        </Card>
      </Grid>

      {/* Country guard */}
      {scopeMode === "selected" && !countryId ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <GitBranch className="mb-4 h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Title>{t(lang, "flow_select_country")}</Title>
            <Text>{t(lang, "flow_select_country_hint")}</Text>
          </div>
        </Card>
      ) : isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-24 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
          ))}
        </div>
      ) : error ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <AlertCircle className="mb-4 h-12 w-12 text-rose-500" />
            <Title>{lang === "zh" ? "加载失败" : "Failed to load flow"}</Title>
            <Text className="max-w-2xl">
              {error instanceof Error ? error.message : (lang === "zh" ? "请求数据时发生错误。" : "An error occurred while loading data.")}
            </Text>
            <Text className="mt-2 text-xs text-tremor-content">
              {lang === "zh"
                ? "如果你刚刚更新了 dashboard 代码，请重启 API 服务；旧版 API 不支持 all countries 聚合接口。"
                : "If you just updated the dashboard code, restart the API service. Older API builds do not support the all-countries flow endpoint."}
            </Text>
          </div>
        </Card>
      ) : !flows || flows.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <GitBranch className="mb-4 h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Title>{t(lang, "no_data")}</Title>
            <Text>{t(lang, "flow_no_sources_hint")}</Text>
            <button
              onClick={openModal}
              className="mt-4 flex items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 py-2 text-sm font-medium text-tremor-brand-inverted transition hover:opacity-90 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
            >
              <Plus className="h-4 w-4" />
              {t(lang, "flow_create_first_task")}
            </button>
          </div>
        </Card>
      ) : (
        <div className="space-y-4">
          {scopeMode === "all" ? (
            flowsByCountry.map((group) => (
              <section key={group.key} className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <GitBranch className="h-5 w-5 text-tremor-brand dark:text-dark-tremor-brand" />
                  <Title>
                    {group.countryName} {group.countryCode ? `(${group.countryCode})` : ""}
                  </Title>
                  <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {group.items.length} {t(lang, "flow_sources_count")}
                  </Text>
                </div>
                {group.items.map((flow) => (
                  <FlowRow
                    key={`${group.key}-${flow.data_source}`}
                    flow={flow}
                    lang={lang}
                  />
                ))}
              </section>
            ))
          ) : (
            <>
              <div className="flex items-center gap-2">
                <GitBranch className="h-5 w-5 text-tremor-brand dark:text-dark-tremor-brand" />
                <Title>
                  {effectiveCountryName} — {flows.length} {t(lang, "flow_sources_count")}
                </Title>
              </div>
              {flows.map((flow) => (
                <FlowRow
                  key={flow.data_source}
                  flow={flow}
                  lang={lang}
                />
              ))}
            </>
          )}
        </div>
      )}

      {/* Modal */}
      {modalOpen && countryId && (
        <CreateCrawlModal
          open={true}
          countryId={countryId}
          countryName={countryName}
          countryCode={countryCode}
          sourceConfig={selectedSourceConfig}
          lang={lang}
          onClose={closeModal}
        />
      )}
    </div>
  );
}
