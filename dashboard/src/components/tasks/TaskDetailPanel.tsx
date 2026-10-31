"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge, Card, Grid, Text } from "@/components/ui/tremor";
import type { Color } from "@/components/ui/tremor";
import { apiFetch } from "@/lib/api";
import type { TaskDetail, WorkbookEntry } from "@/lib/hooks/useTasks";
import { useReportRuns } from "@/lib/hooks/useReports";
import { getSourceDisplayLabel } from "@/lib/source-labels";
import { formatDateTime } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import { compactTaskLogEntries } from "./task-log-compaction";
import { foldConsecutiveEntries, logFieldRows, windowTaskLogs } from "./task-log-display";

interface TaskDetailPanelProps {
  taskDetail?: TaskDetail;
  detailLoading: boolean;
  emptyMessage?: string;
  logDisplayMode?: "default" | "raw-collapsed" | "minimal";
  rawLogLabel?: string;
}

const entryBadge: Record<string, Color> = {
  info: "blue",
  success: "emerald",
  warning: "amber",
  error: "rose",
};

function asDisplayString(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }

  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }

  return null;
}

function extractStructuredRows(value: string | null): Array<{ label: string; value: string }> {
  if (!value) return [];

  const lines = value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const source = lines.length === 1 ? lines[0].split(",").map((part) => part.trim()) : lines;
  const rows = source
    .map((part) => {
      const separator = part.indexOf(":");
      if (separator <= 0 || separator >= part.length - 1) {
        return null;
      }
      return {
        label: part.slice(0, separator).trim(),
        value: part.slice(separator + 1).trim(),
      };
    })
    .filter((row): row is { label: string; value: string } => !!row && !!row.label && !!row.value);

  return rows.length >= 2 ? rows : [];
}

function normalizeStructuredRows(rows: Array<{ label: string; value: string }>) {
  return rows.map((row) => {
    if (row.label.trim().toLowerCase() === "source") {
      return { ...row, value: getSourceDisplayLabel(row.value, "en") };
    }
    return row;
  });
}

function metadataString(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  if (typeof value === "boolean") {
    return value ? "yes" : "no";
  }
  return null;
}

function booleanLabel(value: unknown): string | null {
  if (typeof value === "boolean") return value ? "yes" : "no";
  return null;
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => asDisplayString(item))
    .filter((item): item is string => !!item);
}

function deriveRawArchivePath(taskDetail?: TaskDetail): string | null {
  if (!taskDetail) return null;
  const input = (taskDetail.input_data as Record<string, unknown> | null) ?? null;
  if (!input || input.save_raw !== true) return null;
  const countryCode =
    asDisplayString(input.country_code) ??
    asDisplayString(input.country) ??
    taskDetail.country_code ??
    null;
  if (!countryCode) return null;
  return `data/raw/${countryCode.toLowerCase()}`;
}

function tokenTotal(tokens: Record<string, unknown> | null | undefined): number {
  if (!tokens || typeof tokens !== "object") return 0;
  const total = tokens.total;
  return typeof total === "number" ? total : 0;
}

function qualityOverall(qualityScores: Record<string, unknown> | null | undefined): string {
  if (!qualityScores || typeof qualityScores !== "object") return "-";
  for (const key of ["overall", "quality", "score", "final"]) {
    const value = qualityScores[key];
    if (typeof value === "number") {
      return value.toFixed(2);
    }
  }
  return "-";
}

interface StageSnapshot {
  stage: string;
  attempt: number;
  status: string;
  error?: string | null;
  metrics?: Record<string, unknown> | null;
}

const logCopy = {
  en: {
    attention: "Logs that need attention",
    timeline: "Task log",
    earlier: "Show earlier logs",
    hideEarlier: "Hide earlier logs",
    full: "Show full log",
    less: "Show less",
    empty: "No workflow entries recorded yet.",
    repeats: "repeated",
    stages: "Stages",
    taskError: "Task error",
    folded: "Verbose logs folded",
  },
  zh: {
    attention: "需要处理的日志",
    timeline: "任务日志",
    earlier: "显示更早的日志",
    hideEarlier: "收起更早的日志",
    full: "展开完整日志",
    less: "收起",
    empty: "还没有任务日志。",
    repeats: "重复",
    stages: "阶段",
    taskError: "任务错误",
    folded: "已折叠的重复输出",
  },
} as const;

function latestStageSnapshots(stages: StageSnapshot[]): StageSnapshot[] {
  if (!stages.length) return [];
  const attempt = Math.max(...stages.map((stage) => stage.attempt));
  const order = new Map(["collect", "organize", "store"].map((name, index) => [name, index]));
  return stages
    .filter((stage) => stage.attempt === attempt)
    .sort((left, right) => (order.get(left.stage) ?? 99) - (order.get(right.stage) ?? 99) || left.stage.localeCompare(right.stage));
}

function LogBody({ content, expandLabel, collapseLabel }: { content: string; expandLabel: string; collapseLabel: string }) {
  const [open, setOpen] = useState(false);
  const lines = content.split("\n");
  const long = content.length > 1800 || lines.length > 30;
  const shown = !long || open ? content : lines.length > 30 ? lines.slice(0, 30).join("\n") : content.slice(0, 1800);
  return (
    <div className="min-w-0">
      <pre className="whitespace-pre-wrap break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">{shown}</pre>
      {long ? (
        <button type="button" className="mt-1 text-xs font-medium text-orange-800 underline dark:text-orange-300" onClick={() => setOpen((current) => !current)}>
          {open ? collapseLabel : expandLabel}
        </button>
      ) : null}
    </div>
  );
}

function TaskLogEntry({
  entry,
  repeats,
  countryCode,
  repeatsLabel,
  expandLabel,
  collapseLabel,
}: {
  entry: WorkbookEntry;
  repeats: number;
  countryCode?: string | null;
  repeatsLabel: string;
  expandLabel: string;
  collapseLabel: string;
}) {
  const content = entry.content?.trim() ? entry.content : null;
  const fields = logFieldRows(content).map((row) =>
    row.label.trim().toLowerCase() === "source"
      ? { ...row, value: getSourceDisplayLabel(row.value, "en", countryCode) }
      : row,
  );
  const metadata = entry.metadata || {};
  const metadataDisease = metadataString(metadata.disease_name);
  const metadataSection = metadataString(metadata.section_type);
  const metadataProvider = metadataString(metadata.provider);
  const attention = entry.entry_type === "error" || entry.success === false;
  return (
    <article className={`mb-2 min-w-0 rounded-md border px-3 py-2 last:mb-0 ${attention ? "border-rose-200 bg-rose-50/70 dark:border-rose-900/50 dark:bg-rose-950/20" : "border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background"}`}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
        <span className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{entry.title}</span>
        <Badge color={entryBadge[entry.entry_type] ?? "slate"}>{entry.entry_type}</Badge>
        {repeats > 1 ? <Badge color="slate">{repeatsLabel} {repeats}</Badge> : null}
        {metadataDisease ? <Badge color="violet">{metadataDisease}</Badge> : null}
        {metadataSection ? <Badge color="blue">{metadataSection}</Badge> : null}
        {metadataProvider ? <Badge color="cyan">{metadataProvider}</Badge> : null}
        {entry.model_used ? <Badge color="blue">{entry.model_used}</Badge> : null}
        {entry.tokens_used != null ? <Badge color="slate">{entry.tokens_used} tokens</Badge> : null}
        {entry.duration != null ? <Badge color="slate">{entry.duration.toFixed(1)}s</Badge> : null}
        <span className="text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{formatDateTime(entry.created_at)}</span>
      </div>
      {fields.length > 0 ? (
        <dl className="mt-2 grid gap-2 sm:grid-cols-2">
          {fields.map((row, index) => (
            <div key={`${entry.id}-${index}`} className="min-w-0">
              <dt className="text-[10px] uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{row.label}</dt>
              <dd className="break-words text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : content ? (
        <div className="mt-2"><LogBody content={content} expandLabel={expandLabel} collapseLabel={collapseLabel} /></div>
      ) : null}
      {(entry.prompt || entry.response) && (
        <details className="mt-2 rounded-md border border-tremor-border px-2 py-1 dark:border-dark-tremor-border">
          <summary className="cursor-pointer text-[11px] font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">View AI payload</summary>
          {entry.prompt ? <pre className="mt-2 whitespace-pre-wrap break-all font-mono text-xs">{entry.prompt}</pre> : null}
          {entry.response ? <pre className="mt-2 whitespace-pre-wrap break-all font-mono text-xs">{entry.response}</pre> : null}
        </details>
      )}
    </article>
  );
}

function runStatusColor(status: string): Color {
  const normalized = status.toLowerCase();
  if (normalized === "completed") return "emerald";
  if (normalized === "running") return "amber";
  if (normalized === "failed") return "rose";
  if (normalized === "cancelled") return "slate";
  return "blue";
}

export function TaskDetailPanel({
  taskDetail,
  detailLoading,
  emptyMessage = "Task detail unavailable.",
  logDisplayMode = "default",
  rawLogLabel = "View raw log",
}: TaskDetailPanelProps) {
  const outputData = (taskDetail?.output_data as Record<string, unknown> | null) ?? null;
  const compactedTimeline = useMemo(() => {
    if (!taskDetail) return { entries: [], hiddenCount: 0 };
    const sortedEntries = [...taskDetail.workbook_entries].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
    return compactTaskLogEntries(sortedEntries);
  }, [taskDetail]);
  const timelineEntries = compactedTimeline.entries;
  const lang = useAppStore((state) => state.lang);
  const copy = logCopy[lang];
  const [showEarlier, setShowEarlier] = useState(false);
  useEffect(() => {
    setShowEarlier(false);
  }, [taskDetail?.task_uuid]);
  const logWindow = useMemo(() => windowTaskLogs(timelineEntries), [timelineEntries]);
  const routineEntries = showEarlier ? [...logWindow.earlier, ...logWindow.recent] : logWindow.recent;
  const attentionGroups = useMemo(() => foldConsecutiveEntries(logWindow.pinned), [logWindow.pinned]);
  const routineGroups = useMemo(() => foldConsecutiveEntries(routineEntries), [routineEntries]);
  const stageQuery = useQuery({
    queryKey: ["collection-run-stages", taskDetail?.task_uuid],
    enabled: Boolean(taskDetail?.task_uuid),
    retry: false,
    staleTime: 15_000,
    queryFn: () => apiFetch<{ stages?: StageSnapshot[] }>(`/collection/runs/${encodeURIComponent(taskDetail!.task_uuid)}`),
  });
  const stageSnapshots = useMemo(
    () => latestStageSnapshots(stageQuery.data?.stages ?? []),
    [stageQuery.data?.stages],
  );

  const timelineStats = useMemo(() => {
    if (!taskDetail) {
      return { aiSteps: 0, totalTokens: 0, totalDuration: 0 };
    }
    const aiSteps = taskDetail.workbook_entries.filter((entry) => !!entry.model_used).length;
    const totalTokens = taskDetail.workbook_entries.reduce((acc, entry) => acc + (entry.tokens_used ?? 0), 0);
    const totalDuration = taskDetail.workbook_entries.reduce((acc, entry) => acc + (entry.duration ?? 0), 0);
    return { aiSteps, totalTokens, totalDuration };
  }, [taskDetail]);

  const descriptionRows = normalizeStructuredRows(extractStructuredRows(taskDetail?.description ?? null));
  const crawlInput = (taskDetail?.input_data as Record<string, unknown> | null) ?? null;
  const crawlConfigRows = useMemo(() => {
    if (!taskDetail || taskDetail.task_type !== "crawl_data" || !crawlInput) return [];
    const rows: Array<{ label: string; value: string }> = [];
    const source = asDisplayString(crawlInput.source);
    const saveRaw = booleanLabel(crawlInput.save_raw);
    const fillMissing = booleanLabel(crawlInput.fill_missing);
    const process = booleanLabel(crawlInput.process);
    const force = booleanLabel(crawlInput.force);
    const startYear = asDisplayString(crawlInput.start_year);
    const sourceFile = asDisplayString(crawlInput.source_file);
    const sourceDir = asDisplayString(crawlInput.source_dir);
    const rawArchivePath = deriveRawArchivePath(taskDetail);

    if (source) rows.push({ label: "Source", value: getSourceDisplayLabel(source, "en", taskDetail.country_code) });
    if (startYear) rows.push({ label: "History Start Year", value: startYear });
    if (sourceFile) rows.push({ label: "Source File", value: sourceFile });
    if (sourceDir) rows.push({ label: "Source Dir", value: sourceDir });
    if (saveRaw) rows.push({ label: "Save Raw", value: saveRaw });
    if (rawArchivePath) rows.push({ label: "Raw Archive", value: rawArchivePath });
    if (fillMissing) rows.push({ label: "Fill Missing", value: fillMissing });
    if (process) rows.push({ label: "Process", value: process });
    if (force) rows.push({ label: "Force", value: force });
    return rows;
  }, [crawlInput, taskDetail]);

  const diseaseKnowledgeRows = useMemo(() => {
    if (!taskDetail || taskDetail.task_type !== "update_disease_knowledge" || !crawlInput) return [];
    const rows: Array<{ label: string; value: string }> = [];
    const diseaseId = asDisplayString(crawlInput.disease_id);
    const diseaseIds = asStringList(crawlInput.disease_ids);
    const sourceGroups = asStringList(crawlInput.source_groups ?? crawlInput.source);
    const generator = asDisplayString(crawlInput.generator);
    const force = booleanLabel(crawlInput.force);
    const dryRun = booleanLabel(crawlInput.dry_run);

    if (diseaseId) rows.push({ label: "Disease", value: diseaseId });
    if (diseaseIds.length > 0) rows.push({ label: "Disease IDs", value: diseaseIds.join(", ") });
    if (sourceGroups.length > 0) rows.push({ label: "Source Groups", value: sourceGroups.join(", ") });
    if (generator) rows.push({ label: "Generator", value: generator });
    if (force) rows.push({ label: "Force", value: force });
    if (dryRun) rows.push({ label: "Dry Run", value: dryRun });
    return rows;
  }, [crawlInput, taskDetail]);

  const reportUuid = asDisplayString(outputData?.report_uuid);
  const reportId = asDisplayString(taskDetail?.report_id) ?? asDisplayString(outputData?.report_id);
  const { data: reportRuns } = useReportRuns(reportUuid);

  const runsByDisease = useMemo(() => {
    const grouped = new Map<string, NonNullable<typeof reportRuns>>();
    (reportRuns ?? []).forEach((run) => {
      const diseaseName = run.disease_name || "General";
      const current = grouped.get(diseaseName) ?? [];
      current.push(run);
      grouped.set(diseaseName, current);
    });
    return Array.from(grouped.entries()).map(([diseaseName, runs]) => ({
      diseaseName,
      runs: [...runs].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
    }));
  }, [reportRuns]);

  const runSummary = useMemo(() => {
    const total = reportRuns?.length ?? 0;
    const completed = (reportRuns ?? []).filter((run) => run.status === "completed").length;
    const running = (reportRuns ?? []).filter((run) => run.status === "running").length;
    const failed = (reportRuns ?? []).filter((run) => run.status === "failed").length;
    return { total, completed, running, failed };
  }, [reportRuns]);

  if (detailLoading && !taskDetail) {
    return (
      <div className="space-y-4">
        <div className="h-5 w-2/5 animate-pulse rounded bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
        <div className="h-20 w-full animate-pulse rounded bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
      </div>
    );
  }

  if (!taskDetail) {
    return (
      <div className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {emptyMessage}
      </div>
    );
  }

  return (
    <>
      {logDisplayMode !== "minimal" && <Grid numItems={2} numItemsLg={3} className="mb-4 gap-3">
        <Card className="p-3">
          <Text>Task UUID</Text>
          <Text className="mt-1 break-all font-mono text-xs font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {taskDetail.task_uuid}
          </Text>
        </Card>
        {reportUuid && <Card className="p-3">
          <Text>Report UUID</Text>
          <Text className="mt-1 break-all font-mono text-xs font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {reportUuid}
          </Text>
        </Card>}
        {reportId && <Card className="p-3">
          <Text>Report ID</Text>
          <Text className="mt-1 font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{reportId}</Text>
        </Card>}
        <Card className="p-3">
          <Text>Priority</Text>
          <Text className="mt-1 font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{taskDetail.priority}</Text>
        </Card>
        <Card className="p-3">
          <Text>Duration</Text>
          <Text className="mt-1 font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {taskDetail.actual_duration ? `${taskDetail.actual_duration.toFixed(1)}s` : "-"}
          </Text>
        </Card>
        <Card className="p-3">
          <Text>Started</Text>
          <Text className="mt-1 font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {formatDateTime(taskDetail.started_at)}
          </Text>
        </Card>
        <Card className="p-3">
          <Text>Completed</Text>
          <Text className="mt-1 font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {formatDateTime(taskDetail.completed_at)}
          </Text>
        </Card>
      </Grid>}

      {taskDetail.description && (
        descriptionRows.length > 0 ? (
          <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-tremor-content">
            {descriptionRows.map((row) => (
              <span key={`${row.label}-${row.value}`}>
                {row.label}: {row.value}
              </span>
            ))}
          </div>
        ) : (
          <Text className="mb-3 text-xs">{taskDetail.description}</Text>
        )
      )}

      {crawlConfigRows.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-tremor-content">
          {crawlConfigRows.map((row) => (
            <span key={`${row.label}-${row.value}`}>
              {row.label}: {row.value}
            </span>
          ))}
        </div>
      )}

      {diseaseKnowledgeRows.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-tremor-content">
          {diseaseKnowledgeRows.map((row) => (
            <span key={`${row.label}-${row.value}`}>
              {row.label}: {row.value}
            </span>
          ))}
        </div>
      )}

      {taskDetail.cancel_requested && taskDetail.status === "running" && (
        <Card className="mb-3 border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-900/30 dark:bg-amber-950/30">
          <Text className="text-xs font-medium text-amber-700 dark:text-amber-300">
            Cancellation has been requested. The current AI call will stop at the next safe checkpoint.
          </Text>
        </Card>
      )}

      {taskDetail.task_type === "generate_report" && ["cancelled", "failed"].includes(taskDetail.status) && (
        <Card className="mb-3 border-sky-200 bg-sky-50 px-3 py-2 dark:border-sky-900/30 dark:bg-sky-950/30">
          <Text className="text-xs font-medium text-sky-700 dark:text-sky-300">
            Re-running the same scope will resume this partial report and reuse the sections that were already generated instead of starting from zero.
          </Text>
        </Card>
      )}

      {taskDetail.last_error && !logWindow.pinned.some((entry) => (entry.content ?? "").includes(taskDetail.last_error!)) && (
        <div className="mb-3 min-w-0 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 dark:border-rose-900/40 dark:bg-rose-950/30">
          <p className="text-[11px] font-medium uppercase text-rose-700 dark:text-rose-300">{copy.taskError}</p>
          <div className="mt-1"><LogBody content={taskDetail.last_error} expandLabel={rawLogLabel === "View raw log" ? copy.full : rawLogLabel} collapseLabel={copy.less} /></div>
        </div>
      )}

      {stageSnapshots.length > 0 && (
        <section className="mb-3 min-w-0 space-y-2">
          <h3 className="text-xs font-medium uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{copy.stages}</h3>
          {stageSnapshots.map((stage) => (
            <article key={`${stage.stage}-${stage.attempt}`} className={`min-w-0 rounded-md border px-3 py-2 ${stage.status === "failed" ? "border-rose-200 bg-rose-50/70 dark:border-rose-900/50 dark:bg-rose-950/20" : "border-tremor-border dark:border-dark-tremor-border"}`}>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-semibold">{stage.stage}</span>
                <Badge color={stage.status === "failed" ? "rose" : stage.status === "completed" ? "emerald" : "slate"}>{stage.status}</Badge>
                <span className="text-tremor-content-subtle">#{stage.attempt}</span>
              </div>
              {stage.error ? <div className="mt-2"><LogBody content={stage.error} expandLabel={copy.full} collapseLabel={copy.less} /></div> : null}
            </article>
          ))}
        </section>
      )}

      <div className="mb-3 flex flex-wrap gap-2">
        <Badge color="blue">Workflow Steps: {timelineEntries.length}</Badge>
        <Badge color="slate">AI Steps: {timelineStats.aiSteps}</Badge>
        <Badge color="slate">Tokens: {timelineStats.totalTokens}</Badge>
        <Badge color="slate">Duration: {timelineStats.totalDuration.toFixed(1)}s</Badge>
        {compactedTimeline.hiddenCount > 0 && (
          <Badge color="slate">{copy.folded}: {compactedTimeline.hiddenCount}</Badge>
        )}
        {reportRuns && reportRuns.length > 0 && <Badge color="violet">Section Runs: {runSummary.total}</Badge>}
      </div>

      {runsByDisease.length > 0 && (
        <Card className="mb-3 border-tremor-border/80 bg-white/80 px-3 py-3 dark:border-dark-tremor-border/80 dark:bg-white/5">
          <div className="flex flex-wrap items-center gap-2">
            <Text className="text-xs font-medium uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              Section Execution
            </Text>
            <Badge color="emerald">completed {runSummary.completed}</Badge>
            <Badge color="amber">running {runSummary.running}</Badge>
            <Badge color="rose">failed {runSummary.failed}</Badge>
          </div>
          <div className="mt-3 space-y-3">
            {runsByDisease.map((group) => (
              <div key={group.diseaseName} className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                <div className="flex flex-wrap items-center gap-2">
                  <Text className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{group.diseaseName}</Text>
                  <Badge color="slate">{group.runs.length} runs</Badge>
                </div>
                <div className="mt-3 grid gap-2 xl:grid-cols-2">
                  {group.runs.map((run) => (
                    <div key={run.id} className="rounded-md border border-tremor-border bg-tremor-background-subtle px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
                      <div className="flex flex-wrap items-center gap-2 text-[11px]">
                        <Text className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{run.section_type || "section"}</Text>
                        <Badge color={runStatusColor(run.status)}>{run.status}</Badge>
                        {run.model && <Badge color="blue">{run.model}</Badge>}
                        {run.provider && <Badge color="slate">{run.provider}</Badge>}
                      </div>
                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        <div>
                          <Text className="text-[10px] uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Tokens</Text>
                          <Text className="text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{tokenTotal(run.token_usage)}</Text>
                        </div>
                        <div>
                          <Text className="text-[10px] uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Quality</Text>
                          <Text className="text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{qualityOverall(run.quality_scores)}</Text>
                        </div>
                        <div>
                          <Text className="text-[10px] uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Revisions</Text>
                          <Text className="text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{run.revision_count}</Text>
                        </div>
                        <div>
                          <Text className="text-[10px] uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Started</Text>
                          <Text className="text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{formatDateTime(run.started_at)}</Text>
                        </div>
                      </div>
                      {run.error_message && (
                        <Text className="mt-2 text-xs text-rose-700 dark:text-rose-300">{run.error_message}</Text>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {attentionGroups.length > 0 && (
        <section className="mb-4 min-w-0">
          <h3 className="mb-2 text-xs font-medium uppercase text-rose-700 dark:text-rose-300">{copy.attention}</h3>
          {attentionGroups.map((group) => (
            <TaskLogEntry
              key={group.entry.id}
              entry={group.entry}
              repeats={group.repeats}
              countryCode={taskDetail.country_code}
              repeatsLabel={copy.repeats}
              expandLabel={rawLogLabel === "View raw log" ? copy.full : rawLogLabel}
              collapseLabel={copy.less}
            />
          ))}
        </section>
      )}

      <section className="min-w-0">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-medium uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{copy.timeline}</h3>
          {logWindow.earlier.length > 0 && (
            <button type="button" className="text-xs font-medium text-orange-800 underline dark:text-orange-300" onClick={() => setShowEarlier((current) => !current)}>
              {showEarlier ? copy.hideEarlier : `${copy.earlier} (${logWindow.earlier.length})`}
            </button>
          )}
        </div>
        {routineGroups.length > 0 ? routineGroups.map((group) => (
          <TaskLogEntry
            key={group.entry.id}
            entry={group.entry}
            repeats={group.repeats}
            countryCode={taskDetail.country_code}
            repeatsLabel={copy.repeats}
            expandLabel={rawLogLabel === "View raw log" ? copy.full : rawLogLabel}
            collapseLabel={copy.less}
          />
        )) : attentionGroups.length === 0 ? (
          <div className="rounded-md bg-tremor-background-subtle px-3 py-2 text-xs text-tremor-content-subtle dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
            {copy.empty}
          </div>
        ) : null}
      </section>
    </>
  );
}
