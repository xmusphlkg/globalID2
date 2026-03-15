"use client";

import { useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useSourcesFlow, useStartCrawl } from "@/lib/hooks/useSources";
import { useTaskWebSocket } from "@/lib/hooks/useTasks";
import { formatDate } from "@/lib/utils";
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
import type { DataSourceFlow, StageInfo } from "@/lib/hooks/useSources";

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

function sourceLabel(lang: "en" | "zh", source?: string | null): string {
  const s = (source || "all").toLowerCase();
  if (s === "pubmed") return "PubMed";
  if (s === "cdc_weekly") return "CDC Weekly";
  if (s === "nhc") return lang === "zh" ? "国家卫健委" : "NHC";
  return lang === "zh" ? "全部" : "All";
}

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
  return (
    <Card className="p-4 overflow-hidden">
      <div className="flex flex-col md:flex-row md:items-center gap-4">
        {/* Source name + stats */}
        <div className="w-full md:w-[320px] flex-shrink-0 space-y-1.5">
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
          </div>
          {flow.latest_task_uuid && (
            <div className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "最近任务" : "Latest task"}: {sourceLabel(lang, flow.latest_task_source)}
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
  lang,
  onClose,
}: {
  open: boolean;
  countryId: number;
  lang: "en" | "zh";
  onClose: () => void;
}) {
  const [source, setSource] = useState("all");
  const [priority, setPriority] = useState("normal");
  const [force, setForce] = useState(false);
  const [process, setProcess] = useState(true);
  const [fillMissing, setFillMissing] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { mutate: startCrawl, isPending, isSuccess } = useStartCrawl();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    startCrawl(
      {
        country_id: countryId,
        source,
        force,
        process,
        save_raw: true,
        fill_missing: fillMissing,
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
      <div className="relative w-full max-w-md rounded-tremor-default bg-tremor-background p-6 shadow-xl dark:bg-dark-tremor-background">
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
            {/* Data source selector */}
            <div>
              <label className={labelCls}>
                {lang === "zh" ? "数据源" : "Data Source"}
              </label>
              <select value={source} onChange={(e) => setSource(e.target.value)} className={inputCls}>
                <option value="all">{lang === "zh" ? "全部" : "All Sources"}</option>
                <option value="cdc_weekly">CDC Weekly (English)</option>
                <option value="nhc">{lang === "zh" ? "国家卫健委" : "NHC (Chinese)"}</option>
                <option value="pubmed">PubMed RSS</option>
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
                <input type="checkbox" checked={fillMissing} onChange={(e) => setFillMissing(e.target.checked)}
                  className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted" />
                {lang === "zh" ? "回填缺失月份" : "Backfill missing months"}
              </label>
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
                disabled={isPending}
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
  const { lang, countryId, countryName } = useAppStore();
  const { data: flows, isLoading } = useSourcesFlow(countryId);

  const [modalOpen, setModalOpen] = useState(false);

  useTaskWebSocket();

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
          {countryId && (
            <button
              onClick={openModal}
              className="flex items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 py-2 text-sm font-medium text-tremor-brand-inverted shadow-tremor-input transition hover:opacity-90 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
            >
              <Plus className="h-4 w-4" />
              {t(lang, "flow_new_crawl_task")}
            </button>
          )}
        </div>
      </div>

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
      {!countryId ? (
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
          <div className="flex items-center gap-2">
            <GitBranch className="h-5 w-5 text-tremor-brand dark:text-dark-tremor-brand" />
            <Title>
              {countryName} — {flows.length} {t(lang, "flow_sources_count")}
            </Title>
          </div>
          {flows.map((flow) => (
            <FlowRow
              key={flow.data_source}
              flow={flow}
              lang={lang}
            />
          ))}
        </div>
      )}

      {/* Modal */}
      {modalOpen && countryId && (
        <CreateCrawlModal
          open={true}
          countryId={countryId}
          lang={lang}
          onClose={closeModal}
        />
      )}
    </div>
  );
}
