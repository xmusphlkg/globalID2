"use client";

import { useEffect, useMemo, useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import {
  type CountrySourceConfig,
  type DataSourceFlow,
  type OntologySeries,
  type StageInfo,
  useOntologySeries,
  useSourceConfigs,
  useSourcesFlow,
  useStartCrawl,
  useSituationSources,
  useRefreshSituationSources,
} from "@/features/operations/sources/api";
import { useTaskEventStream } from "@/features/operations/tasks/api";
import { formatDate } from "@/lib/utils";
import { getConfiguredSourceOptions, getSourceDisplayLabel } from "@/lib/source-labels";
import { formatSeriesMetadata } from "./series-format";
import {
  Badge,
  Text,
  Title,
  ProgressBar,
} from "@/components/ui/tremor";
import type { Color } from "@/components/ui/tremor";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  GitBranch,
  Database,
  Download,
  Cog,
  Plus,
  X,
  ChevronRight,
  CheckCircle2,
  Circle,
  AlertCircle,
  Loader2,
  ExternalLink,
  RadioTower,
  RefreshCw,
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
  const sourceSeriesCount = flow.source_series_count ?? 0;
  const sourceObservationCount = flow.source_observation_count ?? 0;
  const hasSeriesFacts = sourceSeriesCount > 0 || sourceObservationCount > 0;
  const coverageStart = flow.earliest_date ?? (
    flow.history_start_year ? `${flow.history_start_year}-01-01` : null
  );
  const coverageText = coverageStart && flow.latest_date
    ? `${coverageStart} → ${flow.latest_date}`
    : coverageStart
      ? (lang === "zh" ? `配置起始：${coverageStart}` : `Configured from ${coverageStart}`)
      : null;

  return (
    <div className="app-panel overflow-hidden p-4">
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
              {hasSeriesFacts
                ? `${sourceObservationCount.toLocaleString()} ${lang === "zh" ? "条来源观测" : "source observations"}`
                : `${flow.record_count.toLocaleString()} ${lang === "zh" ? "条兼容记录" : "compatibility records"}`}
            </span>
            {hasSeriesFacts ? (
              <span className="inline-flex items-center gap-1 text-tremor-content dark:text-dark-tremor-content">
                {sourceSeriesCount.toLocaleString()} {lang === "zh" ? "个来源序列" : "source series"}
              </span>
            ) : null}
            {hasSeriesFacts && flow.record_count > 0 ? (
              <span className="inline-flex items-center gap-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh" ? "兼容投影" : "Compatibility projection"}: {flow.record_count.toLocaleString()}
              </span>
            ) : null}
            {flow.latest_date && (
              <span className="inline-flex items-center gap-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh" ? "数据覆盖截止" : "Coverage end"}: {flow.latest_date}
              </span>
            )}
            {coverageText && (
              <span className="inline-flex items-center gap-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh" ? "历史覆盖" : "History"}: {coverageText}
              </span>
            )}
          </div>
          {hasSeriesFacts ? (
            <div className="space-y-1.5 pt-1 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {Object.entries(flow.metric_types ?? {}).length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {Object.entries(flow.metric_types).map(([metric, count]) => (
                    <StatusBadge key={metric} tone={metric === "registered_diagnoses" ? "warning" : "info"}>
                      {metric === "registered_diagnoses"
                        ? (lang === "zh" ? "登记诊断序列（非病例通知）" : "Registered-diagnosis series (not case notifications)")
                        : metric === "case_notifications"
                          ? (lang === "zh" ? "病例通知序列" : "Case-notification series")
                        : formatSeriesMetadata(metric)}
                      {` ${count}`}
                    </StatusBadge>
                  ))}
                </div>
              ) : null}
              <div className="flex flex-wrap gap-1">
                {Object.entries(flow.series_availability ?? {}).map(([status, count]) => (
                  <StatusBadge key={`series-${status}`} tone={status === "active" ? "success" : "neutral"}>
                    {lang === "zh" ? "序列状态" : "Series"} {status} {count}
                  </StatusBadge>
                ))}
                {Object.entries(flow.source_availability ?? {}).map(([status, count]) => (
                  <StatusBadge key={`source-${status}`} tone={status === "available" ? "success" : "warning"}>
                    {lang === "zh" ? "可用性" : "Availability"} {status} {count}
                  </StatusBadge>
                ))}
                {Object.entries(flow.observation_quality ?? {}).map(([status, count]) => (
                  <StatusBadge key={`quality-${status}`} tone={status === "rejected" ? "danger" : "neutral"}>
                    {lang === "zh" ? "质量" : "Quality"} {status} {count.toLocaleString()}
                  </StatusBadge>
                ))}
                {Object.entries(flow.mapping_relations ?? {}).map(([status, count]) => (
                  <StatusBadge key={`mapping-${status}`} tone={status === "exact" ? "success" : "warning"}>
                    {lang === "zh" ? "映射" : "Mapping"} {status} {count}
                  </StatusBadge>
                ))}
                {Object.entries(flow.comparability ?? {}).map(([status, count]) => (
                  <StatusBadge key={`comparability-${status}`} tone={status === "direct" ? "success" : "neutral"}>
                    {lang === "zh" ? "可比性" : "Comparability"} {formatSeriesMetadata(status)} {count}
                  </StatusBadge>
                ))}
              </div>
            </div>
          ) : null}
          {flow.latest_task_uuid && (
            <div className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "最近运行/检查" : "Latest run/check"}: {getSourceDisplayLabel(flow.latest_task_source, lang, flow.country_code)}
              {" · "}
              {flow.latest_task_status || "-"}
              {flow.latest_task_time ? ` · ${formatDate(flow.latest_task_time)}` : ""}
            </div>
          )}
          {!flow.latest_task_uuid && flow.source_scope ? (
            <div className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh"
                ? "最近运行/检查：未记录（待首次调度运行）"
                : "Latest run/check: not recorded (awaiting first scheduled run)"}
            </div>
          ) : null}
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
    </div>
  );
}

interface SeriesMappingRisk {
  key: string;
  conceptId: string;
  sourceId: string;
  frequency: string;
  measure: string;
  rows: OntologySeries[];
  labels: string[];
}

function flowRowKey(flow: DataSourceFlow) {
  return [
    flow.country_code || flow.country_name || "unknown",
    flow.source_scope || "default",
    flow.data_source,
  ].join("::");
}

function buildSeriesMappingRisks(rows: OntologySeries[]): SeriesMappingRisk[] {
  const groups = new Map<string, OntologySeries[]>();
  for (const row of rows) {
    const conceptId = row.concept_id || row.target?.id || "unmapped";
    const key = [
      conceptId,
      row.source_id,
      row.frequency,
      row.measure,
      row.reporting_basis,
    ].join("|");
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }

  const risks: SeriesMappingRisk[] = [];
  for (const [key, groupedRows] of groups) {
    if (groupedRows.length < 2) continue;
    const labels = Array.from(
      new Set(
        groupedRows
          .flatMap((row) => row.local_labels ?? [])
          .map((label) => label.trim())
          .filter(Boolean),
      ),
    );
    const normalizedLabels = new Set(labels.map((label) => label.toLocaleLowerCase()));
    if (normalizedLabels.size < 2) continue;
    const first = groupedRows[0];
    risks.push({
      key,
      conceptId: first.concept_id || first.target?.id || "unmapped",
      sourceId: first.source_id,
      frequency: first.frequency,
      measure: first.measure,
      rows: groupedRows,
      labels,
    });
  }

  return risks.sort((left, right) => {
    const leftNonCase = left.measure === "registered_diagnoses" ? 1 : 0;
    const rightNonCase = right.measure === "registered_diagnoses" ? 1 : 0;
    return leftNonCase - rightNonCase || left.conceptId.localeCompare(right.conceptId);
  });
}

function SeriesMappingRegister({
  rows,
  lang,
}: {
  rows: OntologySeries[];
  lang: "en" | "zh";
}) {
  const [search, setSearch] = useState("");
  const risks = useMemo(() => buildSeriesMappingRisks(rows), [rows]);
  const targetCount = useMemo(
    () => new Set(rows.map((row) => row.concept_id || row.target?.id).filter(Boolean)).size,
    [rows],
  );
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const filteredRows = useMemo(
    () => rows.filter((row) => {
      if (!normalizedSearch) return true;
      return [
        row.id,
        row.source_id,
        row.concept_id ?? "",
        ...(row.local_codes ?? []),
        ...(row.local_labels ?? []),
        row.target?.labels?.en ?? "",
        row.target?.labels?.zh ?? "",
        row.measure,
        row.mapping_relation,
        row.comparability,
      ]
        .join(" ")
        .toLocaleLowerCase()
        .includes(normalizedSearch);
    }),
    [normalizedSearch, rows],
  );

  return (
    <section className="app-panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {lang === "zh" ? "来源序列与疾病映射" : "Source-series disease mappings"}
          </h2>
          <p className="mt-1 max-w-3xl text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {lang === "zh"
              ? "以 series registry 为全量口径；历史序列不会因 is_active=false 被隐藏。映射关系与可比性是两个独立结论。"
              : "The series registry is the complete source of truth; historical series remain visible even when is_active=false. Mapping relation and comparability are separate assertions."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge tone="info">{rows.length} {lang === "zh" ? "个序列" : "series"}</StatusBadge>
          <StatusBadge>{targetCount} {lang === "zh" ? "个疾病目标" : "disease targets"}</StatusBadge>
          <StatusBadge tone={risks.length > 0 ? "warning" : "success"}>
            {risks.length} {lang === "zh" ? "组语义复核" : "semantic review groups"}
          </StatusBadge>
        </div>
      </div>

      {risks.length > 0 ? (
        <div className="mt-4 space-y-2">
          <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">
            {lang === "zh"
              ? "同一目标、来源、粒度下存在多个非同义标签；代表序列选择不能被理解为合并。"
              : "Multiple non-synonymous labels share a target, source, and grain; representative selection must not be interpreted as a merge."}
          </p>
          <div className="grid gap-2 lg:grid-cols-2">
            {risks.map((risk) => (
              <div key={risk.key} className="rounded-tremor-default border border-amber-200 bg-amber-50/70 p-3 dark:border-amber-900/50 dark:bg-amber-950/20">
                <div className="flex flex-wrap items-center gap-1.5">
                  <StatusBadge tone="warning">{risk.conceptId}</StatusBadge>
                  <StatusBadge>{risk.frequency}</StatusBadge>
                  <StatusBadge tone={risk.measure === "registered_diagnoses" ? "warning" : "info"}>
                    {risk.measure === "registered_diagnoses"
                      ? (lang === "zh" ? "登记诊断量（非病例通知）" : "registered diagnoses (not case notifications)")
                      : formatSeriesMetadata(risk.measure)}
                  </StatusBadge>
                </div>
                <p className="mt-2 text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {risk.labels.join(" · ")}
                </p>
                <p className="mt-1 font-mono text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {risk.rows.map((row) => row.id).join(" · ")}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <details className="mt-4 rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {lang === "zh" ? `核查全部 ${rows.length} 个来源序列` : `Inspect all ${rows.length} source series`}
        </summary>
        <div className="border-t border-tremor-border p-3 dark:border-dark-tremor-border">
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={lang === "zh" ? "搜索 D-code、来源标签、metric…" : "Search D-code, source label, metric…"}
            className="mb-3 h-9 w-full max-w-md rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          />
          <div className="max-h-[560px] overflow-auto">
            <table className="w-full min-w-[980px] border-collapse text-left text-xs">
              <thead className="sticky top-0 bg-tremor-background dark:bg-dark-tremor-background">
                <tr className="border-b border-tremor-border dark:border-dark-tremor-border">
                  <th className="px-2 py-2">Series / source</th>
                  <th className="px-2 py-2">Local label</th>
                  <th className="px-2 py-2">Target</th>
                  <th className="px-2 py-2">Metric / grain</th>
                  <th className="px-2 py-2">Mapping / comparability</th>
                  <th className="px-2 py-2">Availability</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row) => (
                  <tr key={row.id} className="border-b border-tremor-border/70 align-top dark:border-dark-tremor-border/70">
                    <td className="px-2 py-2">
                      <div className="font-mono text-[11px] text-tremor-content-strong dark:text-dark-tremor-content-strong">{row.id}</div>
                      <div className="mt-1 font-mono text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{row.source_id}</div>
                    </td>
                    <td className="max-w-[260px] px-2 py-2 text-tremor-content dark:text-dark-tremor-content">{(row.local_labels ?? []).join(" · ") || "-"}</td>
                    <td className="px-2 py-2">
                      <div className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{row.concept_id || row.target?.id || "-"}</div>
                      <div className="mt-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? row.target?.labels?.zh || row.target?.labels?.en : row.target?.labels?.en || row.target?.labels?.zh}
                      </div>
                    </td>
                    <td className="px-2 py-2">
                      <StatusBadge tone={row.measure === "registered_diagnoses" ? "warning" : "info"}>
                        {row.measure === "registered_diagnoses"
                          ? (lang === "zh" ? "登记诊断量（非病例通知）" : "registered diagnoses (not case notifications)")
                          : formatSeriesMetadata(row.measure)}
                      </StatusBadge>
                      <div className="mt-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{row.frequency} · {formatSeriesMetadata(row.reporting_basis)}</div>
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex flex-wrap gap-1">
                        <StatusBadge tone={row.mapping_relation === "exact" ? "success" : row.mapping_relation ? "warning" : "neutral"}>{formatSeriesMetadata(row.mapping_relation)}</StatusBadge>
                        <StatusBadge tone={row.comparability === "direct" ? "success" : "neutral"}>{formatSeriesMetadata(row.comparability)}</StatusBadge>
                      </div>
                      <div className="mt-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{formatSeriesMetadata(row.aggregation_policy)}</div>
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex flex-wrap gap-1">
                        <StatusBadge tone={row.status === "active" ? "success" : "neutral"}>{row.status}</StatusBadge>
                        {Array.from(new Set((row.availability ?? []).map((item) => item.status).filter(Boolean))).map((status) => (
                          <StatusBadge key={status} tone={status === "available" ? "success" : "warning"}>{status}</StatusBadge>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </details>
    </section>
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
  const { mutate: startCrawl, isPending, isSuccess } = useStartCrawl();

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
  const effectiveCountryCode = scopeMode === "all" ? null : countryCode;
  const effectiveCountryName =
    scopeMode === "all"
      ? (lang === "zh" ? "全部国家" : "All countries")
      : countryName;

  const { data: flows, isLoading, error } = useSourcesFlow(effectiveCountryCode);
  const { data: sourceConfigs } = useSourceConfigs(lang);
  const { data: situationSources, isLoading: situationSourcesLoading, error: situationSourcesError } = useSituationSources();
  const refreshSituationSources = useRefreshSituationSources();
  const { data: ontologySeries } = useOntologySeries(
    scopeMode === "selected" ? countryCode : null,
  );
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

  useTaskEventStream({ extraQueryKeys: [["sources-flow"], ["situation-sources"]] });

  const openModal = () => setModalOpen(true);
  const closeModal = () => setModalOpen(false);

  // summary counts
  const totalSources = flows?.length ?? 0;
  const totalSourceSeries = (flows ?? []).reduce(
    (total, flow) => total + (flow.source_series_count ?? 0),
    0,
  );
  const totalSourceObservations = (flows ?? []).reduce(
    (total, flow) => total + (flow.source_observation_count ?? 0),
    0,
  );
  const activeFlows = flows?.filter((f) =>
    f.stages.some((s) => s.status === "running"),
  ).length ?? 0;
  const failedFlows = flows?.filter((f) =>
    f.stages.some((s) => s.status === "failed"),
  ).length ?? 0;
  const completedFlows = flows?.filter((f) =>
    f.stages.some((s) => Boolean(s.status))
    && f.stages.every((s) => !s.status || s.status === "completed"),
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
        items: items.sort(
          (a, b) =>
            (b.source_observation_count ?? b.record_count)
            - (a.source_observation_count ?? a.record_count),
        ),
      }))
      .sort((a, b) => a.countryName.localeCompare(b.countryName));
  }, [flows]);

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_sources")}
        title={t(lang, "flow_title")}
        description={t(lang, "flow_subtitle")}
        meta={
          <>
            <StatusBadge tone={scopeMode === "all" ? "info" : "primary"}>
              {scopeMode === "all" ? (lang === "zh" ? "全部国家" : "All countries") : effectiveCountryName}
            </StatusBadge>
            <StatusBadge tone={failedFlows > 0 ? "danger" : "success"}>
              {failedFlows > 0
                ? lang === "zh"
                  ? `${failedFlows} 个异常流程`
                  : `${failedFlows} failed flows`
                : lang === "zh"
                  ? "无异常流程"
                  : "No failed flows"}
            </StatusBadge>
          </>
        }
        actions={
          countryId && countrySupported ? (
            <button
              onClick={openModal}
              className="flex h-10 items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 text-sm font-medium text-tremor-brand-inverted transition hover:opacity-90 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
            >
              <Plus className="h-4 w-4" />
              {t(lang, "flow_new_crawl_task")}
            </button>
          ) : null
        }
      />

      <section className="app-panel overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-tremor-border p-4 dark:border-dark-tremor-border lg:flex-row lg:items-center lg:justify-between">
          <div>
            <Title className="flex items-center gap-2"><RadioTower className="h-5 w-5 text-tremor-brand" />{lang === "zh" ? "全球态势数据获取" : "Global Situation acquisition"}</Title>
            <Text className="mt-1">{lang === "zh" ? "WHO、ECDC、Africa CDC、PAHO 与 CDC 适配器在这里采集；计算、质量门和历史归档随后自动执行。" : "WHO, ECDC, Africa CDC, PAHO, and CDC adapters are acquired here; calculation, quality gates, and history archival follow automatically."}</Text>
          </div>
          <div className="flex flex-wrap gap-2">
            <button disabled={refreshSituationSources.isPending} onClick={() => refreshSituationSources.mutate("numeric_only")} className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border px-3 text-sm font-medium disabled:opacity-50 dark:border-dark-tremor-border"><RefreshCw className={`h-4 w-4 ${refreshSituationSources.isPending ? "animate-spin" : ""}`} />{lang === "zh" ? "仅重算数值" : "Recalculate only"}</button>
            <button disabled={refreshSituationSources.isPending} onClick={() => refreshSituationSources.mutate("full")} className="inline-flex h-9 items-center gap-2 rounded-tremor-default bg-tremor-brand px-3 text-sm font-medium text-tremor-brand-inverted disabled:opacity-50 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"><Download className="h-4 w-4" />{lang === "zh" ? "采集全部态势来源" : "Acquire all Situation sources"}</button>
          </div>
        </div>
        {refreshSituationSources.isSuccess ? <p className="border-b border-emerald-200 bg-emerald-50 px-4 py-2 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300">{lang === "zh" ? `任务已进入队列：${refreshSituationSources.data.task_uuid}` : `Task queued: ${refreshSituationSources.data.task_uuid}`}</p> : null}
        {refreshSituationSources.error || situationSourcesError ? <p className="border-b border-rose-200 bg-rose-50 px-4 py-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300">{String((refreshSituationSources.error || situationSourcesError) instanceof Error ? (refreshSituationSources.error || situationSourcesError)?.message : (refreshSituationSources.error || situationSourcesError))}</p> : null}
        <div className="grid gap-0 md:grid-cols-2 xl:grid-cols-3">
          {situationSourcesLoading ? <div className="col-span-full p-6 text-sm text-tremor-content-subtle"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />{lang === "zh" ? "加载适配器状态…" : "Loading adapter status…"}</div> : situationSources?.map((source) => {
            const status = source.health?.status || "not_checked";
            const statusTone = status === "fresh" ? "success" : status === "failed" ? "danger" : status === "stale" ? "warning" : "neutral";
            return <article key={source.source_id} className="border-b border-r border-tremor-border p-4 dark:border-dark-tremor-border"><div className="flex items-start justify-between gap-3"><div><p className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{source.label}</p><p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{source.transport} · {source.source_kind.replaceAll("_", " ")}</p></div><div className="text-right"><StatusBadge tone={statusTone}>{status.replaceAll("_", " ")}</StatusBadge>{source.health.from_history ? <p className="mt-1 text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{lang === "zh" ? "历史库最近状态" : "Last known from history"}</p> : null}</div></div><div className="mt-3 flex flex-wrap gap-1">{source.contract.map((field) => <span key={field} className="rounded border border-tremor-border px-1.5 py-0.5 font-mono text-[10px] text-tremor-content dark:border-dark-tremor-border dark:text-dark-tremor-content">{field}</span>)}</div><p className="mt-3 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{lang === "zh" ? "本次检查" : "Checked"}: {source.health.checked_at ? formatDate(source.health.checked_at) : "—"} · {source.health.item_count ?? 0} {lang === "zh" ? "项" : "items"}</p><div className="mt-2 rounded border border-tremor-border bg-tremor-background-subtle p-2 text-xs dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">{source.usage?.mode === "official_event" ? <><p className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{lang === "zh" ? "事件使用" : "Event use"}: {source.usage.in_latest_emerging ?? 0} emerging · {source.usage.used_in_composite_risk ?? 0} risk</p><p className="mt-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{Object.entries(source.usage.persisted ?? {}).map(([key, value]) => `${key} ${value}`).join(" · ") || (lang === "zh" ? "尚无入库事件" : "No persisted events")}</p></> : source.usage?.mode === "numeric_series" ? <><p className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{source.usage.analyzed_count ?? 0}/{source.usage.series_count ?? 0} {lang === "zh" ? "序列已完成五类计算" : "series ran all five methods"}</p><p className="mt-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{source.health.normalized_observation_count ?? 0} {lang === "zh" ? "条规范化观测" : "normalized observations"} · {source.usage.rejected_count ?? 0} rejected</p></> : <p className="text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{lang === "zh" ? "仅作背景指标：" : "Context only: "}{source.usage?.not_analyzed_reason ?? "—"}</p>}</div><p className="mt-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{lang === "zh" ? "最后成功" : "Last success"}: {source.last_success_at ? formatDate(source.last_success_at) : "—"} · stale ≤ {source.stale_policy_hours}h</p>{source.health.error ? <p className="mt-2 line-clamp-2 text-xs text-rose-700 dark:text-rose-300" title={source.health.error}>{source.health.error}</p> : null}<a href={source.url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-tremor-brand hover:underline dark:text-dark-tremor-brand">{lang === "zh" ? "官方端点" : "Official endpoint"}<ExternalLink className="h-3 w-3" /></a></article>;
          })}
        </div>
      </section>

      <FilterToolbar>
        <div className="flex items-center gap-1 rounded-tremor-default border border-tremor-border bg-tremor-background-subtle p-1 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <button
            onClick={() => setScopeMode("selected")}
            className={`h-8 rounded-tremor-default px-3 text-sm font-medium transition ${scopeMode === "selected" ? "bg-tremor-background text-tremor-content-strong dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong" : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"}`}
          >
            {lang === "zh" ? "当前国家 / 地区" : "Selected country / region"}
          </button>
          <button
            onClick={() => setScopeMode("all")}
            className={`h-8 rounded-tremor-default px-3 text-sm font-medium transition ${scopeMode === "all" ? "bg-tremor-background text-tremor-content-strong dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong" : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"}`}
          >
            {lang === "zh" ? "全部国家" : "All countries"}
          </button>
        </div>
      </FilterToolbar>

      {scopeMode === "selected" && countryId && !countrySupported && (
        <div className="app-panel p-4">
          <Text>
            {lang === "zh"
              ? "当前国家的自动爬取工作流尚未设计，flow 仅展示已入库数据来源。"
              : "Automated crawl workflow is not designed for this country yet. Flow currently shows ingested source data only."}
          </Text>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        <MetricTile
          label={lang === "zh" ? "来源序列" : "Source series"}
          value={totalSourceSeries}
          icon={<GitBranch className="h-4 w-4" />}
          tone="primary"
        />
        <MetricTile
          label={lang === "zh" ? "来源观测" : "Source observations"}
          value={totalSourceObservations}
          icon={<Database className="h-4 w-4" />}
          tone="info"
        />
        <MetricTile
          label={t(lang, "flow_kpi_sources")}
          value={totalSources}
          icon={<Download className="h-4 w-4" />}
          tone="info"
        />
        <MetricTile
          label={t(lang, "flow_kpi_active")}
          value={activeFlows}
          icon={<Loader2 className="h-4 w-4" />}
          tone="warning"
        />
        <MetricTile
          label={t(lang, "flow_kpi_completed")}
          value={completedFlows}
          icon={<CheckCircle2 className="h-4 w-4" />}
          tone="success"
        />
        <MetricTile
          label={t(lang, "flow_kpi_failed")}
          value={failedFlows}
          icon={<AlertCircle className="h-4 w-4" />}
          tone={failedFlows > 0 ? "danger" : "success"}
        />
      </div>

      {/* Country guard */}
      {scopeMode === "selected" && !countryId ? (
        <div className="app-panel p-4">
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <GitBranch className="mb-4 h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Title>{t(lang, "flow_select_country")}</Title>
            <Text>{t(lang, "flow_select_country_hint")}</Text>
          </div>
        </div>
      ) : isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-24 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
          ))}
        </div>
      ) : error ? (
        <div className="app-panel p-4">
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
        </div>
      ) : !flows || flows.length === 0 ? (
        <div className="app-panel p-4">
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
        </div>
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
                    key={flowRowKey(flow)}
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
                  key={flowRowKey(flow)}
                  flow={flow}
                  lang={lang}
                />
              ))}
            </>
          )}
        </div>
      )}

      {scopeMode === "selected" && ontologySeries && ontologySeries.length > 0 ? (
        <SeriesMappingRegister rows={ontologySeries} lang={lang} />
      ) : null}

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
