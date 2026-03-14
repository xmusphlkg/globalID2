"use client";

import { useMemo } from "react";
import { Badge, Card, Grid, Text } from "@tremor/react";
import type { Color } from "@tremor/react";
import type { TaskDetail } from "@/lib/hooks/useTasks";

interface TaskDetailPanelProps {
  taskDetail?: TaskDetail;
  detailLoading: boolean;
  emptyMessage?: string;
}

const entryBadge: Record<string, Color> = {
  info: "blue",
  success: "emerald",
  warning: "amber",
  error: "rose",
};

function formatDateTime(value: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function TaskDetailPanel({
  taskDetail,
  detailLoading,
  emptyMessage = "Task detail unavailable.",
}: TaskDetailPanelProps) {
  const timelineEntries = useMemo(() => {
    if (!taskDetail) return [];
    return [...taskDetail.workbook_entries].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
  }, [taskDetail]);

  const timelineStats = useMemo(() => {
    if (!taskDetail) {
      return { aiSteps: 0, totalTokens: 0, totalDuration: 0 };
    }
    const aiSteps = taskDetail.workbook_entries.filter((entry) => !!entry.model_used).length;
    const totalTokens = taskDetail.workbook_entries.reduce((acc, entry) => acc + (entry.tokens_used ?? 0), 0);
    const totalDuration = taskDetail.workbook_entries.reduce((acc, entry) => acc + (entry.duration ?? 0), 0);
    return { aiSteps, totalTokens, totalDuration };
  }, [taskDetail]);

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
      <Grid numItems={2} numItemsLg={4} className="mb-4 gap-3">
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
        <Text className="mb-3 text-xs">{taskDetail.description}</Text>
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
      </div>

      {timelineEntries.length > 0 ? (
        <div className="max-h-[34rem] overflow-y-auto rounded-lg border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          {timelineEntries.map((entry) => (
            <Card key={entry.id} className="mb-2 p-3 last:mb-0">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <Text className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{entry.title}</Text>
                <Badge color={entryBadge[entry.entry_type] ?? "slate"}>{entry.entry_type}</Badge>
                <Text className="text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{formatDateTime(entry.created_at)}</Text>
                {entry.model_used && <Badge color="blue">{entry.model_used}</Badge>}
                {entry.tokens_used != null && <Badge color="slate">{entry.tokens_used} tokens</Badge>}
                {entry.duration != null && <Badge color="slate">{entry.duration.toFixed(1)}s</Badge>}
              </div>

              {entry.content && (
                <details className="mt-2 rounded-md border border-tremor-border px-2 py-1 dark:border-dark-tremor-border">
                  <summary className="cursor-pointer text-[11px] font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    View detail
                  </summary>
                  <p className="mt-2 whitespace-pre-wrap break-words text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                    {entry.content}
                  </p>
                </details>
              )}
            </Card>
          ))}
        </div>
      ) : (
        <div className="rounded-lg bg-tremor-background-subtle px-3 py-2 text-xs text-tremor-content-subtle dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
          No workflow entries recorded yet.
        </div>
      )}
    </>
  );
}
