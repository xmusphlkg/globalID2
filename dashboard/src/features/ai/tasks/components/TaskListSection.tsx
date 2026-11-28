"use client";

import Link from "next/link";
import {
  Ban,
  ChevronDown,
  Cpu,
  GitBranch,
  Loader2,
  MessageSquareText,
} from "lucide-react";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { Badge, Card, Text } from "@/components/ui/tremor";
import type { TaskDetail, TaskItem } from "@/features/operations/tasks/api";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/utils";
import {
  TASK_PAGE_SIZE,
  buildTaskInteractionsHref,
  canCancelTask,
  canRetryTask,
  statusBadge,
} from "../helpers";

interface TaskListSectionProps {
  lang: "en" | "zh";
  isLoading: boolean;
  tasks: TaskItem[];
  totalCount: number;
  page: number;
  totalPages: number;
  visibleStart: number;
  visibleEnd: number;
  expandedUuid: string | null;
  taskDetail: TaskDetail | undefined;
  detailLoading: boolean;
  activeTaskReportUuid: string | null;
  executingTask: boolean;
  cancellingTask: boolean;
  onToggleExpand: (taskUuid: string) => void;
  onExecuteTask: (taskUuid: string) => void;
  onCancelTask: (taskUuid: string) => void;
  onPreviousPage: () => void;
  onNextPage: () => void;
}

export function TaskListSection({
  lang,
  isLoading,
  tasks,
  totalCount,
  page,
  totalPages,
  visibleStart,
  visibleEnd,
  expandedUuid,
  taskDetail,
  detailLoading,
  activeTaskReportUuid,
  executingTask,
  cancellingTask,
  onToggleExpand,
  onExecuteTask,
  onCancelTask,
  onPreviousPage,
  onNextPage,
}: TaskListSectionProps) {
  return (
    <>
      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-16 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted"
            />
          ))}
        </div>
      ) : tasks.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <Cpu className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Text className="mt-3">{t(lang, "no_data")}</Text>
          </div>
        </Card>
      ) : (
        <div className="space-y-4">
          {tasks.map((task) => {
            const isExpanded = expandedUuid === task.task_uuid;
            return (
              <Card key={task.task_uuid} className="overflow-hidden p-0">
                <button
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-[13px] transition-colors"
                  style={{
                    background: isExpanded
                      ? "var(--color-tremor-background-subtle)"
                      : "transparent",
                  }}
                  onClick={() => onToggleExpand(task.task_uuid)}
                >
                  <Badge color={statusBadge[task.status] ?? "slate"}>{task.status}</Badge>
                  {task.cancel_requested && task.status === "running" && (
                    <Badge color="amber">cancelling</Badge>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                      {task.task_name}
                    </div>
                    <div className="truncate font-mono text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      UUID: {task.task_uuid}
                    </div>
                  </div>
                  <Badge color="slate">{task.task_type}</Badge>
                  <div className="w-24">
                    <div className="h-2 overflow-hidden rounded-full bg-tremor-background-muted dark:bg-dark-tremor-background-muted">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${task.progress}%`,
                          background:
                            task.progress === 100 ? CHART_TOKENS.success : CHART_TOKENS.primary,
                        }}
                      />
                    </div>
                  </div>
                  <span
                    className="w-9 text-right text-[11px] font-medium"
                    style={{ color: "var(--color-tremor-content-subtle)" }}
                  >
                    {task.progress}%
                  </span>
                  <span
                    className="hidden w-24 text-right text-[11px] lg:block"
                    style={{ color: "var(--color-tremor-content-subtle)" }}
                  >
                    {formatDateTime(task.created_at)}
                  </span>
                  <ChevronDown
                    className={`h-3.5 w-3.5 transition-transform ${isExpanded ? "rotate-180" : ""}`}
                    style={{ color: "var(--color-tremor-content-subtle)" }}
                  />
                </button>

                {isExpanded && (
                  <div className="border-t border-tremor-border px-4 py-3 text-[13px] dark:border-dark-tremor-border">
                    <div className="mb-3">
                      <Link
                        href={buildTaskInteractionsHref(task.task_uuid, activeTaskReportUuid)}
                        className="inline-flex items-center gap-1 rounded-tremor-default border border-blue-300/70 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition hover:bg-blue-100 dark:border-blue-900 dark:bg-blue-950/25 dark:text-blue-300"
                      >
                        <MessageSquareText className="h-3.5 w-3.5" />
                        View this task in chat workflow
                      </Link>
                    </div>
                    <div className="mb-3 flex flex-wrap gap-2">
                      {task.task_type === "agent_workflow" && (
                        <Link
                          href={`/production/runs?task_uuid=${encodeURIComponent(task.task_uuid)}`}
                          className="inline-flex items-center gap-1 rounded-tremor-default border border-cyan-300/70 bg-cyan-50 px-3 py-1.5 text-xs font-medium text-cyan-700 transition hover:bg-cyan-100 dark:border-cyan-900 dark:bg-cyan-950/25 dark:text-cyan-300"
                        >
                          <GitBranch className="h-3.5 w-3.5" />
                          {lang === "zh" ? "查看 Agent Run" : "View Agent Run"}
                        </Link>
                      )}
                      {canRetryTask(task.status) && (
                        <button
                          onClick={() => onExecuteTask(task.task_uuid)}
                          disabled={executingTask}
                          className="inline-flex items-center gap-1 rounded-tremor-default border border-amber-300/70 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-300"
                        >
                          {executingTask ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Cpu className="h-3.5 w-3.5" />
                          )}
                          {lang === "zh" ? "重试任务" : "Retry Task"}
                        </button>
                      )}
                      {canCancelTask(task.status) && (
                        <button
                          onClick={() => onCancelTask(task.task_uuid)}
                          disabled={cancellingTask || task.cancel_requested}
                          className="inline-flex items-center gap-1 rounded-tremor-default border border-rose-300/70 bg-rose-50 px-3 py-1.5 text-xs font-medium text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-rose-900 dark:bg-rose-950/25 dark:text-rose-300"
                        >
                          {cancellingTask ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Ban className="h-3.5 w-3.5" />
                          )}
                          {task.cancel_requested
                            ? lang === "zh"
                              ? "取消请求已发送"
                              : "Cancellation Requested"
                            : lang === "zh"
                              ? "取消任务"
                              : "Cancel Task"}
                        </button>
                      )}
                    </div>
                    <TaskDetailPanel
                      taskDetail={taskDetail}
                      detailLoading={detailLoading}
                      emptyMessage="Task detail unavailable."
                    />
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {totalCount > TASK_PAGE_SIZE && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh"
                ? `每页 ${TASK_PAGE_SIZE} 条，当前显示 ${visibleStart}-${visibleEnd}`
                : `${TASK_PAGE_SIZE} per page, currently showing ${visibleStart}-${visibleEnd}`}
            </Text>
            <div className="flex items-center gap-2">
              <button
                onClick={onPreviousPage}
                disabled={page <= 1}
                className="rounded-tremor-default border border-tremor-border px-3 py-1.5 text-sm text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              >
                {lang === "zh" ? "上一页" : "Previous"}
              </button>
              <span className="min-w-[96px] text-center text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {page} / {totalPages}
              </span>
              <button
                onClick={onNextPage}
                disabled={page >= totalPages}
                className="rounded-tremor-default border border-tremor-border px-3 py-1.5 text-sm text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              >
                {lang === "zh" ? "下一页" : "Next"}
              </button>
            </div>
          </div>
        </Card>
      )}
    </>
  );
}
