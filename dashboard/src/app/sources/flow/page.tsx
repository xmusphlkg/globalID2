"use client";

import { useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useSourcesFlow, useCreateCrawlTask } from "@/lib/hooks/useSources";
import { useCountries } from "@/lib/hooks/useCountries";
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
  FileText,
  Upload,
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
  failed: "rose",
  cancelled: "slate",
  retrying: "yellow",
};

function StageIcon({ stage, status }: { stage: string; status: string | null }) {
  const cls = "h-5 w-5 shrink-0";
  if (status === "running") return <Loader2 className={`${cls} animate-spin text-amber-500`} />;
  if (status === "completed") return <CheckCircle2 className={`${cls} text-emerald-500`} />;
  if (status === "failed") return <AlertCircle className={`${cls} text-rose-500`} />;
  // stage default icon
  if (stage === "crawl") return <Download className={`${cls} text-blue-400`} />;
  if (stage === "process") return <Cog className={`${cls} text-violet-400`} />;
  if (stage === "report") return <FileText className={`${cls} text-teal-400`} />;
  if (stage === "export") return <Upload className={`${cls} text-orange-400`} />;
  return <Circle className={`${cls} text-tremor-content-subtle`} />;
}

const STAGE_LABEL_KEYS: Record<string, string> = {
  crawl: "flow_stage_crawl",
  process: "flow_stage_process",
  report: "flow_stage_report",
  export: "flow_stage_export",
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
  onCreateCrawl,
}: {
  flow: DataSourceFlow;
  lang: "en" | "zh";
  onCreateCrawl: (ds: string) => void;
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

        {/* Action */}
        <div className="flex shrink-0">
          <button
            onClick={() => onCreateCrawl(flow.data_source)}
            className="flex items-center justify-center gap-1.5 rounded-tremor-default bg-tremor-brand px-3 py-2 text-xs font-medium text-tremor-brand-inverted shadow-tremor-input transition hover:opacity-90 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted w-full md:w-auto"
          >
            <Plus className="h-3.5 w-3.5" />
            {t(lang, "flow_create_crawl")}
          </button>
        </div>
      </div>
    </Card>
  );
}

// ── Create crawl task modal ──────────────────────────────────────────────────
function CreateCrawlModal({
  open,
  dataSource,
  countryId,
  lang,
  onClose,
}: {
  open: boolean;
  dataSource: string;
  countryId: number;
  lang: "en" | "zh";
  onClose: () => void;
}) {
  const [taskName, setTaskName] = useState(`Crawl ${dataSource}`);
  const [priority, setPriority] = useState("normal");
  const [description, setDescription] = useState("");
  const { mutate: createTask, isPending, isSuccess } = useCreateCrawlTask();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createTask(
      {
        task_name: taskName,
        country_id: countryId,
        priority,
        description: description || undefined,
        input_data: { data_source: dataSource },
      },
      {
        onSuccess: () => {
          setTimeout(onClose, 800);
        },
      },
    );
  };

  if (!open) return null;

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
          <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-5 w-5" />
            <span className="text-sm font-medium">{t(lang, "flow_task_created")}</span>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                {t(lang, "task_name")}
              </label>
              <input
                required
                value={taskName}
                onChange={(e) => setTaskName(e.target.value)}
                className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                {t(lang, "priority")}
              </label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis"
              >
                {["low", "normal", "high", "urgent"].map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                {t(lang, "flow_description")}
              </label>
              <textarea
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis"
              />
            </div>

            <div className="flex justify-end gap-3">
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
                {t(lang, "flow_submit")}
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
  const { data: countries } = useCountries();

  const [modalSource, setModalSource] = useState<string | null>(null);

  useTaskWebSocket();

  const openModal = (ds: string) => setModalSource(ds);
  const closeModal = () => setModalSource(null);

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
              onClick={() => openModal("New Source")}
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
              onClick={() => openModal("New Source")}
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
              onCreateCrawl={openModal}
            />
          ))}
        </div>
      )}

      {/* Modal */}
      {modalSource && countryId && (
        <CreateCrawlModal
          open={true}
          dataSource={modalSource}
          countryId={countryId}
          lang={lang}
          onClose={closeModal}
        />
      )}
    </div>
  );
}
