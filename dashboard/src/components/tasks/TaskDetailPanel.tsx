"use client";

import { useMemo } from "react";
import { Badge, Card, Grid, Text } from "@/components/ui/tremor";
import type { Color } from "@/components/ui/tremor";
import type { TaskDetail } from "@/lib/hooks/useTasks";
import { useReportRuns } from "@/lib/hooks/useReports";
import { getSourceDisplayLabel } from "@/lib/source-labels";
import { formatDateTime } from "@/lib/utils";
import { compactTaskLogEntries } from "./task-log-compaction";

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

function summarizeContent(value: string | null): string | null {
  if (!value) return null;
  const firstLine = value
    .split("\n")
    .map((line) => line.trim())
    .find(Boolean);
  return firstLine || null;
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

function entryKind(title: string): "phase" | "progress" | "result" | "default" {
  if (/^phase\s+\d+\/\d+/i.test(title)) return "phase";
  if (/progress/i.test(title)) return "progress";
  if (/complete|completed|reused|resuming/i.test(title)) return "result";
  return "default";
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

function workflowStageLabel(value: string | null): string | null {
  if (!value) return null;
  return value.replace(/_/g, " ");
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

  const timelineStats = useMemo(() => {
    if (!taskDetail) {
      return { aiSteps: 0, totalTokens: 0, totalDuration: 0 };
    }
    const aiSteps = taskDetail.workbook_entries.filter((entry) => !!entry.model_used).length;
    const totalTokens = taskDetail.workbook_entries.reduce((acc, entry) => acc + (entry.tokens_used ?? 0), 0);
    const totalDuration = taskDetail.workbook_entries.reduce((acc, entry) => acc + (entry.duration ?? 0), 0);
    return { aiSteps, totalTokens, totalDuration };
  }, [taskDetail]);

  const latestEntry = timelineEntries[timelineEntries.length - 1];
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
      <Grid numItems={2} numItemsLg={3} className="mb-4 gap-3">
        <Card className="p-3">
          <Text>Task UUID</Text>
          <Text className="mt-1 break-all font-mono text-xs font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {taskDetail.task_uuid}
          </Text>
        </Card>
        <Card className="p-3">
          <Text>Report UUID</Text>
          <Text className="mt-1 break-all font-mono text-xs font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {reportUuid ?? "-"}
          </Text>
        </Card>
        <Card className="p-3">
          <Text>Report ID</Text>
          <Text className="mt-1 font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{reportId ?? "-"}</Text>
        </Card>
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
      </Grid>

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

      {taskDetail.last_error && (
        <Card className="mb-3 border-rose-200 bg-rose-50 px-3 py-2 dark:border-rose-900/30 dark:bg-rose-950/30">
          <Text className="text-xs font-medium text-rose-700 dark:text-rose-300">{taskDetail.last_error}</Text>
        </Card>
      )}

      <div className="mb-3 flex flex-wrap gap-2">
        <Badge color="blue">Workflow Steps: {timelineEntries.length}</Badge>
        <Badge color="slate">AI Steps: {timelineStats.aiSteps}</Badge>
        <Badge color="slate">Tokens: {timelineStats.totalTokens}</Badge>
        <Badge color="slate">Duration: {timelineStats.totalDuration.toFixed(1)}s</Badge>
        {compactedTimeline.hiddenCount > 0 && (
          <Badge color="slate">Verbose logs folded: {compactedTimeline.hiddenCount}</Badge>
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

      {latestEntry && (
        <Card className="mb-3 border-tremor-border/80 bg-white/80 px-3 py-3 dark:border-dark-tremor-border/80 dark:bg-white/5">
          <div className="flex flex-wrap items-center gap-2">
            <Text className="text-xs font-medium uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              Latest Update
            </Text>
            <Badge color={entryBadge[latestEntry.entry_type] ?? "slate"}>{latestEntry.entry_type}</Badge>
            <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{formatDateTime(latestEntry.created_at)}</Text>
          </div>
          <Text className="mt-2 font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{latestEntry.title}</Text>
          {summarizeContent(latestEntry.content) && (
            <Text className="mt-1 text-sm text-tremor-content dark:text-dark-tremor-content">{summarizeContent(latestEntry.content)}</Text>
          )}
        </Card>
      )}

      {timelineEntries.length > 0 ? (
        <div className="max-h-[34rem] overflow-y-auto rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          {timelineEntries.map((entry, index) => {
            const content = entry.content?.trim() ? entry.content : null;
            const preview = logDisplayMode === "default" ? summarizeContent(content) : null;
            const structuredRows = logDisplayMode === "default" ? extractStructuredRows(content) : [];
            const kind = entryKind(entry.title);
            const rawDetail =
              logDisplayMode === "minimal" || logDisplayMode === "raw-collapsed"
                ? Boolean(content)
                : content !== null && (!preview || content.trim() !== preview || structuredRows.length === 0);
            const metadata = entry.metadata || {};
            const metadataDisease = metadataString(metadata.disease_name);
            const metadataSection = metadataString(metadata.section_type);
            const metadataEvent = metadataString(metadata.event);
            const metadataProvider = metadataString(metadata.provider);
            const metadataWorkflowStage = workflowStageLabel(metadataString(metadata.workflow_stage));

            return (
              <Card
                key={entry.id}
                className="mb-2 p-3 last:mb-0"
              >
                <div className="flex items-start gap-3">
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-tremor-background-muted text-[11px] font-semibold text-tremor-content dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content">
                    {index + 1}
                  </div>
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="flex flex-wrap items-center gap-2 text-[11px]">
                      <Text className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{entry.title}</Text>
                      <Badge color={entryBadge[entry.entry_type] ?? "slate"}>{entry.entry_type}</Badge>
                      {metadataDisease && <Badge color="violet">{metadataDisease}</Badge>}
                      {metadataSection && <Badge color="blue">{metadataSection}</Badge>}
                      {metadataEvent && <Badge color="slate">{metadataEvent}</Badge>}
                      {metadataWorkflowStage && <Badge color="indigo">{metadataWorkflowStage}</Badge>}
                      {kind === "phase" && <Badge color="violet">phase</Badge>}
                      {kind === "progress" && <Badge color="amber">progress</Badge>}
                      {kind === "result" && <Badge color="emerald">result</Badge>}
                      <Text className="text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{formatDateTime(entry.created_at)}</Text>
                      {metadataProvider && <Badge color="cyan">{metadataProvider}</Badge>}
                      {entry.model_used && <Badge color="blue">{entry.model_used}</Badge>}
                      {entry.tokens_used != null && <Badge color="slate">{entry.tokens_used} tokens</Badge>}
                      {entry.duration != null && <Badge color="slate">{entry.duration.toFixed(1)}s</Badge>}
                    </div>

                    {preview && structuredRows.length === 0 && logDisplayMode !== "minimal" && (
                      <Text className="text-sm text-tremor-content dark:text-dark-tremor-content">{preview}</Text>
                    )}

                    {structuredRows.length > 0 && logDisplayMode !== "minimal" && (
                      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                        {structuredRows.map((row, rowIndex) => (
                          <div key={`${entry.id}-${rowIndex}-${row.label}`} className="rounded-md border border-tremor-border bg-tremor-background px-2 py-1.5 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                            <Text className="text-[10px] uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{row.label}</Text>
                            <Text className="mt-1 text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{row.value}</Text>
                          </div>
                        ))}
                      </div>
                    )}

                    {rawDetail && (
                      <details className="rounded-md border border-tremor-border px-2 py-1 dark:border-dark-tremor-border">
                        <summary className="cursor-pointer text-[11px] font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {rawLogLabel}
                        </summary>
                        <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                          {content}
                        </pre>
                      </details>
                    )}

                    {(entry.prompt || entry.response) && (
                      <details className="rounded-md border border-tremor-border px-2 py-1 dark:border-dark-tremor-border">
                        <summary className="cursor-pointer text-[11px] font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          View AI payload
                        </summary>
                        {entry.prompt && (
                          <div className="mt-2">
                            <Text className="text-[10px] uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Prompt</Text>
                            <p className="mt-1 whitespace-pre-wrap break-words text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                              {entry.prompt}
                            </p>
                          </div>
                        )}
                        {entry.response && (
                          <div className="mt-2">
                            <Text className="text-[10px] uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Response</Text>
                            <p className="mt-1 whitespace-pre-wrap break-words text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                              {entry.response}
                            </p>
                          </div>
                        )}
                      </details>
                    )}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      ) : (
        <div className="rounded-tremor-default bg-tremor-background-subtle px-3 py-2 text-xs text-tremor-content-subtle dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
          No workflow entries recorded yet.
        </div>
      )}
    </>
  );
}
