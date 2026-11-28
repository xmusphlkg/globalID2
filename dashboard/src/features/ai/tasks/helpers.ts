import { CHART_TOKENS } from "@/lib/chart-theme";
import type { Color } from "@/components/ui/tremor";
import type { TaskDetail, TaskItem } from "@/features/operations/tasks/api";

export const AI_TYPES =
  "process_data,generate_report,generate_section,review_section,update_disease_knowledge,agent_workflow";

export const AI_TASK_STATUS_OPTIONS = [
  "pending",
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
] as const;

export const AI_TASK_TYPE_OPTIONS = [
  "process_data",
  "generate_report",
  "generate_section",
  "review_section",
  "update_disease_knowledge",
  "agent_workflow",
] as const;

export const TASK_PAGE_SIZE = 100;

export const statusBadge: Record<string, Color> = {
  pending: "slate",
  queued: "blue",
  running: "amber",
  completed: "emerald",
  failed: "rose",
  cancelled: "slate",
  retrying: "amber",
};

export const statusColors: Record<string, string> = {
  pending: CHART_TOKENS.neutral,
  queued: CHART_TOKENS.info,
  running: CHART_TOKENS.warning,
  completed: CHART_TOKENS.success,
  failed: CHART_TOKENS.destructive,
  cancelled: CHART_TOKENS.neutral,
  retrying: "#f97316",
};

export interface TaskSummary {
  total: number;
  running: number;
  failed: number;
  avgProgress: number;
}

export interface ChartBucket {
  name: string;
  value: number;
}

export interface PaginationBounds {
  offset: number;
  totalPages: number;
  visibleStart: number;
  visibleEnd: number;
}

export function parseReportId(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === "string") {
    const num = Number(value);
    if (Number.isFinite(num)) {
      return num;
    }
  }

  return null;
}

export function parseReportUuid(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

export function buildReportUuidMap(
  reports?: ReadonlyArray<{ id: number; report_uuid: string }>,
): Map<number, string> {
  const mapping = new Map<number, string>();
  (reports ?? []).forEach((report) => {
    mapping.set(report.id, report.report_uuid);
  });
  return mapping;
}

export function resolveTaskReportUuid(
  taskDetail: Pick<TaskDetail, "report_id" | "output_data"> | null | undefined,
  reportUuidById: ReadonlyMap<number, string>,
): string | null {
  if (!taskDetail) return null;

  const outputData = taskDetail.output_data as Record<string, unknown> | null;
  const reportUuid = parseReportUuid(outputData?.report_uuid);
  if (reportUuid) {
    return reportUuid;
  }

  const reportId = parseReportId(taskDetail.report_id) ?? parseReportId(outputData?.report_id);
  if (!reportId) return null;

  return reportUuidById.get(reportId) ?? null;
}

export function computeTaskSummary(
  tasks: ReadonlyArray<Pick<TaskItem, "status" | "progress">>,
  totalCount: number,
): TaskSummary {
  const total = totalCount;
  const running = tasks.filter((task) => task.status === "running").length;
  const failed = tasks.filter((task) => task.status === "failed").length;
  const avgProgress =
    total > 0
      ? Math.round(tasks.reduce((acc, task) => acc + task.progress, 0) / Math.max(tasks.length, 1))
      : 0;
  return { total, running, failed, avgProgress };
}

export function buildStatusChartData(
  tasks: ReadonlyArray<Pick<TaskItem, "status">>,
): ChartBucket[] {
  const rows = new Map<string, number>();
  tasks.forEach((task) => {
    rows.set(task.status, (rows.get(task.status) ?? 0) + 1);
  });
  return Array.from(rows.entries())
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value);
}

export function buildTypeChartData(
  tasks: ReadonlyArray<Pick<TaskItem, "task_type">>,
): ChartBucket[] {
  const rows = new Map<string, number>();
  tasks.forEach((task) => {
    rows.set(task.task_type, (rows.get(task.task_type) ?? 0) + 1);
  });
  return Array.from(rows.entries())
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value);
}

export function computePaginationBounds(
  page: number,
  totalCount: number,
  visibleCount: number,
  pageSize = TASK_PAGE_SIZE,
): PaginationBounds {
  const offset = (page - 1) * pageSize;
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  const visibleStart = totalCount === 0 ? 0 : offset + 1;
  const visibleEnd = totalCount === 0 ? 0 : offset + visibleCount;
  return { offset, totalPages, visibleStart, visibleEnd };
}

export function canRetryTask(status: string): boolean {
  return status === "failed" || status === "cancelled";
}

export function canCancelTask(status: string): boolean {
  return status === "pending" || status === "queued" || status === "running";
}

export function buildTaskInteractionsHref(
  taskUuid: string,
  reportUuid?: string | null,
): string {
  return `/production/interactions?task=${encodeURIComponent(taskUuid)}${
    reportUuid ? `&uuid=${encodeURIComponent(reportUuid)}` : ""
  }`;
}
